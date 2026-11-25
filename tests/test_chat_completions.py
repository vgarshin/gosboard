import asyncio
import httpx
import pytest
from fastapi import HTTPException
from app.configuration import AppConfig
from app.providers import Providers, LLM_URL, LLM_PROTOCOL
from test_app import CONFIG,live_settings


@pytest.mark.parametrize('model',['yandexgpt/latest','yandexgpt-lite/latest','aliceai-llm','aliceai-llm-flash','deepseek-v4.1-flash'])
def test_shared_chat_adapter(tmp_path,model):
    async def scenario():
        p=Providers(live_settings(tmp_path));await p.client.aclose()
        def handler(request):
            import json
            body=json.loads(request.content)
            assert str(request.url)==LLM_URL
            assert request.headers['Authorization']=='Api-Key fake'
            assert request.headers['OpenAI-Project']=='test'
            assert body['model']=='gpt://test/'+model
            assert body['messages']==[{'role':'system','content':'Instruction'},{'role':'user','content':'Question'}]
            assert body['max_completion_tokens']==1200
            assert body['store'] is False and body['stream'] is False
            assert 'reasoning_effort' not in body
            return httpx.Response(200,json={'model':model,'choices':[{'finish_reason':'stop','message':{'content':'Answer'}}],'usage':{'total_tokens':10}})
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            answer,meta=await p.complete([{'role':'system','text':'Instruction'},{'role':'user','text':'Question'}],AppConfig.model_validate_json(CONFIG.read_text()).model_copy(update={'model':model}))
            assert answer=='Answer' and meta['provider_api']==LLM_PROTOCOL
            assert meta['provider_usage']['total_tokens']==10
        finally:await p.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('message,finish',[(None,'stop'),('', 'stop'),('Partial','length'),('Bad','content_filter'),(['text'],'stop')])
def test_reject_invalid_and_truncated_completions(tmp_path,message,finish):
    async def scenario():
        p=Providers(live_settings(tmp_path));await p.client.aclose()
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':message}}]})))
        try:
            with pytest.raises(HTTPException) as error:await p.complete([{'role':'user','text':'q'}],AppConfig.model_validate_json(CONFIG.read_text()))
            assert error.value.status_code==502
        finally:await p.close()
    asyncio.run(scenario())


def test_disabled_reasoning_parameter_and_safe_provider_errors(tmp_path):
    async def scenario():
        p=Providers(live_settings(tmp_path));await p.client.aclose()
        def handler(r):
            import json
            assert json.loads(r.content)['reasoning_effort']=='none'
            return httpx.Response(403,text='PRIVATE SECRET PROVIDER BODY')
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            config=AppConfig.model_validate_json(CONFIG.read_text()).model_copy(update={'reasoning_supported':True,'reasoning_effort':'disabled'})
            with pytest.raises(HTTPException) as error:await p.complete([{'role':'user','text':'q'}],config)
            assert error.value.status_code==502 and 'PRIVATE' not in error.value.detail
        finally:await p.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('grounded',[False,True,'query'])
def test_json_mode_only_for_grounded_answers_with_plain_history(tmp_path,grounded):
    from app.rag import grounding_instruction
    async def scenario():
        p=Providers(live_settings(tmp_path));await p.client.aclose()
        def handler(request):
            import json
            body=json.loads(request.content)
            if grounded:assert body['response_format']=={'type':'json_object'}
            else:assert 'response_format' not in body
            assert body['messages'][2]['content']=='Earlier ordinary response'
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"supported":false,"answer":"","used_sources":[]}' if grounded else 'Plain text'}}]})
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            from app.retrieval_query import QUERY_CONTRACT
            instruction=QUERY_CONTRACT if grounded=='query' else grounding_instruction([{'id':'W1'}]) if grounded else 'Normal instruction'
            messages=[{'role':'system','text':instruction},{'role':'user','text':'Earlier question'},{'role':'assistant','text':'Earlier ordinary response'},{'role':'user','text':'Search now'}]
            text,meta=await p.complete(messages,AppConfig.model_validate_json(CONFIG.read_text()))
            assert meta['provider_response_format']==('json_object' if grounded else 'text')
        finally:await p.close()
    asyncio.run(scenario())


def test_cloudru_gigachat_routing_and_structured_stream(tmp_path):
    import json

    from app.providers import CLOUDRU_PROTOCOL, CLOUDRU_URL
    from app.rag import grounding_instruction, validate_rag_answer

    async def scenario():
        settings = live_settings(tmp_path)
        settings.cloudru_api_key = 'fake-cloudru'
        provider = Providers(settings)
        await provider.client.aclose()
        sources = [{'id': 'D1', 'type': 'rag'}]
        raw = '{"supported":true,"answer":"Fact [D1]","used_sources":["D1"]}'
        calls = []

        def handler(request):
            url = str(request.url)
            calls.append(url)
            assert url == CLOUDRU_URL
            assert request.headers['authorization'] == 'Bearer fake-cloudru'
            assert 'OpenAI-Project' not in request.headers
            body = json.loads(request.content)
            assert body['model'] == 'ai-sage/GigaChat3-10B-A1.8B' and body['max_tokens'] == 1200
            assert 'max_completion_tokens' not in body and 'store' not in body
            if not body['stream']:
                assert 'response_format' not in body
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'Hello'}}]})
            assert body['response_format'] == {'type': 'json_object'}
            parts = [{'choices': [{'delta': {'content': raw}}]},
                     {'choices': [{'delta': {'content': ''}, 'finish_reason': 'stop'}]}]
            return httpx.Response(200, text=''.join(f'data: {json.dumps(part)}\n\n' for part in parts) + 'data: [DONE]\n\n')

        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        config = AppConfig.model_validate_json(CONFIG.read_text())
        config = config.model_copy(update={'model': 'cloudru/ai-sage/GigaChat3-10B-A1.8B'})
        messages = [{'role': 'system', 'text': 'Ordinary chat'}, {'role': 'user', 'text': 'Question'}]
        deltas = []

        async def on_delta(part):
            deltas.append(part)

        try:
            answer, meta = await provider.complete(messages, config)
            assert answer == 'Hello' and meta['provider_api'] == CLOUDRU_PROTOCOL
            messages[0]['text'] = grounding_instruction(sources)
            answer, meta = await provider.complete_stream(messages, config, on_delta)
            assert answer == ''.join(deltas)
            assert validate_rag_answer(answer, sources) == ('Fact [D1]', sources, True)
            assert meta['provider_api'] == CLOUDRU_PROTOCOL and meta['provider_response_format'] == 'json_object'
            assert calls == [CLOUDRU_URL, CLOUDRU_URL]
        finally:
            await provider.close()

    asyncio.run(scenario())
