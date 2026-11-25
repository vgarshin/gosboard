from space_helpers import stored_answer, configure_space, configure_space_async
import asyncio
import json
from uuid import uuid4
import pytest
from app.retrieval_query import RetrievalQuery,QUERY_CONTRACT
from app.configuration import AppConfig
from app.store import Store
from test_app import CONFIG
from test_web_search import web_setup,new_chat
from test_rag import ready_base


@pytest.mark.parametrize('rag,web',[(False,True),(True,False),(True,True)])
def test_trump_followup_uses_context_for_both_retrievers(web_setup,rag,web):
    c,app,p=web_setup;bid=ready_base(c)[0] if rag else None
    cid=new_chat(c,rag,web,bid);app.state.store.add_turn(cid,'Кто такой Трамп?','Дональд Трамп — политик.',{'citations':[{'id':'W1'}]})
    original=p.complete;rewrites=[];queries=[]
    async def complete(messages,config):
        if QUERY_CONTRACT in messages[0]['text']:
            payload=json.loads(messages[-1]['text']);rewrites.append(payload)
            assert payload['question']=='Кто его жена?'
            assert payload['history'][-1]['sourced'] and 'Трамп' in payload['history'][-1]['text']
            return json.dumps({'route':'search','query':'Кто жена Дональда Трампа?'}),{}
        return await original(messages,config)
    p.complete=complete
    retrieve=app.state.rag.retrieve
    async def capture(base,question,*args,**kwargs):
        queries.append(question);return await retrieve(base,question,*args,**kwargs)
    app.state.rag.retrieve=capture
    result=c.post(f'/api/chats/{cid}/messages',json={'text':'Кто его жена?','request_id':str(uuid4())})
    assert result.status_code==200,result.text
    assert len(rewrites)==1
    if web:assert p.last_query=='Кто жена Дональда Трампа?'
    if rag:assert queries==['Кто жена Дональда Трампа?']
    assert stored_answer(c,cid)['metadata']['retrieval']['context_query']['contextual']
    assert app.state.store.messages(cid)[-2]['text']=='Кто его жена?'


def test_history_off_routes_without_sending_previous_turns(web_setup):
    c,app,p=web_setup
    configure_space(c, history_messages=0)
    cid=new_chat(c,web=True)
    app.state.store.add_turn(cid,'Who is Trump?','Donald Trump is a politician.',{})
    result=c.post(f'/api/chats/{cid}/messages',json={'text':'Who is his wife?','request_id':str(uuid4())})
    assert result.status_code==200,result.text
    assert p.searches==p.completions==p.routing_calls==1
    assert p.last_query=='Who is his wife?'
    assert not stored_answer(c,cid)['metadata']['retrieval']['context_query']['history_aware']
    assert len(app.state.store.messages(cid))==4


def test_query_resolution_cache_is_private_and_does_not_export_full_history(tmp_path):
    async def scenario():
        store=Store(tmp_path);calls=[]
        class Provider:
            async def complete(self,messages,config):
                calls.append(messages)
                payload = json.loads(messages[-1]['text'])
                question = payload['question']
                if payload['history'] and question == 'Who is his wife?':
                    question = 'Who is Donald Trump wife?'
                return json.dumps({'route': 'search', 'query': question}), {}
        async def charge(uid,kind,amount,operation,rid):return await operation()
        resolver=RetrievalQuery(store,Provider(),charge);config=AppConfig.model_validate_json(CONFIG.read_text())
        history=[{'role':'user','text':'Who is Trump?'},{'role':'assistant','text':'Donald Trump https://private.example/account [W99]'}]
        first,info=await resolver.resolve('u','chat','Who is his wife?',history,config,'r',10,rag=True,web=False)
        assert first['query']=='Who is Donald Trump wife?' and not info['cached']
        assert 'https://private.example' not in calls[0][-1]['text'] and '[W99]' not in calls[0][-1]['text']
        _,info=await resolver.resolve('u','chat','Who is his wife?',history,config,'r2',10,rag=True,web=False)
        assert info['cached'] and len(calls)==1
        await resolver.resolve('other','chat','Who is his wife?',history,config,'r3',10,rag=True,web=False)
        assert len(calls)==2
        await resolver.resolve('u','other-chat','Who is his wife?',history,config,'r4',10,rag=True,web=False)
        assert len(calls)==3
        query,info=await resolver.resolve('u','chat','Who is Donald Trump?',history,config,'r5',10,rag=True,web=False)
        assert query['query']=='Who is Donald Trump?' and info['history_aware'] and not info['rewritten'] and len(calls)==4
        query,info=await resolver.resolve('u','new-chat','Who is his wife?',[],config,'r6',10,rag=True,web=False)
        assert query['query']=='Who is his wife?' and not info['history_aware'] and len(calls)==5
    asyncio.run(scenario())


@pytest.mark.parametrize('answer', ['not JSON', '{"route":"unknown"}',
                                    '{"route":"search","query":"https://private.example"}'])
def test_invalid_route_never_searches_or_saves_turn(web_setup, answer):
    c,app,p=web_setup;cid=new_chat(c,web=True)
    attempts=[]
    async def complete(messages,config):
        attempts.append(1)
        return answer, {}
    p.complete=complete
    for _ in range(2):
        result=c.post(f'/api/chats/{cid}/messages',json={'text':'Who is his wife?','request_id':str(uuid4())})
        assert result.status_code==502,result.text
    assert p.searches==0 and app.state.store.messages(cid)==[]
    assert len(attempts)==2


def test_clarification_is_saved_and_streamed_without_search_or_second_model_call(web_setup):
    from test_streaming import enable, events
    c,app,p=web_setup;cid=new_chat(c,web=True);enable(c,cid)
    async def complete(messages,config):
        assert QUERY_CONTRACT in messages[0]['text']
        return '{"route":"clarify","question":"Какие сроки вы имеете в виду?"}', {}
    p.complete=complete
    response = c.post(f'/api/chats/{cid}/messages/stream',
                      json={'text':'Какие сроки?','request_id':str(uuid4())})
    assert events(response)[-1]['event'] == 'done'
    assert app.state.store.messages(cid)[-1]['text'] == 'Какие сроки вы имеете в виду?'
    assert p.searches == 0


def test_general_planning_uses_configured_history_and_accepts_topic_change(tmp_path):
    async def scenario():
        store=Store(tmp_path);calls=[]
        class Provider:
            async def complete(self,messages,config):
                data=json.loads(messages[-1]['text']);calls.append(data)
                if not data['history']:
                    return '{"route":"clarify","question":"Which curricula?"}', {}
                assert len(data['history'])==10
                assert 'School Alpha' in data['history'][0]['text']
                if data['question'] == 'What causes earthquakes?':
                    return '{"route":"direct"}', {}
                return '{"route":"search","query":"Compare School Alpha and School Beta curricula"}', {}
        async def charge(uid,kind,amount,operation,rid):return await operation()
        resolver=RetrievalQuery(store,Provider(),charge);config=AppConfig.model_validate_json(CONFIG.read_text())
        history=[{'role':'user','text':'School Alpha and School Beta'},{'role':'assistant','text':'Two schools.'}]
        history += [{'role':role,'text':'Additional school details'} for _ in range(4) for role in ('user','assistant')]
        question,info=await resolver.resolve('u','c','Compare the two curricula',history,config,'r',10,rag=True,web=False)
        assert question['query']=='Compare School Alpha and School Beta curricula' and info['history_messages']==10
        question,info=await resolver.resolve('u','c','What causes earthquakes?',history,config,'r2',10,rag=True,web=False)
        assert question=={'route':'direct'} and len(calls)==2
        question,info=await resolver.resolve('u','new','Compare their curricula',[],config,'r3',10,rag=True,web=False)
        assert question['route']=='clarify' and not info['history_aware'] and len(calls)==3
    asyncio.run(scenario())


def test_cancel_during_history_aware_planning_saves_no_partial_turn(tmp_path):
    import httpx
    from app.main import create_app
    from app.settings import Settings
    from test_web_search import SearchProvider
    async def scenario():
        started=asyncio.Event()
        p=SearchProvider();original=p.complete
        async def complete(messages,config):
            if QUERY_CONTRACT in messages[0]['text']:
                started.set();await asyncio.Event().wait()
            return await original(messages,config)
        p.complete=complete
        s=Settings(environment='development',origin='http://localhost',demo=True,web_enabled=True,data_dir=tmp_path,config_path=CONFIG)
        app=create_app(s,p);store=app.state.store
        store.user('u','demo@localhost','Demo');token=store.session('u',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            await configure_space_async(c, web_search_enabled=True)
            cid=(await c.post('/api/chats')).json()['id']
            store.add_turn(cid,'Earlier explicit question','Earlier answer',{})
            rid=str(uuid4())
            pending=asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'New standalone question','request_id':rid}))
            await asyncio.wait_for(started.wait(),2)
            assert (await c.post(f'/api/requests/{rid}/cancel')).json()['cancel_requested']
            assert (await pending).status_code==409
            assert p.searches==0 and len(store.messages(cid))==2
            assert (await c.get('/api/admin/operations')).json()['active_paid_operations']==0
        await p.close()
    asyncio.run(scenario())
