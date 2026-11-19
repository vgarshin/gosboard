def configure_space(client, sid='initial', *, base_ids=None, **settings):
    route = f'/api/management/spaces/{sid}/policy'
    response = client.get(route)
    assert response.status_code == 200, response.text
    policy = response.json()['policy']
    revision = next(space['revision'] for space in client.get('/api/management/spaces').json() if space['id'] == sid)
    policy['settings'].update(settings)
    if base_ids is not None:
        policy['base_ids'] = base_ids
        policy['rag_enabled'] = bool(base_ids)
    response = client.put(route, json={'revision':revision,'policy':policy})
    assert response.status_code == 200, response.text


def stored_answer(client, cid):
    response = client.get(f'/api/admin/chats/{cid}/messages')
    assert response.status_code == 200, response.text
    return response.json()['messages'][-1]


async def configure_space_async(client, sid='initial', *, headers=None, **settings):
    kwargs = {'headers': headers} if headers is not None else {}
    route = f'/api/management/spaces/{sid}/policy'
    policy = (await client.get(route, **kwargs)).json()['policy']
    spaces = (await client.get('/api/management/spaces', **kwargs)).json()
    revision = next(space['revision'] for space in spaces if space['id'] == sid)
    policy['settings'].update(settings)
    result = await client.put(route, json={'revision':revision,'policy':policy}, **kwargs)
    assert result.status_code == 200, result.text
