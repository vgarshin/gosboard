import asyncio
from pathlib import Path
import time
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import AppConfig, context_messages, create_app
from app.providers import Providers, split_text
from app.settings import Settings
from app.store import Store, cache_key

CONFIG = Path(__file__).resolve().parents[1] / 'config/app.json'
ORIGIN = 'http://localhost:5173'


class FakeProviders:
    def __init__(self):
        self.llm_calls = self.tts_calls = self.stt_calls = 0
        self.fail = False
        self.last_messages = []

    async def close(self):
        pass

    async def complete(self, messages, config):
        self.llm_calls += 1
        self.last_messages = messages
        self.last_config = config
        if self.fail:
            raise HTTPException(502, 'Unavailable')
        return 'Ответ модели. ' * 30, {'provider_usage': {'totalTokens': '20'}}

    async def transcribe(self, pcm):
        self.stt_calls += 1
        return 'Распознанный текст'

    async def synthesize(self, text, config):
        self.tts_calls += 1
        return b'RIFF-test-audio'


@pytest.fixture
def setup(tmp_path):
    settings = Settings(environment='development', origin=ORIGIN, data_dir=tmp_path,
                        config_path=CONFIG, demo=True)
    providers = FakeProviders()
    app = create_app(settings, providers)
    with TestClient(app, base_url='http://localhost') as client:
        client.headers['Origin'] = ORIGIN
        client.post('/api/auth/demo', follow_redirects=False)
        me = client.get('/api/me').json()
        client.headers['X-CSRF-Token'] = me['csrf']
        yield client, app, providers


def create_chat(client):
    response = client.post('/api/chats')
    assert response.status_code == 200, response.text
    return response.json()['id']


def send(client, cid, text='Привет', rid=None, **extra):
    return client.post(f'/api/chats/{cid}/messages', json={'text': text, 'request_id': rid or str(uuid4()), **extra})


def switch_user(client, app, name):
    email = f'{name}@example.org'
    with app.state.store.connection() as db:
        db.execute("INSERT OR IGNORE INTO access_accounts(email,role,enabled,created,updated) VALUES (?,'user',1,0,0)", (email,))
        db.execute("INSERT OR IGNORE INTO space_memberships(email,space_id) VALUES (?,'initial')", (email,))
    app.state.store.user(name, email, name)
    token = app.state.store.session(name, 3600)
    client.cookies.clear()
    client.cookies.set('gosboard_session', token)
    client.headers['X-CSRF-Token'] = client.get('/api/me').json()['csrf']


def test_chat_roundtrip_and_context(setup):
    client, app, provider = setup
    cid = create_chat(client)
    assert send(client, cid).status_code == 200
    assert send(client, cid, 'Продолжи').status_code == 200
    messages = client.get(f'/api/chats/{cid}/messages').json()
    assert [m['role'] for m in messages] == ['user', 'assistant', 'user', 'assistant']
    assert provider.last_messages[-1]['text'] == 'Продолжи'
    assert len(provider.last_messages) == 4
    assert 'config_version' not in messages[1]['metadata']
    assert app.state.store.messages(cid)[1]['metadata']['config_version'] == AppConfig.model_validate_json(CONFIG.read_text()).version
    reopened = Store(app.state.settings.data_dir)
    assert len(reopened.messages(cid)) == 4


def test_idempotent_request_and_conflict(setup):
    client, app, provider = setup
    cid, rid = create_chat(client), str(uuid4())
    first = send(client, cid, rid=rid)
    second = send(client, cid, rid=rid)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert provider.llm_calls == 1
    assert len(app.state.store.messages(cid)) == 2
    assert send(client, cid, text='Другое', rid=rid).status_code == 409


def test_llm_cache_and_regeneration(setup):
    client, app, provider = setup
    a, b, c = (create_chat(client) for _ in range(3))
    send(client, a)
    assert send(client, b).json()['metadata']['cached'] is True
    assert provider.llm_calls == 1
    send(client, c, regenerate=True)
    assert provider.llm_calls == 2


def test_private_history_audio_and_cache(setup):
    client, app, provider = setup
    cid = create_chat(client)
    response = send(client, cid).json()
    mid = response['message_id']
    speech = client.post(f'/api/chats/{cid}/messages/{mid}/speech', json={'request_id': str(uuid4())})
    assert speech.status_code == 200, speech.text
    url = speech.json()['url']
    assert client.get(url).status_code == 200
    switch_user(client, app, 'second')
    assert client.get('/api/chats').json() == []
    assert client.get(f'/api/chats/{cid}/messages').status_code == 404
    assert send(client, cid).status_code == 404
    assert client.get(url).status_code == 404
    assert client.delete(f'/api/chats/{cid}').status_code == 404
    send(client, create_chat(client))
    assert provider.llm_calls == 2  # No cross-user conversational cache hits.


def test_tts_replay_and_expiry(setup):
    client, app, provider = setup
    cid = create_chat(client)
    mid = send(client, cid).json()['message_id']
    path = f'/api/chats/{cid}/messages/{mid}/speech'
    first = client.post(path, json={'request_id': str(uuid4())}).json()
    second = client.post(path, json={'request_id': str(uuid4())}).json()
    assert first['url'] == second['url']
    assert second['cached'] is True
    assert provider.tts_calls == 1
    with app.state.store.connection() as db:
        db.execute('UPDATE cache SET expires=0')
    assert client.get(first['url']).status_code == 404


def test_stt_bounds_cache_no_raw_audio(setup):
    client, app, provider = setup
    pcm = b'\x01\x00' * 16000
    def upload(value):
        return client.post('/api/stt', content=value, headers={'Content-Type': 'application/octet-stream', 'X-Request-ID': str(uuid4())})
    assert upload(b'xx').status_code == 422
    assert upload(b'x' * 800002).status_code == 413
    assert upload(pcm).json()['text'] == 'Распознанный текст'
    assert upload(pcm).json()['cached'] is True
    assert provider.stt_calls == 1
    with app.state.store.connection() as db:
        values = db.execute('SELECT value FROM cache').fetchall()
    assert all(pcm not in bytes(row['value']) for row in values)


def test_csrf_origin_and_logout(setup):
    client, app, provider = setup
    assert client.post('/api/chats', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/chats', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/chats').status_code == 401


def test_delete_purges_messages_and_derived_cache(setup):
    client, app, provider = setup
    cid = create_chat(client)
    send(client, cid)
    assert client.delete(f'/api/chats/{cid}').status_code == 200
    assert app.state.store.messages(cid) == []
    with app.state.store.connection() as db:
        assert db.execute('SELECT count(*) FROM cache').fetchone()[0] == 0


def test_failed_generation_does_not_add_half_turn(setup):
    client, app, provider = setup
    cid, rid = create_chat(client), str(uuid4())
    provider.fail = True
    assert send(client, cid, rid=rid).status_code == 502
    assert app.state.store.messages(cid) == []
    assert send(client, cid, rid=rid).status_code == 409


def live_settings(tmp_path):
    return Settings(environment='production', origin='https://aitalks.tech', data_dir=tmp_path,
                    config_path=CONFIG, demo=False, allowed={'admin@example.com', 'user@example.com'},
                    admins={'admin@example.com'}, oauth_id='test', oauth_secret='not-a-real-secret',
                    folder='test', api_key='fake', speech_key='fake', budget_reviewed=True, web_pages=False)


def test_production_fails_closed_and_demo_host_guard(tmp_path, setup):
    with pytest.raises(ValueError):
        create_app(Settings(data_dir=tmp_path, config_path=CONFIG, demo=False, allowed=set(), oauth_id=''))
    with pytest.raises(ValueError):
        create_app(Settings(environment='production', origin='https://aitalks.tech', demo=True))
    client, app, provider = setup
    assert client.get('/api/bootstrap', headers={'Host': 'public.example'}).status_code == 403


def test_live_admin_allowlist_and_budget(tmp_path):
    settings = live_settings(tmp_path)
    provider = FakeProviders()
    app = create_app(settings, provider)
    with TestClient(app, base_url=settings.origin) as client:
        client.headers['Origin'] = settings.origin
        app.state.store.user('outsider', 'outsider@example.com', 'Outsider')
        client.cookies.set('gosboard_session', app.state.store.session('outsider', 3600))
        assert client.get('/api/me').status_code == 403
        for uid, email in [('user', 'user@example.com'), ('admin', 'admin@example.com')]:
            app.state.store.user(uid, email, uid)
        token = app.state.store.session('user', 3600)
        client.cookies.clear(); client.cookies.set('gosboard_session', token)
        client.headers['X-CSRF-Token'] = client.get('/api/me').json()['csrf']
        assert client.get('/api/admin/config').status_code == 403
        app.state.store.update_budget(10, settings.monthly_limit, 1, "admin")
        cid = create_chat(client)
        assert send(client, cid).status_code == 200
        assert send(client, cid, 'Ещё').status_code == 429
        assert provider.llm_calls == 1
        assert client.get('/api/auth/demo').status_code == 405
        with app.state.store.connection() as db:
            db.execute("UPDATE access_accounts SET enabled=0 WHERE email='user@example.com'")
        assert client.get('/api/chats').status_code == 403


def test_budget_atomic_reservations_and_restart(tmp_path):
    store = Store(tmp_path)
    store.reserve('u', 'llm', 10, 10, 100)
    with pytest.raises(HTTPException) as err:
        store.reserve('v', 'llm', .01, 10, 100)
    assert err.value.status_code == 429
    reopened = Store(tmp_path)
    assert reopened.usage()[0]['state'] == 'uncertain'


def test_context_whole_turns_and_cache_versions():
    config = AppConfig.model_validate_json(CONFIG.read_text())
    history = [{'role': 'user', 'text': 'a'*10000}, {'role': 'assistant', 'text': 'b'*10000}]
    assert len(context_messages(history, 'test', config)) == 2
    assert cache_key('tts', 'marina', 'text') != cache_key('tts', 'jane', 'text')
    assert cache_key('llm', {'version': '1'}) != cache_key('llm', {'version': '2'})


def test_tts_splitting_is_bounded():
    parts = split_text('Первое предложение. ' * 100)
    assert all(0 < len(part) <= 240 for part in parts)
    assert ' '.join(parts) == ('Первое предложение. ' * 100).strip()


def test_cache_cleanup(tmp_path):
    store = Store(tmp_path)
    store.put_cache('expired', 'u', 'tts', b'abc', -1)
    store.put_cache('large', 'u', 'tts', b'a' * 10, 3600)
    store.cleanup(5)
    assert store.get_cache('expired', 'u') is None
    assert store.get_cache('large', 'u') is None


def test_oauth_pkce_replay_and_pending_admission(tmp_path):
    from urllib.parse import parse_qs, urlparse
    import httpx

    class OAuthProvider(FakeProviders):
        def __init__(self):
            super().__init__()
            self.email = 'admin@example.com'
            self.uid = '123'
            self.token_payload = None

        async def request(self, method, url, **kwargs):
            if url.endswith('/token'):
                self.token_payload = kwargs['data']
                return httpx.Response(200, json={'access_token': 'fake-oauth-token'})
            assert kwargs['headers']['Authorization'] == 'OAuth fake-oauth-token'
            return httpx.Response(200, json={'id': self.uid, 'default_email': self.email, 'display_name': 'Admin'})

    settings = live_settings(tmp_path)
    provider = OAuthProvider()
    app = create_app(settings, provider)
    with TestClient(app, base_url=settings.origin) as client:
        start = client.get('/api/auth/yandex', follow_redirects=False)
        params = parse_qs(urlparse(start.headers['location']).query)
        assert params['code_challenge_method'] == ['S256']
        assert params['redirect_uri'] == [settings.origin + '/api/auth/callback']
        assert 'HttpOnly' in start.headers['set-cookie'] and 'Secure' in start.headers['set-cookie']
        callback = '/api/auth/callback?code=code&state=' + params['state'][0]
        response = client.get(callback, follow_redirects=False)
        assert response.status_code == 303
        assert provider.token_payload['code_verifier']
        assert client.get('/api/me').json()['admin'] is True
        assert client.get(callback, follow_redirects=False).status_code == 400
        start = client.get('/api/auth/yandex', follow_redirects=False)
        state = parse_qs(urlparse(start.headers['location']).query)['state'][0]
        provider.email = 'outsider@example.com'
        denied = client.get('/api/auth/callback?code=code&state=' + state, follow_redirects=False)
        assert denied.headers['location'] == '/?auth_error=denied'
        assert client.get('/api/me').json()['admin'] is True

        def outsider_login():
            start = client.get('/api/auth/yandex', follow_redirects=False)
            state = parse_qs(urlparse(start.headers['location']).query)['state'][0]
            return client.get('/api/auth/callback?code=code&state=' + state, follow_redirects=False)

        provider.uid = '456'
        assert outsider_login().headers['location'] == '/'
        pending = client.get('/api/me').json()
        assert pending['status'] == 'pending' and pending['role'] is None
        assert client.get('/api/config').status_code == 403
        assert client.get('/api/spaces').status_code == 403
        assert outsider_login().headers['location'] == '/'
        assert client.get('/api/me').json()['status'] == 'pending'
        admin_token = app.state.store.session('yandex:123', 3600)
        admin_headers = {'Cookie': f'gosboard_session={admin_token}', 'Origin': settings.origin,
                         'X-CSRF-Token': app.state.store.auth(admin_token)['csrf']}
        response = client.put('/api/admin/users', headers=admin_headers,
                              json={'email':'outsider@example.com','role':'user'})
        assert response.status_code == 200
        assert client.get('/api/me').json()['status'] == 'active'
        assert client.get('/api/spaces').json() == []
        assert client.put('/api/admin/users', headers=admin_headers,
                          json={'email':'outsider@example.com','enabled':False}).status_code == 200
        assert outsider_login().headers['location'] == '/?auth_error=denied'


def test_cancel_preserves_uncertain_budget(tmp_path):
    import httpx

    class SlowProvider(FakeProviders):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def complete(self, messages, config):
            self.started.set()
            await asyncio.sleep(60)
            return 'Should not finish', {}

    async def scenario():
        settings = live_settings(tmp_path)
        provider = SlowProvider()
        app = create_app(settings, provider)
        store = app.state.store
        store.user('admin', 'admin@example.com', 'Admin')
        token = store.session('admin', 3600)
        me = store.auth(token)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.origin,
                                     cookies={'gosboard_session': token},
                                     headers={'Origin': settings.origin, 'X-CSRF-Token': me['csrf']}) as client:
            chat = (await client.post('/api/chats')).json()['id']
            rid = str(uuid4())
            pending = asyncio.create_task(client.post(f'/api/chats/{chat}/messages', json={'text': 'Test', 'request_id': rid}))
            await asyncio.wait_for(provider.started.wait(), 2)
            cancelled = await client.post(f'/api/requests/{rid}/cancel')
            assert cancelled.json()['cancel_requested'] is True
            response = await pending
            assert response.status_code == 409
            assert store.messages(chat) == []
            assert store.usage()[0]['state'] == 'uncertain'
    asyncio.run(scenario())


def test_backup_excludes_ephemeral_data(tmp_path):
    import sqlite3
    import subprocess
    import sys
    store = Store(tmp_path / 'source')
    store.user('u', 'u@example.com', 'User')
    store.session('u', 3600)
    chat = store.new_chat('u')
    store.add_turn(chat['id'], 'Keep me', 'Answer', {})
    store.put_cache('private-audio', 'u', 'tts', b'private audio', 3600)
    target = tmp_path / 'backup.sqlite3'
    subprocess.run([sys.executable, str(CONFIG.parents[1] / 'scripts/backup.py'), str(store.path), str(target)], check=True, capture_output=True)
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM cache').fetchone()[0] == 0
    assert b'private audio' not in target.read_bytes()


def test_provider_contracts(tmp_path):
    import base64
    import io
    import json
    import wave
    import httpx

    async def scenario():
        settings = live_settings(tmp_path)
        config = AppConfig.model_validate_json(CONFIG.read_text())
        provider = Providers(settings)
        await provider.client.aclose()
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
            wav.writeframes(b'\x01\x00' * 100)
        requests = []
        def handler(request):
            requests.append(request)
            assert request.headers['Authorization'] == 'Api-Key fake'
            if request.url.path=='/v1/chat/completions':
                payload = json.loads(request.content)
                assert payload['model'] == 'gpt://test/yandexgpt/latest'
                assert payload['messages'][-1]['role'] == 'user'
                return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'Ответ'}}], 'usage':{'total_tokens':10}})
            if request.url.host.startswith('stt'):
                assert request.url.params['format'] == 'lpcm'
                assert request.url.params['sampleRateHertz'] == '16000'
                return httpx.Response(200, json={'result': 'Текст'})
            payload = json.loads(request.content)
            assert len(payload['text']) <= 240
            raw = stream.getvalue()
            lines = [json.dumps({'result': {'audioChunk': {'data': base64.b64encode(part).decode()}}}) for part in [raw[:30], raw[30:]]]
            return httpx.Response(200, text='\n'.join(lines))
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        text, metadata = await provider.complete([{'role': 'user', 'text': 'Test'}], config)
        assert text == 'Ответ'
        assert await provider.transcribe(b'\x00\x00' * 1600) == 'Текст'
        audio = await provider.synthesize('Пример. ' * 80, config)
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            assert wav.getnframes() == 100 * len(split_text('Пример. ' * 80))
        await provider.close()
    asyncio.run(scenario())


def test_per_chat_settings_persist_and_drive_generation(setup):
    import json
    from space_helpers import configure_space
    client, app, provider = setup
    configure_space(client, model_id='alice-flash')
    cid = create_chat(client)
    configure_space(client, model_id='default')
    assert client.patch(f'/api/chats/{cid}/mode', json={'communication_mode':'voice'}).status_code == 200
    assert 'model_id' not in client.get('/api/chats').json()[0]['settings']
    assert Store(app.state.settings.data_dir).chat(cid, 'demo')['settings']['model_id'] == 'alice-flash'
    assert send(client, cid).status_code == 200
    assert provider.last_config.model == 'aliceai-llm-flash'
    metadata = app.state.store.messages(cid)[1]['metadata']
    assert metadata['model_label'] == 'Alice AI LLM Flash'
    with app.state.store.connection() as db:
        row = db.execute('SELECT snapshot FROM configurations WHERE hash=?', (metadata['config_hash'],)).fetchone()
    assert json.loads(row['snapshot'])['system_prompt'] == provider.last_config.system_prompt


def test_settings_reject_unavailable_tools_and_cross_user_mutation(setup):
    client, app, provider = setup
    cid = create_chat(client)
    original = client.get('/api/chats').json()[0]['settings']
    for invalid in [{'model_id':'invented'}, {'prompt_id':'invented'}, {'rag_enabled':True},
                    {'knowledge_base_id':'private'}, {'system_prompt':'override'}, {'web_search_enabled':True}]:
        assert client.patch(f'/api/chats/{cid}/mode', json=invalid).status_code == 422
    assert client.get('/api/chats').json()[0]['settings'] == original
    switch_user(client, app, 'another')
    assert client.patch(f'/api/chats/{cid}/mode', json={'communication_mode':'voice'}).status_code == 404
    assert provider.llm_calls == provider.tts_calls == provider.stt_calls == 0


def test_chat_creation_options_and_cache_settings_scope(setup):
    from space_helpers import configure_space
    client, app, provider = setup
    chats = []
    for model in ('default','alice-flash','alice-llm'):
        configure_space(client, model_id=model)
        chats.append(create_chat(client))
    for cid in chats:
        result = send(client, cid)
        assert result.status_code == 200
        assert result.json()['metadata']['cached'] is False
    assert provider.llm_calls == 3
    assert client.post('/api/chats', json={'rag_enabled':True}).status_code == 422


def test_old_database_migration_preserves_history(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / 'gosboard.sqlite3') as db:
        db.execute('CREATE TABLE chats(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,created REAL,updated REAL)')
        db.execute('INSERT INTO chats VALUES (?,?,?,?,?)', ('legacy', 'u', 'Old chat', 1, 1))
        db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,chat_id TEXT,role TEXT,text TEXT,created REAL,metadata TEXT)')
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', ('m', 'legacy', 'user', 'Original text', 1, '{}'))
    store = Store(tmp_path)
    assert store.chat('legacy', 'u')['settings']['model_id'] == 'default'
    assert store.messages('legacy')[0]['text'] == 'Original text'
    store.new_chat('u')
    assert len(Store(tmp_path).chats('u')) == 2


def test_catalog_remains_admin_owned_and_hidden_from_public_config(setup):
    client, app, provider = setup
    config = client.get('/api/config').json()
    assert not {'models','prompts','knowledge_bases','capabilities'} & config.keys()
    assert len(client.get('/api/management/spaces/initial/policy').json()['models']) == 3
    switch_user(client, app, 'another')
    assert client.get('/api/admin/config').status_code == 403
    assert client.get('/api/management/spaces/initial/policy').status_code == 403


def test_restore_rehearsal_uses_copy_and_no_providers(tmp_path):
    import subprocess
    import sys
    import hashlib
    import json
    store = Store(tmp_path / 'source')
    store.user('u', 'u@example.com', 'User')
    cid = store.new_chat('u')['id']
    store.add_turn(cid, 'Keep private history', 'Original response', {})
    backup = tmp_path / 'snapshot.sqlite3'
    subprocess.run([sys.executable, str(CONFIG.parents[1] / 'scripts/backup.py'), str(store.path), str(backup)], check=True, capture_output=True)
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    result = subprocess.run([sys.executable, str(CONFIG.parents[1] / 'scripts/verify_restore.py'), str(backup), str(CONFIG)],
                            check=True, capture_output=True, env={**__import__('os').environ, 'PYTHONPATH': str(CONFIG.parents[1] / 'backend')})
    verified = json.loads(result.stdout)
    assert verified['two_user_isolation'] == 'passed'
    assert verified['per_chat_settings'] == 'passed'
    assert verified['existing_history'] == 'opened'
    assert verified['restored_messages'] == 2
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == before
