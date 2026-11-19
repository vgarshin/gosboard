import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.configuration import AppConfig, ChatOptions
from app.providers import Providers
from app.access import Access
from test_app import live_settings, CONFIG
from test_rag import RagProvider, FakeObjects, upload, wait_document


@pytest.fixture
def roles(tmp_path):
    settings=live_settings(tmp_path)
    settings.allowed.add('editor@example.com')
    settings.rag_enabled=True; settings.s3_key='fake';settings.s3_secret='fake'
    provider=RagProvider()
    app=create_app(settings,provider); app.state.rag.objects=FakeObjects()
    access=Access(app.state.store,settings)
    from app.access import AccountInput
    access.save(AccountInput(email='editor@example.com',role='editor'),'bootstrap')
    for uid in ('admin','editor','user'):
        app.state.store.user(uid,uid+'@example.com',uid)
    with TestClient(app,base_url=settings.origin) as client:
        client.headers['Origin']=settings.origin
        def login(uid):
            client.cookies.clear();token=app.state.store.session(uid,3600)
            client.cookies.set('gosboard_session',token)
            client.headers['X-CSRF-Token']=app.state.store.auth(token)['csrf']
        yield client,app,provider,access,login,settings


def test_roles_enforced_and_editor_metadata_only(roles):
    c,app,p,access,login,s=roles
    login('user')
    cid=c.post('/api/chats').json()['id']
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'PRIVATE MESSAGE BODY','request_id':str(uuid4())}).status_code==200
    for route in ('/api/admin/users','/api/admin/audit','/api/editor/chats'):
        assert c.get(route).status_code==403
    assert c.post('/api/knowledge-bases',json={'name':'denied'}).status_code==403
    assert c.post('/api/chats',json={'voice':'filipp'}).status_code==422
    login('editor')
    assert c.get('/api/editor/chats').json()==[]
    assert c.post('/api/knowledge-bases',json={'name':'denied'}).status_code==403
    login('admin')
    assert c.put('/api/management/spaces/initial/assignments',json={
        'email':'editor@example.com','assigned':True,'management':True,
    }).status_code==200
    login('editor')
    listing=c.get('/api/editor/chats').json()
    assert listing[0]['title'].startswith('PRIVATE')
    assert set(listing[0])=={'id','title','user_id','email','updated','settings','space_id'}
    assert c.get(f'/api/chats/{cid}/messages').status_code==404
    assert c.delete(f'/api/chats/{cid}').status_code==404
    assert c.get('/api/admin/users').status_code==403
    bid=c.post('/api/knowledge-bases',json={'name':'Shared editorial base'}).json()['id']
    did=upload(c,bid)['id'];wait_document(c,bid,did)
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'top_k':2}).status_code==403
    login('user')
    assert c.get(f'/api/knowledge-bases/{bid}/documents').status_code==403
    assert c.post(f'/api/chats/{cid}/messages/{app.state.store.messages(cid)[-1]["id"]}/speech',json={'request_id':str(uuid4())}).status_code==200
    login('admin')
    assert c.get(f'/api/chats/{cid}/messages').status_code==404
    events=c.get('/api/admin/audit').json()
    assert any(e['action']=='space.assignment' for e in events)
    assert 'PRIVATE MESSAGE BODY' not in json.dumps(events)


def test_user_management_revokes_sessions_and_prevents_lockout(roles):
    c,app,p,access,login,s=roles
    login('admin')
    assert c.put('/api/admin/users',json={'email':' New@Example.com ','role':'editor'}).status_code==200
    assert access.account('new@example.com')['role']=='editor'
    assert c.put('/api/admin/users',json={'email':'bad','role':'user'}).status_code==422
    assert c.put('/api/admin/users',json={'email':'admin@example.com','role':'user'}).status_code==409
    assert c.put('/api/admin/users',json={'email':'pending-admin@example.com','role':'admin'}).status_code==200
    assert c.put('/api/admin/users',json={'email':'admin@example.com','role':'user'}).status_code==409
    token=app.state.store.session('user',3600)
    assert c.put('/api/admin/users',json={'email':'user@example.com','role':'editor'}).status_code==200
    assert app.state.store.auth(token) is None
    login('user');assert c.get('/api/me').json()['role']=='editor'
    login('admin')
    assert c.put('/api/admin/users',json={'email':'user@example.com','role':'editor','enabled':False}).status_code==200
    login('user');assert c.get('/api/me').status_code==403
    # Reconstruct the access migration: env cannot resurrect disabled users or demoted roles.
    again=Access(app.state.store,s)
    assert not again.account('user@example.com')['enabled']
    assert again.account('user@example.com')['role']=='editor'


def test_admin_chunk_policy_requires_explicit_reindex_and_invalidates(roles):
    c,app,p,access,login,s=roles
    login('admin');bid=c.post('/api/knowledge-bases',json={'name':'Tuning'}).json()['id']
    did=upload(c,bid)['id'];wait_document(c,bid,did)
    revision=app.state.rag.repo.base(bid)['revision']
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'top_k':1}).status_code==200
    assert app.state.rag.repo.base(bid)['revision']>revision
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'chunk_size':500,'chunk_overlap':100,'top_k':2}).status_code==409
    assert app.state.rag.repo.base(bid)['chunk_size']==1200
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'chunk_size':500,'chunk_overlap':100,'top_k':2,'reindex':True}).status_code==200
    wait_document(c,bid,did)
    _,index=app.state.rag.repo.index_snapshot(bid)
    assert json.loads(index['spec'])['chunk_spec']=='page-recursive-500-100-v1'
    assert app.state.rag.repo.base(bid)['top_k']==2
    for policy in ({'chunk_size':20},{'top_k':100},{'chunk_size':400,'chunk_overlap':400}):
        assert c.put(f'/api/knowledge-bases/{bid}/policy',json=policy).status_code==422


def test_reasoning_capability_and_chat_completions_request(tmp_path):
    config=AppConfig.model_validate_json(CONFIG.read_text())
    with pytest.raises(ValueError,match='не поддерживает'):
        config.resolve(ChatOptions(reasoning_effort='enabled'))
    config=config.model_copy(update={'reasoning_supported':True})
    effective=config.resolve(ChatOptions(reasoning_effort='enabled',voice='filipp'))
    assert effective.reasoning_effort=='enabled' and effective.voice=='filipp'
    async def scenario():
        settings=live_settings(tmp_path);provider=Providers(settings)
        recorded=[]
        async def request(method,url,**kwargs):
            recorded.append(kwargs['json'])
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]})
        provider.request=request
        try:await provider.complete([{'role':'user','text':'test'}],effective)
        finally:await provider.close()
        assert recorded[0]['reasoning_effort']=='medium'
    asyncio.run(scenario())


def test_source_file_limit_and_chunk_limit_are_independent(roles):
    c,app,p,access,login,s=roles
    login('admin');bid=c.post('/api/knowledge-bases',json={'name':'Several files'}).json()['id']
    for number in range(3):
        content=('Education reform navigation school mathematics. '+str(number)+' ')*20
        did=upload(c,bid,content.encode(),f'education-{number}.txt')['id'];wait_document(c,bid,did)
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'top_k':5,'top_k_documents':1}).status_code==200
    uid='admin'
    sources,_=asyncio.run(app.state.rag.retrieve(bid,'Education reform navigation school',uid,5500))
    assert sources and len({source['document_id'] for source in sources})==1
    assert c.put(f'/api/knowledge-bases/{bid}/policy',json={'top_k':1,'top_k_documents':3}).status_code==200
    sources,_=asyncio.run(app.state.rag.retrieve(bid,'Education reform navigation school',uid,5500))
    assert len(sources)==1


def test_disable_user_cancels_inflight_generation(tmp_path):
    class Slow(RagProvider):
        def __init__(self):
            super().__init__();self.started=asyncio.Event()
        async def complete(self,messages,config):
            self.started.set();await asyncio.sleep(60)
    async def scenario():
        s=live_settings(tmp_path);p=Slow();app=create_app(s,p);store=app.state.store
        clients=[]
        for uid in ('admin','user'):
            store.user(uid,uid+'@example.com',uid);token=store.session(uid,3600)
            clients.append(httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,
                cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']}))
        admin,user=clients
        try:
            cid=(await user.post('/api/chats')).json()['id']
            pending=asyncio.create_task(user.post(f'/api/chats/{cid}/messages',json={'text':'Test','request_id':str(uuid4())}))
            await asyncio.wait_for(p.started.wait(),2)
            result=await admin.put('/api/admin/users',json={'email':'user@example.com','role':'user','enabled':False})
            assert result.status_code==200
            assert (await pending).status_code==409
            assert (await user.get('/api/me')).status_code==401
            assert store.messages(cid)==[]
            assert store.usage()[0]['state']=='uncertain'
        finally:
            for client in clients:await client.aclose()
            await p.close()
    asyncio.run(scenario())
