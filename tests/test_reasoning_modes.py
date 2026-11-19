import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from app.configuration import AppConfig, ChatOptions,ModelOption,allowed_reasoning
from app.providers import Providers
from test_app import CONFIG,live_settings
from test_roles import roles


def test_per_model_modes_override_legacy_boolean_and_preserve_aliases():
    config=AppConfig.model_validate_json(CONFIG.read_text())
    legacy=config.model_copy(update={'reasoning_supported':True})
    assert allowed_reasoning(legacy)==['none','medium']
    assert legacy.resolve(ChatOptions(reasoning_effort='enabled')).reasoning_effort=='enabled'
    restricted=legacy.model_copy(update={'reasoning_efforts':['low','high']})
    assert restricted.catalog()['models'][0]['reasoning_efforts']==['low','high']
    assert restricted.resolve(ChatOptions(reasoning_effort='high')).reasoning_effort=='high'
    with pytest.raises(ValueError):restricted.resolve(ChatOptions(reasoning_effort='enabled'))
    empty=legacy.model_copy(update={'reasoning_efforts':[]})
    with pytest.raises(ValueError):empty.resolve(ChatOptions(reasoning_effort='none'))
    with pytest.raises(ValueError):ModelOption(id='bad',label='Bad',model='aliceai-llm',reasoning_efforts=['low','low'])
    with pytest.raises(ValueError):AppConfig.model_validate({**config.model_dump(),'reasoning_efforts':['low'],'reasoning_effort':'high'})


@pytest.mark.parametrize('mode,wire',[('none','none'),('low','low'),('medium','medium'),('high','high'),('enabled','medium'),('disabled','none'),('default',None)])
def test_exact_reasoning_wire_parameter(tmp_path,mode,wire):
    async def scenario():
        s=live_settings(tmp_path);p=Providers(s);await p.client.aclose()
        config=AppConfig.model_validate_json(CONFIG.read_text()).model_copy(update={'reasoning_efforts':['none','low','medium','high'],'reasoning_effort':mode})
        def handler(request):
            payload=json.loads(request.content)
            assert payload.get('reasoning_effort')==wire
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'Answer'}}]})
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            _,meta=await p.complete([{'role':'user','text':'Question'}],config)
            assert meta['provider_reasoning_effort']==(wire or 'default')
        finally:await p.close()
    asyncio.run(scenario())


def test_reasoning_profile_survives_catalog_updates_and_user_cannot_override(roles):
    from space_helpers import configure_space
    c,app,p,access,login,s=roles
    login('admin')
    live=c.get('/api/editor/config').json()
    entry=next(model for model in live['config']['models'] if model['id']=='alice-flash')
    entry.update(reasoning_efforts=['low','high'],reasoning_supported=True)
    assert c.put('/api/editor/config',json={'expected_revision':live['revision'],'config':live['config']}).status_code==200
    configure_space(c, model_id='alice-flash', reasoning_effort='low')
    login('user')
    cid=c.post('/api/chats').json()['id']
    assert c.patch(f'/api/chats/{cid}/mode',json={'reasoning_effort':'high'}).status_code==422
    login('admin')
    live=c.get('/api/editor/config').json()
    entry=next(model for model in live['config']['models'] if model['id']=='alice-flash')
    entry['reasoning_efforts']=['high']
    assert c.put('/api/editor/config',json={'expected_revision':live['revision'],'config':live['config']}).status_code==200
    login('user')
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'Question','request_id':str(uuid4())}).status_code==200
    assert app.state.store.chat(cid,'user')['settings']['reasoning_effort']=='low'
