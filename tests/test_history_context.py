import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from app.main import context_messages,create_app
from app.configuration import AppConfig,ChatOptions
from app.providers import Providers,LLM_PROTOCOL
from test_app import CONFIG,live_settings
from test_roles import roles


def test_history_window_limits_and_whole_turn_character_budget():
    config=AppConfig.model_validate_json(CONFIG.read_text())
    history=[{'role':role,'text':f'{i}-{role}'} for i in range(15) for role in ('user','assistant')]
    messages=context_messages(history,'Now',config)
    assert len(messages)==22 and messages[1]['text']=='5-user'
    assert context_messages(history,'Now',config,4)[1:-1]==history[-4:]
    assert len(context_messages(history,'Now',config,0))==2
    huge=[{'role':'user','text':'u'*10000},{'role':'assistant','text':'a'*10000}]
    assert len(context_messages(huge,'Now',config,20))==2
    for count in (-2,1,101,True):
        with pytest.raises(ValueError):ChatOptions(history_messages=count)


def test_only_admin_changes_history_and_existing_profiles_keep_history_limit(roles):
    from space_helpers import configure_space
    c,app,p,access,login,s=roles
    original=p.complete
    async def capture(messages,config):
        p.last_messages=messages
        return await original(messages,config)
    p.complete=capture
    login('admin')
    configure_space(c,history_messages=4)
    assert c.put('/api/management/spaces/initial/assignments',json={
        'email':'editor@example.com','assigned':True,'management':True,
    }).status_code==200
    login('editor')
    route='/api/management/spaces/initial/policy'
    policy=c.get(route).json()['policy']
    policy['settings']['history_messages']=2
    assert c.put(route,json={'revision':2,'policy':policy}).status_code==403
    login('user')
    cid=c.post('/api/chats').json()['id']
    assert c.patch(f'/api/chats/{cid}/mode',json={'history_messages':2}).status_code==422
    for i in range(4):
        app.state.store.add_turn(cid,f'q{i}',f'a{i}',{})
    login('admin')
    configure_space(c,history_messages=0)
    login('user')
    assert c.patch(f'/api/chats/{cid}/mode',json={'communication_mode':'voice'}).status_code==200
    response=c.post(f'/api/chats/{cid}/messages',json={'text':'Continue','request_id':str(uuid4())})
    assert response.status_code==200,response.text
    assert [m['text'] for m in p.last_messages[1:-1]]==['q2','a2','q3','a3']
    assert len(app.state.store.messages(cid))==10
    new=c.post('/api/chats').json()['id']
    app.state.store.add_turn(new,'Unshared context','The earlier answer',{})
    response=c.post(f'/api/chats/{new}/messages',json={'text':'No previous context','request_id':str(uuid4())})
    assert response.status_code==200 and len(p.last_messages)==2


def test_saved_history_reaches_actual_chat_completions_wire_payload(tmp_path):
    async def scenario():
        settings=live_settings(tmp_path);provider=Providers(settings);await provider.client.aclose();seen=[]
        def handler(request):
            body=json.loads(request.content);seen.append(body)
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'Ответ'}}]})
        provider.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app=create_app(settings,provider);store=app.state.store
        store.user('user','user@example.com','User');token=store.session('user',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.origin,cookies={'gosboard_session':token},headers={'Origin':settings.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            cid=(await c.post('/api/chats')).json()['id']
            for text in ('Запомни код КЕДР-72','Какой код я назвал?'):
                r=await c.post(f'/api/chats/{cid}/messages',json={'text':text,'request_id':str(uuid4())});assert r.status_code==200,r.text
            assert seen[-1]['messages'][1:]==[{'role':'user','content':'Запомни код КЕДР-72'},{'role':'assistant','content':'Ответ'},{'role':'user','content':'Какой код я назвал?'}]
            assert store.messages(cid)[-1]['metadata']['provider_api']==LLM_PROTOCOL
        await provider.close()
    asyncio.run(scenario())
