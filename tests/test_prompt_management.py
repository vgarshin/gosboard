from uuid import uuid4
from test_roles import roles


def test_prompt_crud_selection_and_next_response(roles):
    c,app,provider,access,login,s=roles
    login('user');cid=c.post('/api/chats').json()['id']
    for method,path,body in [('GET','/api/editor/prompts',None),('POST','/api/editor/prompts',{}),('PUT','/api/editor/prompts/default',{}),('DELETE','/api/editor/prompts/default',{})]:
        assert c.request(method,path,json=body).status_code==403
    login('editor');catalog=c.get('/api/editor/prompts').json();revision=catalog['revision']
    created=c.post('/api/editor/prompts',json={'expected_revision':revision,'label':'Education','system_prompt':'Original historical instruction'})
    assert created.status_code==200,created.text
    pid=created.json()['prompt_id'];revision=created.json()['revision']
    assert pid.startswith('prompt-')
    assert c.put(f'/api/editor/chats/{cid}/model',json={'model_id':'alice-flash','prompt_id':pid}).status_code==200
    assert c.get(f'/api/chats/{cid}/messages').status_code==404
    login('user')
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'Question','request_id':str(uuid4())}).status_code==200
    assert provider.completions==1
    old=app.state.store.messages(cid)[-1]
    config_hash=old['metadata']['config_hash']
    with app.state.store.connection() as db:
        assert 'Original historical instruction' in db.execute('SELECT snapshot FROM configurations WHERE hash=?',(config_hash,)).fetchone()['snapshot']
    login('editor')
    result=c.put('/api/editor/prompts/'+pid,json={'expected_revision':revision,'label':'Education updated','system_prompt':'Updated historical instruction'})
    assert result.status_code==200
    revision=result.json()['revision']
    assert c.request('DELETE','/api/editor/prompts/'+pid,json={'expected_revision':revision}).status_code==409
    assert next(p for p in c.get('/api/editor/prompts').json()['prompts'] if p['id']==pid)['used_by_chats']==1
    login('user')
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'Question','request_id':str(uuid4())}).status_code==200
    new=app.state.store.messages(cid)[-1]
    assert new['metadata']['config_hash']!=config_hash
    with app.state.store.connection() as db:
        assert 'Updated historical instruction' in db.execute('SELECT snapshot FROM configurations WHERE hash=?',(new['metadata']['config_hash'],)).fetchone()['snapshot']
    assert app.state.store.messages(cid)[1]==old
    login('editor')
    assert c.put(f'/api/editor/chats/{cid}/model',json={'model_id':'alice-flash','prompt_id':'default'}).status_code==200
    deleted=c.request('DELETE','/api/editor/prompts/'+pid,json={'expected_revision':revision})
    assert deleted.status_code==200
    assert all(p['id']!=pid for p in deleted.json()['prompts'])
    assert any(p['id']=='default' for p in deleted.json()['prompts'])


def test_prompt_validation_stale_versions_and_protected_default(roles):
    c,app,provider,access,login,s=roles
    login('admin');catalog=c.get('/api/editor/prompts').json();revision=catalog['revision']
    original=next(p for p in catalog['prompts'] if p['id']=='default')
    assert c.request('DELETE','/api/editor/prompts/default',json={'expected_revision':revision}).status_code==409
    assert c.post('/api/editor/prompts',json={'expected_revision':revision,'label':' ','system_prompt':' '}).status_code==422
    changed=c.put('/api/editor/prompts/default',json={'expected_revision':revision,'label':original['label'],'system_prompt':'Live default instructions'})
    assert changed.status_code==200
    assert c.get('/api/config').json()['version']==f'live-{revision+1}'
    assert c.post('/api/editor/prompts',json={'expected_revision':revision,'label':'Stale','system_prompt':'Must not overwrite'}).status_code==409
    latest=changed.json()['revision']
    assert c.put('/api/editor/prompts/default',json={'expected_revision':latest,'label':'Rename default','system_prompt':'Some text'}).status_code==422
    assert c.put('/api/editor/prompts/absent',json={'expected_revision':latest,'label':'Missing','system_prompt':'Text'}).status_code==404
    assert c.post('/api/editor/prompts',json={'expected_revision':latest,'label':'Bad field','system_prompt':'Text','api_key':'forbidden'}).status_code==422
