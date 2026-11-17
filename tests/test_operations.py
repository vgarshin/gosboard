import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app.main import create_app
from app.metrics import Metrics
from test_app import FakeProviders, live_settings
from test_roles import roles


def test_metrics_bounded_and_permissions_and_private_content(roles):
    c,app,p,access,login,s=roles
    for role in ('user','editor'):
        login(role);assert c.get('/api/admin/operations').status_code==403
    login('user');cid=c.post('/api/chats').json()['id']
    text='PRIVATE CONTENT SHOULD NEVER APPEAR IN METRICS'
    assert c.post(f'/api/chats/{cid}/messages',json={'text':text,'request_id':str(uuid4())}).status_code==200
    login('admin');data=c.get('/api/admin/operations').json()
    assert text not in json.dumps(data)
    assert cid not in json.dumps(data)
    assert 'user@example.com' not in json.dumps(data)
    assert any(row['name']=='POST /api/chats/{cid}/messages' for row in data['series'])
    assert data['caches']['llm']['misses']==1
    assert data['active_paid_operations']==0
    m=Metrics()
    for i in range(1500):m.observe('operation','llm',i,'complete')
    row=m.snapshot()['series'][0]
    assert row['count']==1500 and row['sample_count']==1000
    assert row['p95_ms']==1449


@pytest.mark.parametrize('users',[5,10])
def test_concurrent_user_rehearsal(tmp_path,users):
    class Delayed(FakeProviders):
        def __init__(self):super().__init__();self.active=0;self.maximum=0;self.cancel_started=asyncio.Event()
        async def complete(self,messages,config):
            self.active+=1;self.maximum=max(self.maximum,self.active)
            try:
                if messages[-1]['text']=='CANCEL TURN':
                    self.cancel_started.set()
                    await asyncio.Event().wait()
                await asyncio.sleep(.035)
                if messages[-1]['text']=='PROVIDER FAILURE':raise HTTPException(502,'Simulated unavailable')
                self.llm_calls+=1
                return 'Synthetic answer',{}
            finally:self.active-=1
    async def scenario():
        s=live_settings(tmp_path);s.allowed.update(f'u{i}@example.com' for i in range(users));s.daily_limit=10000;s.monthly_limit=10000
        p=Delayed();app=create_app(s,p);store=app.state.store
        clients=[];chats=[]
        try:
            for i in range(users):
                uid=f'u{i}';store.user(uid,f'{uid}@example.com',uid);token=store.session(uid,3600)
                client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']})
                clients.append(client);chats.append((await client.post('/api/chats')).json()['id'])
            responses=await asyncio.gather(*(c.post(f'/api/chats/{cid}/messages',json={'text':f'Question {i}','request_id':str(uuid4())}) for i,(c,cid) in enumerate(zip(clients,chats))))
            assert all(r.status_code==200 for r in responses),[r.status_code for r in responses]
            assert p.maximum==2 and p.llm_calls==users
            assert all(len(store.messages(cid))==2 for cid in chats)
            assert (await clients[0].get(f'/api/chats/{chats[-1]}/messages')).status_code==404
            failed=await clients[0].post(f'/api/chats/{chats[0]}/messages',json={'text':'PROVIDER FAILURE','request_id':str(uuid4())})
            assert failed.status_code==502
            assert len(store.messages(chats[0]))==2 # no partial turn persisted
            recovered=await clients[0].post(f'/api/chats/{chats[0]}/messages',json={'text':'Recovered','request_id':str(uuid4())})
            assert recovered.status_code==200
            rid=str(uuid4());before=len(store.messages(chats[0]))
            pending=asyncio.create_task(clients[0].post(f'/api/chats/{chats[0]}/messages',json={'text':'CANCEL TURN','request_id':rid}))
            await asyncio.wait_for(p.cancel_started.wait(),2)
            assert (await clients[0].post(f'/api/requests/{rid}/cancel')).json()['cancel_requested']
            assert (await pending).status_code==409
            assert len(store.messages(chats[0]))==before
            assert not p.active
            # Exercise repeated STT audio and per-user cache isolation concurrently.
            async def transcribe(c):return await c.post('/api/stt',content=b'\x00\x00'*1600,headers={'Content-Type':'application/octet-stream','X-Request-ID':str(uuid4())})
            stt=await asyncio.gather(*(transcribe(c) for c in clients))
            assert all(r.status_code==200 for r in stt),[r.text for r in stt]
            repeat=await asyncio.gather(*(transcribe(c) for c in clients))
            assert all(r.json()['cached'] for r in repeat)
            assert p.stt_calls==users
            for c,cid in zip(clients,chats):
                mid=store.messages(cid)[-1]['id']
                first=await c.post(f'/api/chats/{cid}/messages/{mid}/speech',json={'request_id':str(uuid4())})
                second=await c.post(f'/api/chats/{cid}/messages/{mid}/speech',json={'request_id':str(uuid4())})
                assert first.status_code==second.status_code==200
                assert second.json()['cached']
            assert not any(row['state']=='pending' for row in store.usage())
            row=next(r for r in store.metrics.snapshot()['series'] if r['name']=='POST /api/chats/{cid}/messages')
            print(json.dumps({'simulated_users':users,'maximum_parallel_llm':p.maximum,'message_requests':row['count'],'message_mean_ms':row['mean_ms'],'message_p95_ms':row['p95_ms'],'outcomes':row['outcomes'],'stt_cache_repeats':users,'tts_cache_repeats':users}))
        finally:
            for c in clients:await c.aclose()
    asyncio.run(scenario())
