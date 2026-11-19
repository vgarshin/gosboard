from uuid import uuid4

from test_app import setup, send
from test_web_search import web_setup


def create_space(client, name):
    response = client.post('/api/management/spaces', json={'name': name, 'description': f'Описание {name}'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def assign(client, sid, email='demo@localhost', assigned=True, management=False):
    return client.put(f'/api/management/spaces/{sid}/assignments', json={
        'email': email, 'assigned': assigned, 'management': management,
    })


def space_chat(client, sid, **body):
    response = client.post('/api/chats', json=body, headers={'X-Space-ID':sid})
    assert response.status_code == 200, response.text
    return response.json()['id']


def policy(client, sid, instruction):
    path = f'/api/management/spaces/{sid}/policy'
    loaded = client.get(path).json()['policy']
    revision = next(space['revision'] for space in client.get('/api/management/spaces').json() if space['id'] == sid)
    response = client.put(path, json={'revision':revision, 'policy':{**loaded, 'prompt':instruction}})
    assert response.status_code == 200, response.text


def test_memberships_are_independent_and_history_survives_revocation(setup):
    client, _, provider = setup
    sid = create_space(client, 'Второе')
    assert client.get('/api/chats', headers={'X-Space-ID':sid}).status_code == 403
    assert assign(client, sid).status_code == 200
    first, second = space_chat(client, 'initial'), space_chat(client, sid)
    assert [chat['id'] for chat in client.get('/api/chats').json()] == [first]
    assert [chat['id'] for chat in client.get('/api/chats', headers={'X-Space-ID':sid}).json()] == [second]
    assert send(client, first, 'Только в основном').status_code == 200
    assert send(client, second, 'Только во втором').status_code == 200
    assert len(provider.last_messages) == 2
    assert assign(client, sid, assigned=False).status_code == 200
    assert send(client, second).status_code == 403
    assert client.delete(f'/api/chats/{second}').status_code == 403
    assert len(client.get(f'/api/chats/{second}/messages').json()) == 2
    assert [chat['id'] for chat in client.get('/api/chats/archived').json()] == [second]
    assert send(client, first).status_code == 200


def test_policy_and_mode_profiles_are_snapshotted(setup):
    client, _, provider = setup
    sid = create_space(client, 'Снимки')
    assert assign(client, sid).status_code == 200
    policy(client, sid, 'Первая инструкция')
    one = space_chat(client, sid)
    policy(client, sid, 'Вторая инструкция')
    two = space_chat(client, sid)
    assert client.patch(f'/api/chats/{one}/mode', json={'communication_mode':'voice'}).status_code == 200
    assert send(client, one).status_code == 200
    assert provider.last_config.system_prompt == 'Первая инструкция'
    assert send(client, two).status_code == 200
    assert provider.last_config.system_prompt == 'Вторая инструкция'
    assert client.patch(f'/api/chats/{one}/mode', json={'communication_mode':'voice','rag_enabled':True}).status_code == 422
    assert client.put(f'/api/chats/{one}/settings', json={}).status_code == 404


def test_import_pending_is_idempotent_and_admission_keeps_memberships(setup):
    client, app, provider = setup
    sid = create_space(client, 'Для заявки')
    preview = client.post('/api/admin/users/import', content=f'email,role,space_ids\nnew@example.org,editor,initial;{sid}\n').json()
    url = f"/api/admin/users/import/{preview['batch']}"
    assert client.post(url, json={}).json() == {'created':1,'admitted':0,'skipped':0}
    assert client.post(url, json={'approve':True}).json() == {'created':1,'admitted':0,'skipped':0}
    app.state.store.user('new', 'new@example.org', 'Новое имя')
    token = app.state.store.session('new', 3600)
    headers = {'Cookie': f'gosboard_session={token}', 'X-CSRF-Token':app.state.store.auth(token)['csrf']}
    assert client.get('/api/me', headers=headers).json()['role'] is None
    assert client.post('/api/stt', headers=headers).status_code == 403
    assert provider.stt_calls == 0
    assert client.put('/api/admin/users', json={'email':'new@example.org','role':'editor'}).status_code == 200
    assert {space['id'] for space in client.get('/api/spaces', headers=headers).json()} == {'initial', sid}
    assert client.get('/api/management/spaces', headers=headers).json() == []


def test_archive_requires_current_name_and_preserves_other_space(setup):
    client, _, provider = setup
    sid = create_space(client, 'Для архива')
    assert assign(client, sid).status_code == 200
    cid = space_chat(client, sid)
    mid = send(client, cid).json()['message_id']
    audio = client.post(f'/api/chats/{cid}/messages/{mid}/speech', json={'request_id':str(uuid4())}).json()['url']
    path = f'/api/management/spaces/{sid}/archive'
    assert client.post(path, json={'name':'Для архива','revision':0}).status_code == 409
    assert client.post(path, json={'name':'Для архива','revision':1}).status_code == 200
    assert {space['id'] for space in client.get('/api/spaces').json()} == {'initial'}
    assert client.get(audio).status_code == 403
    assert client.get(f'/api/chats/{cid}/export').status_code == 403
    assert send(client, space_chat(client, 'initial')).status_code == 200
    assert provider.llm_calls == 2


def test_multiple_bases_and_web_share_answer_with_owner_citation_previews(web_setup):
    from test_rag import new_base, upload, wait_document
    client, app, provider = web_setup
    bids = []
    for index in range(2):
        bid = new_base(client, f'Base {index + 1}')
        doc = upload(client, bid, filename=f'education-{index + 1}.txt')['id']
        wait_document(client, bid, doc)
        bids.append(bid)
    path = '/api/management/spaces/initial/policy'
    loaded = client.get(path).json()['policy']
    loaded['base_ids'] = bids
    loaded['rag_enabled'] = True
    loaded['settings']['web_search_enabled'] = True
    assert client.put(path, json={'revision':1,'policy':loaded}).status_code == 200
    cid = space_chat(client, 'initial')
    seen = []
    original = app.state.rag.retrieve

    async def record(bid, *args, **kwargs):
        seen.append(bid)
        return await original(bid, *args, **kwargs)

    app.state.rag.retrieve = record
    response = send(client, cid, 'Education reform navigation school')
    assert response.status_code == 200, response.text
    assert seen == bids and provider.searches == 1
    assert 'citations' not in response.json()['metadata']
    answer = client.get(f'/api/chats/{cid}/messages').json()[-1]
    citations = answer['metadata']['citations']
    assert {source['type'] for source in citations} == {'rag', 'web'}
    assert all(set(source) == {'id','type','available','title','page'} for source in citations)
    previews = f"/api/chats/{cid}/messages/{answer['id']}/citations/"
    for source in citations:
        preview = client.get(previews + source['id']).json()
        assert preview['excerpt']
        assert not {'document_url','chunk_id','document_id','base_id'} & preview.keys()
        assert ('url' in preview) == (source['type'] == 'web')
    assert client.get(previews + 'D99').status_code == 404
    assert 'citations' in client.get(f'/api/admin/chats/{cid}/messages').json()['messages'][-1]['metadata']
    assert 'citations' not in client.get(f'/api/chats/{cid}/export?format=json').text
    rag_source = next(source for source in client.get(f'/api/admin/chats/{cid}/messages').json()['messages'][-1]['metadata']['citations'] if source['type']=='rag')
    assert client.post(f"/api/knowledge-bases/{rag_source['base_id']}/revoke").status_code == 200
    assert send(client, cid, 'Education reform navigation school').status_code == 409
    assert provider.searches == 1
    assert client.get(previews + 'D1').status_code == 410
    assert client.get(previews + 'W1').status_code == 200
    assert assign(client, 'initial', assigned=False).status_code == 200
    assert client.get(previews + 'W1').status_code == 403
    archived = client.get(f'/api/chats/{cid}/messages').json()[-1]
    assert 'citations' not in archived['metadata']
    assert '[D1]' not in archived['text']
    from test_app import switch_user
    switch_user(client, app, 'another')
    assert client.get(previews + 'W1').status_code == 404


def test_stream_projection_handles_split_citations_and_ordinary_links(setup):
    client, app, provider = setup
    cid = space_chat(client, 'initial')
    fragments = ['Answer [', 'D', '1]', ' and [an ordinary link](https://example.org). [W2]', '(https://example.org/source).']

    async def stream(messages, config, delta):
        for fragment in fragments:
            await delta(fragment)
        return ''.join(fragments), {}

    provider.complete_stream = stream
    response = client.post(f'/api/chats/{cid}/messages/stream', json={'text':'Projection','request_id':str(uuid4())})
    assert response.status_code == 200, response.text
    assert '[D1]' not in response.text
    assert '[W2]' not in response.text
    assert '[an ordinary link](https://example.org)' in response.text
    assert '[D1]' in app.state.store.messages(cid)[-1]['text']



def test_archive_cancels_only_operations_in_that_space(tmp_path):
    import asyncio
    import httpx
    from app.main import create_app
    from app.settings import Settings
    from test_app import CONFIG, FakeProviders

    class WaitingProvider(FakeProviders):
        def __init__(self):
            super().__init__()
            self.started = {question: asyncio.Event() for question in ('A', 'B')}
            self.complete_b = asyncio.Event()

        async def complete(self, messages, config):
            question = messages[-1]['text']
            self.started[question].set()
            await self.complete_b.wait()
            return 'Finished', {}

    async def scenario():
        provider = WaitingProvider()
        settings = Settings(environment='development', origin='http://localhost', demo=True,
                            data_dir=tmp_path, config_path=CONFIG)
        app = create_app(settings, provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.origin,
                                     headers={'Origin':settings.origin}) as client:
            assert (await client.post('/api/auth/demo')).status_code == 303
            client.headers['X-CSRF-Token'] = (await client.get('/api/me')).json()['csrf']
            sid = (await client.post('/api/management/spaces', json={'name':'B'})).json()['id']
            assigned = await client.put(f'/api/management/spaces/{sid}/assignments',
                                        json={'email':'demo@localhost','assigned':True})
            assert assigned.status_code == 200
            chats = {}
            pending = {}
            for space, question in [('initial','A'),(sid,'B')]:
                header = {'X-Space-ID':space}
                chats[question] = (await client.post('/api/chats', headers=header)).json()['id']
                pending[question] = asyncio.create_task(client.post(
                    f"/api/chats/{chats[question]}/messages", headers=header,
                    json={'text':question,'request_id':str(uuid4())}))
                await asyncio.wait_for(provider.started[question].wait(), 2)
            result = await client.post('/api/management/spaces/initial/archive',
                                       json={'name':'Основное пространство','revision':1})
            assert result.status_code == 200
            assert (await asyncio.wait_for(pending['A'], 2)).status_code == 409
            assert not pending['B'].done()
            provider.complete_b.set()
            assert (await asyncio.wait_for(pending['B'], 2)).status_code == 200
            assert app.state.store.messages(chats['A']) == []
            assert len(app.state.store.messages(chats['B'])) == 2

    asyncio.run(scenario())
