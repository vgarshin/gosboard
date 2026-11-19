from uuid import uuid4
from test_roles import roles


def test_space_prompt_crud_and_snapshots(roles):
    c,app,provider,access,login,s=roles
    login('admin')
    route='/api/management/spaces/initial/policy'
    policy=c.get(route).json()['policy']
    item={'id':'education','label':'Education','system_prompt':'Original historical instruction'}
    policy.update(prompts=[item],prompt_id=item['id'])
    assert c.put(route,json={'revision':1,'policy':policy}).status_code==200
    login('user')
    assert c.get(route).status_code==403
    old=c.post('/api/chats').json()['id']
    login('admin')
    item['system_prompt']='Updated historical instruction'
    assert c.put(route,json={'revision':2,'policy':policy}).status_code==200
    assert c.put(route,json={'revision':2,'policy':policy}).status_code==409
    login('user')
    new=c.post('/api/chats').json()['id']
    login('admin')
    policy.update(prompts=[],prompt_id='default')
    assert c.put(route,json={'revision':3,'policy':policy}).status_code==200
    login('user')
    for cid, expected in [(old,'Original historical instruction'),(new,'Updated historical instruction')]:
        assert c.post(f'/api/chats/{cid}/messages',json={'text':'Question','request_id':str(uuid4())}).status_code==200
        metadata=app.state.store.messages(cid)[-1]['metadata']
        with app.state.store.connection() as db:
            assert expected in db.execute('SELECT snapshot FROM configurations WHERE hash=?',(metadata['config_hash'],)).fetchone()['snapshot']


def test_prompt_validation_stale_versions_and_protected_default(roles):
    c,app,provider,access,login,s=roles
    login('admin');catalog=c.get('/api/editor/prompts').json();revision=catalog['revision']
    original=next(p for p in catalog['prompts'] if p['id']=='default')
    assert c.request('DELETE','/api/editor/prompts/default',json={'expected_revision':revision}).status_code==409
    assert c.post('/api/editor/prompts',json={'expected_revision':revision,'label':' ','system_prompt':' '}).status_code==422
    changed=c.put('/api/editor/prompts/default',json={'expected_revision':revision,'label':original['label'],'system_prompt':'Live default instructions'})
    assert changed.status_code==200
    assert c.get('/api/admin/config').json()['config']['version']==f'live-{revision+1}'
    assert c.post('/api/editor/prompts',json={'expected_revision':revision,'label':'Stale','system_prompt':'Must not overwrite'}).status_code==409
    latest=changed.json()['revision']
    assert c.put('/api/editor/prompts/default',json={'expected_revision':latest,'label':'Rename default','system_prompt':'Some text'}).status_code==422
    assert c.put('/api/editor/prompts/absent',json={'expected_revision':latest,'label':'Missing','system_prompt':'Text'}).status_code==404
    assert c.post('/api/editor/prompts',json={'expected_revision':latest,'label':'Bad field','system_prompt':'Text','api_key':'forbidden'}).status_code==422
