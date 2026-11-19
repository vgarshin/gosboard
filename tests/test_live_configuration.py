import asyncio
import json
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings
from test_app import CONFIG, FakeProviders, live_settings
from test_roles import roles


def test_live_edit_persists_and_file_is_bootstrap_only(tmp_path):
    source=tmp_path/'bootstrap.json';source.write_text(CONFIG.read_text())
    settings=Settings(environment='development',origin='http://localhost',demo=True,data_dir=tmp_path/'data',config_path=source)
    provider=FakeProviders();app=create_app(settings,provider)
    with TestClient(app,base_url=settings.origin) as c:
        c.headers['Origin']=settings.origin;c.post('/api/auth/demo');c.headers['X-CSRF-Token']=c.get('/api/me').json()['csrf']
        original=c.get('/api/editor/config').json();cid=c.post('/api/chats').json()['id']
        def send():return c.post(f'/api/chats/{cid}/messages',json={'text':'Same question','request_id':str(uuid4())})
        assert send().status_code==200
        old_message=app.state.store.messages(cid)[-1]
        update=json.loads(json.dumps(original));update['expected_revision']=update.pop('revision')
        update['config'].update(system_prompt='New live instructions',voice='filipp',temperature=0.2)
        saved=c.put('/api/editor/config',json=update)
        assert saved.status_code==200,saved.text
        assert saved.json()['revision']==2
        assert c.get('/api/admin/config').json()['config']['voice']=='filipp'
        assert send().status_code==200
        assert provider.last_config.system_prompt==original['config']['system_prompt']
        new_chat=c.post('/api/chats').json()['id']
        assert c.post(f'/api/chats/{new_chat}/messages',json={'text':'New chat','request_id':str(uuid4())}).status_code==200
        assert provider.last_config.system_prompt==original['config']['system_prompt']
        assert provider.last_config.temperature==0.2
        assert provider.llm_calls==3
        assert app.state.store.messages(cid)[1]==old_message
        assert app.state.store.messages(new_chat)[-1]['metadata']['config_version']=='live-2'
        assert c.put('/api/editor/config',json=update).status_code==409
        assert len(c.get('/api/editor/config/versions').json())==2
        assert c.get('/api/editor/config/versions/1').json()['config']==original['config']
        assert c.get('/api/editor/config/versions/999').status_code==404
    # The existing file is no longer read or required once the database has a configuration.
    source.write_text('invalid file after migration')
    restored=create_app(settings,FakeProviders())
    assert restored.state.live_config.current().system_prompt=='New live instructions'
    assert restored.state.live_config.read()['revision']==2


def test_editor_manages_assigned_policy_and_cannot_change_global_catalog(roles):
    c,app,p,access,login,s=roles
    login('user')
    cid=c.post('/api/chats').json()['id']
    assert c.post('/api/chats',json={'prompt_id':'peter-council'}).status_code==422
    assert c.get('/api/editor/config').status_code==403
    login('editor')
    assert c.get('/api/editor/config').status_code==403
    assert c.get('/api/management/spaces/initial/policy').status_code==404
    login('admin')
    assert c.put('/api/management/spaces/initial/assignments',json={
        'email':'editor@example.com','assigned':True,'management':True,
    }).status_code==200
    login('editor')
    route='/api/management/spaces/initial/policy'
    policy=c.get(route).json()['policy']
    policy['prompt']='New preset instruction'
    assert c.put(route,json={'revision':1,'policy':policy}).status_code==200
    assert c.get(f'/api/chats/{cid}/messages').status_code==404
    assert c.put('/api/editor/config',json={}).status_code==403
    assert app.state.live_config.read()['revision']==1


def test_explicit_add_user_rejects_duplicate_without_changing_account(roles):
    c,app,p,access,login,s=roles
    login('admin')
    assert c.post('/api/admin/users',json={'email':'new@example.com','role':'user'}).status_code==200
    assert c.post('/api/admin/users',json={'email':'new@example.com','role':'admin'}).status_code==409
    assert access.account('new@example.com')['role']=='user'
    login('editor');assert c.post('/api/admin/users',json={'email':'forbidden@example.com'}).status_code==403


def test_inflight_response_keeps_original_configuration(tmp_path):
    class Waiting(FakeProviders):
        def __init__(self):super().__init__();self.started=asyncio.Event();self.finish=asyncio.Event();self.seen=None
        async def complete(self,messages,config):
            self.seen=config;self.started.set();await self.finish.wait();return 'Completed response',{}
    async def scenario():
        s=live_settings(tmp_path);p=Waiting();app=create_app(s,p);store=app.state.store
        store.user('admin','admin@example.com','Admin');token=store.session('admin',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            cid=(await c.post('/api/chats')).json()['id']
            old=(await c.get('/api/editor/config')).json()
            pending=asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'Test','request_id':str(uuid4())}))
            await asyncio.wait_for(p.started.wait(),2)
            new=json.loads(json.dumps(old['config']));new['system_prompt']='Changed while generating';new['model_label']='Changed model label'
            result=await c.put('/api/editor/config',json={'expected_revision':old['revision'],'config':new});assert result.status_code==200
            p.finish.set();answer=await pending
            assert answer.status_code==200
            assert store.messages(cid)[-1]['metadata']['config_version']==old['config']['version']
            assert store.messages(cid)[-1]['metadata']['model_label']==old['config']['model_label']
            assert p.seen.system_prompt==old['config']['system_prompt']
        await p.close()
    asyncio.run(scenario())


def test_restore_preserves_live_revision_with_invalid_bootstrap(roles, tmp_path):
    import importlib.util
    c,app,p,access,login,s=roles
    login('admin');live=c.get('/api/editor/config').json();live['config']['system_prompt']='Restored runtime instructions'
    assert c.put('/api/editor/config',json={'expected_revision':live['revision'],'config':live['config']}).status_code==200
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    def module(name):
        spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py'));result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
    target=tmp_path/'live-snapshot.sqlite3';module('backup').snapshot(app.state.store.path,target)
    invalid=tmp_path/'invalid-bootstrap.json';invalid.write_text('not json')
    result=module('verify_restore').verify(target,invalid)
    assert result['runtime_configuration']=='preserved'
    assert result['access_roles']=='preserved'


def test_mode_changes_preserve_saved_spatial_model_and_voice(roles):
    from space_helpers import configure_space
    c,app,p,access,login,s=roles
    login('admin')
    configure_space(c,model_id='alice-flash',voice='filipp')
    login('user')
    cid=c.post('/api/chats').json()['id']
    login('admin')
    configure_space(c,model_id='default',voice='marina')
    assert c.patch(f'/api/chats/{cid}/mode',json={'communication_mode':'voice'}).status_code==404
    login('user')
    result=c.patch(f'/api/chats/{cid}/mode',json={'communication_mode':'voice'})
    assert result.status_code==200
    assert result.json()['settings']['communication_mode']=='voice'
    assert app.state.store.chat(cid,'user')['settings']['model_id']=='alice-flash'
    assert app.state.store.chat(cid,'user')['settings']['voice']=='filipp'
