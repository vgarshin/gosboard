from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from app.store import Store
from test_roles import roles


def test_admin_budget_permissions_persistence_and_immediate_enforcement(roles):
    c,app,p,access,login,s=roles
    for role in ('user','editor'):
        login(role)
        assert c.get('/api/admin/budget').status_code==403
        assert c.put('/api/admin/budget',json={'expected_revision':1,'daily_limit_rub':10,'monthly_limit_rub':20}).status_code==403
    login('admin');initial=c.get('/api/admin/budget').json()
    rid=app.state.store.reserve('user','tts',20,999999,999999)
    app.state.store.settle(rid,'uncertain')
    body={'expected_revision':initial['revision'],'daily_limit_rub':21,'monthly_limit_rub':30}
    saved=c.put('/api/admin/budget',json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['day']['used_rub']==20
    assert saved.json()['day']['remaining_rub']==1
    with pytest.raises(HTTPException) as error:app.state.store.reserve('user','llm',10,999999,999999)
    assert error.value.status_code==429
    assert c.put('/api/admin/budget',json=body).status_code==409
    for bad in (0,-1,'NaN','Infinity'):
        assert c.put('/api/admin/budget',json={**body,'daily_limit_rub':bad}).status_code==422
    body.update(expected_revision=saved.json()['revision'],daily_limit_rub=50,monthly_limit_rub=50)
    assert c.put('/api/admin/budget',json=body).status_code==200
    app.state.store.reserve('user','llm',10,1,1) # persisted policy overrides old environment
    restored=Store(s.data_dir);restored.initialize_budget(1,1)
    assert restored.budget()['day']['limit_rub']==50
    assert restored.budget()['month']['used_rub']==30
    assert any(e['action']=='budget.update' for e in c.get('/api/admin/audit').json())


def test_budget_atomic_concurrent_reservations_and_periods(tmp_path):
    store=Store(tmp_path);store.initialize_budget(10,10)
    def reserve(_):
        try:store.reserve('u','llm',10,999,999);return True
        except HTTPException:return False
    with ThreadPoolExecutor(max_workers=4) as pool:assert sum(pool.map(reserve,range(4)))==1
    with store.connection() as db:db.execute('INSERT INTO usage VALUES (?,?,?,?,?,?)',('old','u','llm',99,'complete',0))
    view=store.budget()
    assert view['day']['used_rub']==10
    assert view['month']['used_rub']==10
    assert view['day']['remaining_rub']==0


def test_monthly_limit_blocks_even_when_today_is_empty(tmp_path):
    from datetime import datetime, timezone
    store=Store(tmp_path);store.initialize_budget(100,10)
    now=datetime(2026,11,14,12,tzinfo=timezone.utc)
    import unittest.mock
    with store.connection() as db:
        db.execute('INSERT INTO usage VALUES (?,?,?,?,?,?)',('earlier','u','llm',10,'complete',datetime(2026,11,13,tzinfo=timezone.utc).timestamp()))
    with unittest.mock.patch('app.store.datetime') as clock:
        clock.now.return_value=now
        assert store.budget()['day']['used_rub']==0
        assert store.budget()['month']['used_rub']==10
        with pytest.raises(HTTPException):store.reserve('u','stt',5,100,100)
