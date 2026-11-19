import asyncio
import base64
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.web_search import parse_search_response,safe_web_url,WEB_UNSUPPORTED
from app.rag import validate_rag_answer
from app.settings import Settings
from test_app import CONFIG,live_settings
from test_rag import RagProvider,FakeObjects,ready_base,ask
from space_helpers import configure_space, configure_space_async, stored_answer

class SearchProvider(RagProvider):
    def __init__(self):
        super().__init__();self.searches=0;self.fail_search=False;self.empty=False;self.last_query=None
    async def search(self,query):
        self.searches+=1;self.last_query=query
        if self.fail_search:raise HTTPException(502,'Search unavailable')
        if self.empty:return []
        return await super().search(query)

@pytest.fixture
def web_setup(tmp_path):
    s=Settings(environment='development',origin='http://localhost',demo=True,rag_enabled=True,web_enabled=True,data_dir=tmp_path,config_path=CONFIG)
    p=SearchProvider();app=create_app(s,p);app.state.rag.objects=FakeObjects()
    with TestClient(app,base_url=s.origin) as c:
        c.headers['Origin']=s.origin;c.post('/api/auth/demo');c.headers['X-CSRF-Token']=c.get('/api/me').json()['csrf']
        yield c,app,p

def new_chat(c,rag=False,web=False,bid=None):
    configure_space(c, base_ids=[bid] if rag else [], web_search_enabled=web)
    response=c.post('/api/chats')
    assert response.status_code==200,response.text
    return response.json()['id']

@pytest.mark.parametrize('rag,web',[(False,False),(True,False),(False,True),(True,True)])
def test_four_source_modes_and_citations(web_setup,rag,web):
    c,app,p=web_setup;bid=ready_base(c)[0] if rag else None
    embeddings=p.embeddings;cid=new_chat(c,rag,web,bid)
    result=ask(c,cid)
    assert result.status_code==200,result.text
    assert p.searches==int(web)
    if not rag:assert p.embeddings==embeddings
    metadata=stored_answer(c,cid)['metadata'];citations=metadata.get('citations',[])
    assert {source['type'] for source in citations}==({'rag'} if rag else set())|({'web'} if web else set())
    if web:
        source=next(s for s in citations if s['type']=='web')
        assert source['url'].startswith('https://') and source['id'].startswith('W')
        preview=c.get('/api/web-sources/'+source['source_id'])
        assert preview.status_code==200 and preview.json()['excerpt']==source['excerpt']
        assert preview.json()['content_kind']=='search_snippet'
    assert metadata['retrieval']['web_search_enabled']==web


def test_search_and_answer_cache_freshness_private_source_history(web_setup):
    c,app,p=web_setup
    first=new_chat(c,web=True);result=ask(c,first);assert result.status_code==200
    source=stored_answer(c,first)['metadata']['citations'][0];sid=source['source_id']
    second=new_chat(c,web=True);cached=ask(c,second)
    assert cached.json()['metadata']['cached'] and stored_answer(c,second)['metadata']['retrieval']['web']['cached']
    assert p.searches==p.completions==1
    with app.state.store.connection() as db:db.execute("UPDATE cache SET expires=0 WHERE kind='web_search'")
    third=new_chat(c,web=True);fresh=ask(c,third)
    assert fresh.status_code==200
    assert p.searches==p.completions==2
    assert stored_answer(c,third)['metadata']['citations'][0]['source_id']!=sid
    assert c.get('/api/web-sources/'+sid).json()['fetched_at']==source['fetched_at']
    token=c.cookies.get('gosboard_session')
    app.state.store.user('other','other@localhost','Other')
    other=app.state.store.session('other',3600);c.cookies.clear();c.cookies.set('gosboard_session',other)
    assert c.get('/api/web-sources/'+sid).status_code==403
    c.cookies.clear();c.cookies.set('gosboard_session',token)
    c.headers['X-CSRF-Token']=c.get('/api/me').json()['csrf']
    c.delete('/api/chats/'+first);c.delete('/api/chats/'+second)
    assert c.get('/api/web-sources/'+sid).status_code==404


def test_search_failure_does_not_save_or_silently_fallback(web_setup):
    c,app,p=web_setup;bid=ready_base(c)[0];cid=new_chat(c,True,True,bid)
    p.fail_search=True
    assert ask(c,cid).status_code==502
    assert not app.state.store.messages(cid) and p.completions==0
    cid = new_chat(c, rag=True, bid=bid)
    assert ask(c,cid).status_code==200
    assert p.searches==1


def test_empty_search_no_model_and_query_bound(web_setup):
    c,app,p=web_setup;p.empty=True;cid=new_chat(c,web=True)
    result=ask(c,cid,'x '*1000)
    assert result.status_code==200 and stored_answer(c,cid)['metadata']['model_called'] is False
    assert len(p.last_query)<=400 and p.completions==0
    assert app.state.store.messages(cid)[-1]['text']==WEB_UNSUPPORTED


def test_mixed_unknown_citations_and_invented_urls_rejected():
    sources=[{'id':'D1','type':'rag'},{'id':'W1','type':'web','url':'https://example.com/source'}]
    answer,citations,supported=validate_rag_answer(json.dumps({'supported':True,'answer':'Evidence [D1, W1]','used_sources':['D1','W1']}),sources)
    assert answer=='Evidence [D1] [W1]' and supported and len(citations)==2
    for text in ['Fake [D1] [W99]','Fake [D1] [X1]','Fake [D1] https://invented.example/source']:
        with pytest.raises(HTTPException):validate_rag_answer(json.dumps({'supported':True,'answer':text,'used_sources':['D1']}),sources)


def xml_payload(xml):return {'rawData':base64.b64encode(xml.encode()).decode()}

def test_parser_snippets_urls_and_xml_limits():
    results=parse_search_response(xml_payload('<yandexsearch><response><results><group><doc><url>https://example.com/path</url><title>A <hlword>title</hlword></title><passages><passage>Some <hlword>useful</hlword> evidence in this source snippet.</passage></passages></doc></group></results></response></yandexsearch>'))
    assert results[0]['title']=='A title' and 'useful evidence' in results[0]['excerpt']
    for url in ['javascript:alert(1)','http://127.0.0.1/a','http://169.254.169.254/','https://user:secret@example.com/','http://metadata.internal/','https://example.com:8000/','https://example.com/\nsecret']:
        assert not safe_web_url(url)
    for payload in [{'rawData':'not-base64'},xml_payload('<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>'),xml_payload('<error>Failure</error>'),xml_payload('<invalid')]:
        with pytest.raises(HTTPException):parse_search_response(payload)


def test_live_budget_search_cache_and_disabled_costs(tmp_path):
    s=live_settings(tmp_path);s.web_enabled=True;s.search_key='fake-search';s.search_folder='test'
    p=SearchProvider();app=create_app(s,p)
    app.state.store.user('admin','admin@example.com','Admin');token=app.state.store.session('admin',3600)
    with TestClient(app,base_url=s.origin) as c:
        c.cookies.set('gosboard_session',token);c.headers.update({'Origin':s.origin,'X-CSRF-Token':app.state.store.auth(token)['csrf']})
        first=new_chat(c,web=True);assert ask(c,first).status_code==200
        second=new_chat(c,web=True);assert ask(c,second).json()['metadata']['cached']
        assert p.searches==1
        rows=app.state.store.usage()
        assert sum(row['kind']=='web-search' for row in rows)==1
        normal=new_chat(c);assert ask(c,normal).status_code==200
        assert p.searches==1


def test_cancel_while_searching_saves_no_partial_turn(tmp_path):
    class Waiting(SearchProvider):
        def __init__(self):super().__init__();self.started=asyncio.Event()
        async def search(self,query):self.started.set();await asyncio.sleep(60)
    async def scenario():
        s=live_settings(tmp_path);s.web_enabled=True;s.search_key='fake';s.search_folder='test'
        p=Waiting();app=create_app(s,p);store=app.state.store
        store.user('admin','admin@example.com','Admin');token=store.session('admin',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            await configure_space_async(c, web_search_enabled=True)
            cid=(await c.post('/api/chats')).json()['id'];rid=str(uuid4())
            pending=asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'Test search','request_id':rid}))
            await asyncio.wait_for(p.started.wait(),2)
            assert (await c.post('/api/requests/'+rid+'/cancel')).json()['cancel_requested']
            assert (await pending).status_code==409
            assert not store.messages(cid) and p.completions==0
            assert store.usage()[0]['state']=='uncertain'
        await p.close()
    asyncio.run(scenario())


def test_real_provider_contract_and_private_key_scope(tmp_path):
    from app.providers import Providers
    async def scenario():
        s=live_settings(tmp_path);s.web_enabled=True;s.search_key='dedicated-search-key';s.search_folder='search-folder'
        p=Providers(s);await p.client.aclose();requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json=xml_payload('<yandexsearch><doc><url>https://example.org</url><title>Result</title><passages><passage>Evidence from the search API result.</passage></passages></doc></yandexsearch>'))
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            results=await p.search('Education reform')
            assert results[0]['url']=='https://example.org'
            assert len(requests)==1 and str(requests[0].url)=='https://searchapi.api.cloud.yandex.net/v2/web/search'
            assert requests[0].headers['Authorization']=='Api-Key dedicated-search-key'
            assert json.loads(requests[0].content)['folderId']=='search-folder'
        finally:await p.close()
    asyncio.run(scenario())


def test_cancel_after_search_before_model_slot(tmp_path):
    class Waiting(SearchProvider):
        def __init__(self):super().__init__();self.started=asyncio.Event();self.allow=asyncio.Event()
        async def complete(self,messages,config):
            self.completions+=1;self.started.set();await self.allow.wait();return 'ordinary response',{}
    async def scenario():
        s=live_settings(tmp_path);s.web_enabled=True;s.search_key='fake';s.search_folder='test'
        p=Waiting();app=create_app(s,p);store=app.state.store
        store.user('admin','admin@example.com','Admin');token=store.session('admin',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            ids=[(await c.post('/api/chats')).json()['id'] for _ in range(2)]
            active=[asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'Hold model slot','request_id':str(uuid4())})) for cid in ids]
            await asyncio.wait_for(p.started.wait(),2)
            while p.completions<2:await asyncio.sleep(.01)
            await configure_space_async(c, web_search_enabled=True)
            cid=(await c.post('/api/chats')).json()['id'];rid=str(uuid4())
            pending=asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'Search before queued generation','request_id':rid}))
            while p.searches<1:await asyncio.sleep(.01)
            await asyncio.sleep(.03)
            assert (await c.post('/api/requests/'+rid+'/cancel')).json()['cancel_requested']
            assert (await pending).status_code==409
            assert not store.messages(cid) and p.completions==2
            p.allow.set();await asyncio.gather(*active)
        await p.close()
    asyncio.run(scenario())


def test_user_role_can_search_but_admin_cannot_read_private_web_excerpt(tmp_path):
    s=live_settings(tmp_path);s.web_enabled=True;s.search_key='fake';s.search_folder='test'
    p=SearchProvider();app=create_app(s,p);store=app.state.store
    for uid in ('admin','user'):store.user(uid,uid+'@example.com',uid)
    token=store.session('user',3600)
    with TestClient(app,base_url=s.origin) as c:
        admin_token = store.session('admin',3600)
        c.cookies.set('gosboard_session',admin_token)
        c.headers.update({'Origin':s.origin,'X-CSRF-Token':store.auth(admin_token)['csrf']})
        configure_space(c, web_search_enabled=True)
        c.cookies.clear()
        c.cookies.set('gosboard_session',token)
        c.headers['X-CSRF-Token']=store.auth(token)['csrf']
        cid=c.post('/api/chats').json()['id'];response=ask(c,cid);assert response.status_code==200
        source=store.messages(cid)[-1]['metadata']['citations'][0]
        assert c.get('/api/web-sources/'+source['source_id']).status_code==403
        c.cookies.clear();c.cookies.set('gosboard_session',store.session('admin',3600))
        assert c.get('/api/web-sources/'+source['source_id']).status_code==404


def test_offline_restore_preserves_web_citations(web_setup,tmp_path):
    import importlib.util
    from pathlib import Path
    c,app,p=web_setup;cid=new_chat(c,web=True);assert ask(c,cid).status_code==200
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    def module(name):
        spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py'));value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
    target=tmp_path/'web-snapshot.sqlite3';module('backup').snapshot(app.state.store.path,target)
    result=module('verify_restore').verify(target,CONFIG)
    assert result['web_citations_checked']>0
    assert result['network']=='not used'


def test_switching_sources_in_existing_plain_conversation(web_setup):
    c,app,p=web_setup
    bid=ready_base(c)[0];cid=new_chat(c)
    assert ask(c,cid).status_code==200
    original=p.complete;seen=[]
    async def capture(messages,config):
        seen.append(messages)
        return await original(messages,config)
    p.complete=capture
    app.state.store.add_turn(cid,'Remember the topic [D77]','Earlier reply [D99] [W99] [D1, W1]',{})
    for rag,web in [(False,True),(True,False),(True,True),(False,False)]:
        cid = new_chat(c, rag, web, bid)
        for idx in range(2):
            app.state.store.add_turn(cid,'Remember the topic [D77]','Earlier reply [D99] [W99] [D1, W1]',{})
        result=ask(c,cid);assert result.status_code==200,result.text
        assert stored_answer(c,cid)['metadata']['context']['history_messages_included']>=4
        if rag or web:
            assert any(m['text']=='Remember the topic [D77]' for m in seen[-1])
            assert all('[D99]' not in m['text'] and '[W99]' not in m['text'] and '[D1, W1]' not in m['text'] for m in seen[-1] if m['role']=='assistant')
            assert 'Старые ссылки и ID относятся к прошлым ответам' in seen[-1][0]['text']
            expected=({'rag'} if rag else set())|({'web'} if web else set())
            assert {s['type'] for s in stored_answer(c,cid)['metadata']['citations']}==expected
            if web and not rag:assert '"used_sources": ["W1"]' in seen[-1][0]['text']
        else:assert '[RAG_RESPONSE_SCHEMA_V1]' not in seen[-1][0]['text']
    assert any(m['text']=='Earlier reply [D99] [W99] [D1, W1]' for m in app.state.store.messages(cid))
