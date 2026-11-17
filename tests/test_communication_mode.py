from uuid import uuid4
from test_roles import roles
from test_app import setup,create_chat


def test_mode_preferences_owned_and_editor_policy_preserves_them(roles):
    c,app,p,access,login,s=roles;login('user');cid=create_chat(c)
    r=c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'voice','review_before_send':True})
    assert r.status_code==200,r.text
    settings=r.json()['settings'];assert settings['streaming_enabled'] and settings['auto_speech'] and settings['review_before_send']
    assert c.get('/api/chats').json()[0]['settings']==settings
    login('editor')
    result=c.put(f'/api/editor/chats/{cid}/model',json={'model_id':'alice-flash'})
    assert result.status_code==200
    assert all(result.json()['settings'][key]==settings[key] for key in ('communication_mode','review_before_send','streaming_enabled','auto_speech'))
    assert c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'text'}).status_code==404
    login('user')
    result=c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'text','streaming_enabled':True})
    assert result.status_code==200 and not result.json()['settings']['auto_speech']
    result=c.patch(f'/api/chats/{cid}/sources',json={'streaming_enabled':False})
    assert not result.json()['settings']['streaming_enabled']
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'Classic mode','request_id':str(uuid4())}).status_code==200
    assert c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'unknown'}).status_code==422
    assert c.patch(f'/api/chats/{cid}/sources',json={'history_messages':0}).status_code==422


def test_modes_and_review_setting_share_answer_cache(setup):
    c,app,p=setup
    a=create_chat(c)
    body={'text':'Same question','request_id':str(uuid4())}
    assert c.post(f'/api/chats/{a}/messages',json=body).status_code==200
    b=create_chat(c)
    assert c.patch(f'/api/chats/{b}/sources',json={'communication_mode':'voice','review_before_send':True}).status_code==200
    result=c.post(f'/api/chats/{b}/messages/stream',json={**body,'request_id':str(uuid4())})
    assert result.status_code==200 and '"cached": true' in result.text
    assert p.llm_calls==1


def test_live_mode_preferences_are_owned_and_preserved_by_editor(roles):
    c,app,p,access,login,s=roles;login('user');cid=create_chat(c)
    r=c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'live','live_pause':'long','streaming_enabled':False,'auto_speech':False})
    assert r.status_code==200,r.text
    settings=r.json()['settings'];assert settings['communication_mode']=='live' and settings['live_pause']=='long'
    assert settings['streaming_enabled'] and settings['auto_speech']
    assert c.patch(f'/api/chats/{cid}/sources',json={'live_pause':'arbitrary'}).status_code==422
    login('editor');r=c.put(f'/api/editor/chats/{cid}/model',json={'voice':'filipp'})
    assert r.status_code==200
    assert r.json()['settings']['communication_mode']=='live' and r.json()['settings']['live_pause']=='long'
    assert c.patch(f'/api/chats/{cid}/sources',json={'communication_mode':'text'}).status_code==404
    login('user');assert c.get('/api/chats').json()[0]['settings']['live_pause']=='long'


def test_live_pause_does_not_change_answer_cache_identity(setup):
    c,app,p=setup
    for pause in ('normal','long'):
        cid=c.post('/api/chats',json={'communication_mode':'live','live_pause':pause}).json()['id']
        r=c.post(f'/api/chats/{cid}/messages/stream',json={'text':'Same live question','request_id':str(uuid4())})
        assert r.status_code==200 and '"event": "done"' in r.text
        if pause=='long':assert '"cached": true' in r.text
    assert p.llm_calls==1
