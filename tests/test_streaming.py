import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.configuration import AppConfig
from app.providers import Providers
from app.reply_stream import ReplyStream, provisional_answer
from test_app import CONFIG, setup, create_chat, live_settings
from test_roles import roles


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def enable(client, cid, **options):
    assert not set(options) - {'auto_speech', 'communication_mode'}
    mode = options.get('communication_mode') or ('voice' if options.get('auto_speech') else 'text')
    result = client.patch(f'/api/chats/{cid}/mode', json={'communication_mode':mode})
    assert result.status_code == 200, result.text


def test_stream_roundtrip_history_and_idempotent_retry(setup):
    c, app, p = setup
    cid = create_chat(c); enable(c, cid)
    async def stream(messages, config, delta):
        p.llm_calls += 1; p.last_messages = messages
        await delta('Первая часть. ')
        assert app.state.store.messages(cid) == []
        await delta('Вторая часть.')
        return 'Первая часть. Вторая часть.', {'provider_streaming':True}
    p.complete_stream = stream
    rid = str(uuid4()); body = {'text':'Вопрос', 'request_id':rid}
    first = events(c.post(f'/api/chats/{cid}/messages/stream', json=body))
    assert ''.join(e['text'] for e in first if e['event']=='delta') == 'Первая часть. Вторая часть.'
    assert first[-1]['event']=='done' and first[-1]['metadata']['delivery']['streaming']
    assert not any(e['event']=='audio' for e in first)
    assert len(c.get(f'/api/chats/{cid}/messages').json())==2
    replay = events(c.post(f'/api/chats/{cid}/messages/stream', json=body))
    assert replay[-1]['message_id']==first[-1]['message_id'] and p.llm_calls==1
    assert len(app.state.store.messages(cid))==2
    # Classic still works and receives the previous complete exchange.
    assert c.post(f'/api/chats/{cid}/messages', json={'text':'Продолжи', 'request_id':str(uuid4())}).status_code==200
    assert [m['role'] for m in p.last_messages]==['system','user','assistant','user']


def test_failed_partial_answer_is_not_history(setup):
    c, app, p = setup; cid=create_chat(c); enable(c,cid)
    async def stream(messages, config, delta):
        await delta('Незавершённый ответ')
        raise HTTPException(502,'Поток прерван')
    p.complete_stream=stream
    result=events(c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Вопрос','request_id':str(uuid4())}))
    assert any(e['event']=='delta' for e in result)
    assert result[-1]=={'event':'error','detail':'Поток прерван'}
    assert app.state.store.messages(cid)==[]


def test_speech_starts_before_text_finishes_and_audio_cache_is_private(setup):
    c, app, p = setup; cid=create_chat(c); enable(c,cid,auto_speech=True)
    started=None
    async def synthesize(text, config):
        p.tts_calls += 1; started.set(); return b'RIFF-test-audio'
    async def stream(messages, config, delta):
        nonlocal started
        started=asyncio.Event()
        text='Это первая законченная фраза, которая достаточно длинна для начала озвучивания ответа модели. '
        await delta(text)
        await asyncio.wait_for(started.wait(),1)
        assert app.state.store.messages(cid)==[]
        await delta('Конец.')
        return text+'Конец.', {}
    p.synthesize=synthesize;p.complete_stream=stream
    result=events(c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Вопрос','request_id':str(uuid4())}))
    audio=[e for e in result if e['event']=='audio'];assert len(audio)==2
    assert c.get(audio[0]['url']).content==b'RIFF-test-audio'
    assert p.tts_calls==2 and result[-1]['event']=='done'
    from test_app import switch_user
    switch_user(c,app,'other');assert c.get(audio[0]['url']).status_code==404


def test_tts_failure_does_not_discard_text(setup):
    c,app,p=setup;cid=create_chat(c);enable(c,cid,auto_speech=True)
    async def failed(text,config):raise HTTPException(429,'Budget')
    p.synthesize=failed
    result=events(c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Вопрос','request_id':str(uuid4())}))
    assert any(e['event']=='audio_error' for e in result)
    assert result[-1]['event']=='done' and len(app.state.store.messages(cid))==2


def test_stream_permissions_preferences_and_no_model_privilege(roles):
    c,app,p,access,login,s=roles
    login('admin')
    route='/api/management/spaces/initial/policy'
    policy=c.get(route).json()['policy']
    policy['classic_text']=True
    assert c.put(route,json={'revision':1,'policy':policy}).status_code==200
    login('user')
    cid=create_chat(c)
    body={'text':'Question','request_id':str(uuid4())}
    assert c.post(f'/api/chats/{cid}/messages/stream',json=body).status_code==409
    enable(c,cid,auto_speech=True)
    assert c.get('/api/chats').json()[0]['settings']['auto_speech'] is True
    assert c.patch(f'/api/chats/{cid}/mode',json={'model_id':'alice-flash'}).status_code==422
    login('admin')
    assert c.post(f'/api/chats/{cid}/messages/stream',json=body).status_code==404
    assert c.post('/api/requests/'+body['request_id']+'/audio/stop').json()=={'stopped':False}
    login('user')
    assert c.patch(f'/api/chats/{cid}/mode',json={'communication_mode':'text'}).status_code==200
    assert c.post(f'/api/chats/{cid}/messages',json=body).status_code==200


@pytest.mark.parametrize('answer',['Текст с "кавычками"\nи строкой.','Emoji 😀 и обратная \\ черта'])
def test_provisional_json_prefixes_do_not_expose_schema(answer):
    raw=json.dumps({'supported':True,'answer':answer,'used_sources':['W1']},ensure_ascii=True)
    previous=''
    for i in range(len(raw)+1):
        preview=provisional_answer(raw[:i])
        assert answer.startswith(preview)
        assert preview.startswith(previous)
        previous=preview
    assert previous==answer
    assert provisional_answer('{"metadata":{"answer":"WRONG"},"answer":"RIGHT"}')=='RIGHT'


@pytest.mark.parametrize('finish,done',[('stop',True),('length',True),('stop',False)])
def test_provider_stream_handles_utf8_and_rejects_truncation(tmp_path,finish,done):
    async def scenario():
        p=Providers(live_settings(tmp_path));await p.client.aclose()
        fragments=[{'choices':[{'delta':{'content':part},'finish_reason':None}]} for part in ['Привет, ','мир!']]
        fragments += [{'model':'aliceai-llm','choices':[{'delta':{},'finish_reason':finish}],'usage':{'total_tokens':7}}]
        wire=''.join('data: '+json.dumps(item,ensure_ascii=False)+'\n\n' for item in fragments)+('data: [DONE]\n\n' if done else '')
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                data=wire.encode()
                for i in range(0,len(data),3):yield data[i:i+3]
        def handler(request):
            payload=json.loads(request.content);assert payload['stream'] and payload['store'] is False
            return httpx.Response(200,stream=Bytes())
        p.client=httpx.AsyncClient(transport=httpx.MockTransport(handler));parts=[]
        async def delta(text):parts.append(text)
        try:
            if finish=='stop' and done:
                answer,meta=await p.complete_stream([{'role':'user','text':'Hi'}],AppConfig.model_validate_json(CONFIG.read_text()),delta)
                assert answer=='Привет, мир!' and parts==['Привет, ','мир!']
                assert meta['provider_usage']=={'total_tokens':7}
            else:
                with pytest.raises(HTTPException):await p.complete_stream([{'role':'user','text':'Hi'}],AppConfig.model_validate_json(CONFIG.read_text()),delta)
        finally:await p.close()
    asyncio.run(scenario())


def test_phrase_queue_bounded_and_cancel_mutes_future_audio():
    async def scenario():
        started=asyncio.Event();cancelled=asyncio.Event()
        async def synth(text,config):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        stream=ReplyStream(synth);stream.auto_speech=True
        stream.audio_task=asyncio.create_task(stream.audio_worker())
        stream.feed('Пример достаточно длинной фразы. '*400,final=True)
        assert stream.spoken_chars==6000 and stream.phrases.qsize()<=80
        await asyncio.wait_for(started.wait(),1);stream.stop_audio()
        with pytest.raises(asyncio.CancelledError):await stream.audio_task
        assert cancelled.is_set()
        before=stream.phrases.qsize();stream.feed('No more',final=True)
        assert stream.phrases.qsize()==before
    asyncio.run(scenario())

from test_web_search import web_setup, new_chat
from test_rag import ready_base


@pytest.mark.parametrize('rag,web',[(True,False),(False,True),(True,True)])
@pytest.mark.parametrize('invalid',[False,True])
def test_grounded_preview_and_speech_wait_for_validated_sources(web_setup,rag,web,invalid):
    c,app,p=web_setup;bid=ready_base(c)[0] if rag else None
    cid=new_chat(c,rag,web,bid);enable(c,cid,auto_speech=True)
    called=False
    async def stream(messages,config,delta):
        nonlocal called
        called=True
        raw,meta=await p.complete(messages,config)
        if invalid:
            raw=json.dumps({'supported':True,'answer':'Invented [W99]','used_sources':['W99']})
        for i in range(0,len(raw),17):
            await delta(raw[i:i+17]);await asyncio.sleep(0)
            assert p.spoken is None, 'Unvalidated source answer reached TTS'
        return raw,meta
    p.complete_stream=stream
    result=events(c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Education reform','request_id':str(uuid4())}))
    assert called
    deltas=[e for e in result if e['event']=='delta'];assert deltas and all(e['provisional'] for e in deltas)
    assert not ''.join(e['text'] for e in deltas).startswith('{')
    if invalid:
        assert result[-1]['event']=='error' and app.state.store.messages(cid)==[]
        assert p.spoken is None and not any(e['event']=='audio' for e in result)
    else:
        assert result[-1]['event']=='done' and p.spoken is not None
        answer=next(e for e in result if e['event']=='answer')
        assert {s['type'] for s in app.state.store.messages(cid)[-1]['metadata']['citations']}==({'rag'} if rag else set())|({'web'} if web else set())
        assert result.index(answer)<next(i for i,e in enumerate(result) if e['event']=='audio')
        assert '[' not in p.spoken


def test_classic_and_streaming_share_answer_and_audio_cache(setup):
    c,app,p=setup
    first=create_chat(c)
    assert c.post(f'/api/chats/{first}/messages',json={'text':'Same question','request_id':str(uuid4())}).status_code==200
    second=create_chat(c);enable(c,second,auto_speech=True)
    result=events(c.post(f'/api/chats/{second}/messages/stream',json={'text':'Same question','request_id':str(uuid4())}))
    assert result[-1]['metadata']['cached'] and p.llm_calls==1
    calls=p.tts_calls
    third=create_chat(c);enable(c,third,auto_speech=True)
    again=events(c.post(f'/api/chats/{third}/messages/stream',json={'text':'Same question','request_id':str(uuid4())}))
    assert p.tts_calls==calls and all(e['cached'] for e in again if e['event']=='audio')


@pytest.mark.parametrize('action',['cancel','mute','revoke'])
def test_stream_lifecycle_and_budget_reservations(tmp_path,action):
    from app.main import create_app
    from app.store import digest
    from test_app import FakeProviders
    async def scenario():
        class Waiting(FakeProviders):
            def __init__(self):
                super().__init__();self.audio_started=asyncio.Event();self.finish_text=asyncio.Event();self.audio_cancelled=asyncio.Event()
            async def complete_stream(self,messages,config,delta):
                self.llm_calls+=1
                text='Это законченная фраза, достаточно длинная для проверки озвучивания до завершения ответа модели. '
                await delta(text)
                await self.finish_text.wait()
                await delta('Конец.')
                return text+'Конец.',{}
            async def synthesize(self,text,config):
                self.audio_started.set()
                try:await asyncio.Event().wait()
                finally:self.audio_cancelled.set()
        s=live_settings(tmp_path);p=Waiting();app=create_app(s,p);store=app.state.store
        for uid in ('admin','user'):store.user(uid,uid+'@example.com',uid)
        def headers(uid):
            token=store.session(uid,3600);auth=store.auth(token)
            return {'Origin':s.origin,'X-CSRF-Token':auth['csrf'],'Cookie':'gosboard_session='+token}
        uh,ah=headers('user'),headers('admin')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,headers=uh) as c:
            cid=(await c.post('/api/chats',json={'communication_mode':'voice'})).json()['id']
            rid=str(uuid4());task=asyncio.create_task(c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Question','request_id':rid}))
            await asyncio.wait_for(p.audio_started.wait(),2)
            assert store.messages(cid)==[]
            if action=='mute':
                # Other users cannot mute this owner's request.
                other=await c.post(f'/api/requests/{rid}/audio/stop',headers=ah)
                assert not other.json()['stopped'] and not p.audio_cancelled.is_set()
                assert (await c.post(f'/api/requests/{rid}/audio/stop')).json()['stopped']
                p.finish_text.set()
            elif action=='revoke':
                assert (await c.put('/api/admin/users',headers=ah,json={'email':'user@example.com','role':'user','enabled':False})).status_code==200
            else:
                assert (await c.post(f'/api/requests/{rid}/cancel')).json()['cancel_requested']
            result=events(await asyncio.wait_for(task,3))
            assert p.audio_cancelled.is_set()
            assert result[-1]['event']==('done' if action=='mute' else 'error')
            assert len(store.messages(cid))==(2 if action=='mute' else 0)
            usage=store.usage('user')
            assert len(usage)==2
            by_kind={row['kind']:row for row in usage}
            assert by_kind['tts']['state']=='uncertain'
            assert by_kind['llm']['state']==('complete' if action=='mute' else 'uncertain')
        await p.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('users',[5,10])
def test_concurrent_streams_keep_users_history_and_audio_isolated(tmp_path,users):
    from app.main import create_app
    from test_app import FakeProviders
    async def scenario():
        class Concurrent(FakeProviders):
            async def complete_stream(self,messages,config,delta):
                text='Ответ: '+messages[-1]['text']
                await delta(text[:8]);await asyncio.sleep(.02);await delta(text[8:])
                return text,{}
        s=live_settings(tmp_path);p=Concurrent()
        s.allowed.update(f'load{i}@example.com' for i in range(users))
        app=create_app(s,p);store=app.state.store
        async def one(i):
            uid=f'load{i}';store.user(uid,uid+'@example.com',uid)
            token=store.session(uid,3600);auth=store.auth(token)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,
                 headers={'Origin':s.origin,'X-CSRF-Token':auth['csrf'],'Cookie':'gosboard_session='+token}) as c:
                cid=(await c.post('/api/chats',json={'communication_mode':'voice'})).json()['id']
                result=events(await c.post(f'/api/chats/{cid}/messages/stream',json={'text':uid,'request_id':str(uuid4())}))
                assert result[-1]['event']=='done'
                saved=(await c.get(f'/api/chats/{cid}/messages')).json()
                assert [m['text'] for m in saved]==[uid,'Ответ: '+uid]
                assert store.messages(cid)[-1]['metadata']['context']['history_messages_included']==0
                assert (await c.get(next(e['url'] for e in result if e['event']=='audio'))).status_code==200
                return cid,token
        results=await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(users))),10)
        assert len({cid for cid,token in results})==users
        assert sum(row['calls'] for row in store.usage())==2*users
        await p.close()
    asyncio.run(scenario())
