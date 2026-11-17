import json
import time
from uuid import uuid4

from test_roles import roles


def test_admin_read_all_messages_and_sources_without_owner_write_access(roles):
    c,app,p,access,login,s=roles
    login('user');cid=c.post('/api/chats').json()['id']
    source={'id':'W1','type':'web','source_id':'a'*64,'title':'Source','url':'https://example.org/source','excerpt':'Private saved evidence from an actual source.','content_kind':'search_snippet','fetched_at':1,'query':'Question'}
    app.state.store.add_turn(cid,'PRIVATE OWNER QUESTION','PRIVATE OWNER ANSWER [W1]',{'citations':[source]})
    before=app.state.store.usage()
    for role in ('user','editor'):
        login(role)
        assert c.get('/api/admin/chats').status_code==403
        assert c.get(f'/api/admin/chats/{cid}/messages').status_code==403
    login('admin')
    listing=c.get('/api/admin/chats').json();assert listing['total']==1
    assert listing['items'][0]['email']=='user@example.com'
    result=c.get(f'/api/admin/chats/{cid}/messages')
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['messages'][0]['text']=='PRIVATE OWNER QUESTION'
    assert result.json()['messages'][1]['metadata']['citations'][0]['excerpt']==source['excerpt']
    assert c.get(f'/api/chats/{cid}/messages').status_code==404
    assert c.post(f'/api/chats/{cid}/messages',json={'text':'No write','request_id':str(uuid4())}).status_code==404
    assert c.delete(f'/api/chats/{cid}').status_code==404
    assert c.get(f'/api/chats/{cid}/export').status_code==404
    assert c.get('/api/web-sources/'+'a'*64).status_code==404
    event=next(event for event in c.get('/api/admin/audit').json() if event['action']=='chat.read')
    assert event['target']==cid and json.loads(event['details'])=={'owner_id':'user','message_count':2}
    assert 'PRIVATE OWNER' not in json.dumps(event)
    assert app.state.store.usage()==before
    assert c.get(f'/api/admin/chats/{uuid4()}/messages').status_code==404


def test_admin_list_pagination_beyond_500_and_owner_filter(roles):
    c,app,p,access,login,s=roles
    now=time.time()
    with app.state.store.connection() as db:
        rows=[(str(uuid4()),'user',f'Chat {i}',now,now+i) for i in range(505)]
        db.executemany('INSERT INTO chats VALUES (?,?,?,?,?)',rows)
        db.execute('INSERT INTO chats VALUES (?,?,?,?,?)',(str(uuid4()),'editor','Editor chat',now,now))
    login('admin')
    listing=c.get('/api/admin/chats?limit=100&offset=500').json()
    assert listing['total']==506 and len(listing['items'])==6
    assert len({chat['id'] for chat in listing['items']})==6
    filtered=c.get('/api/admin/chats?owner=editor').json()
    assert filtered['total']==1 and filtered['items'][0]['title']=='Editor chat'
    assert {owner['email'] for owner in filtered['owners']}=={'user@example.com','editor@example.com'}
    assert c.get('/api/admin/chats?owner=unknown').json()['total']==0
    for query in ('limit=101','offset=-1','limit=0'):
        assert c.get('/api/admin/chats?'+query).status_code==422


def test_disabled_owner_is_visible_and_demoted_admin_loses_access(roles):
    c,app,p,access,login,s=roles
    login('user');cid=c.post('/api/chats').json()['id']
    app.state.store.add_turn(cid,'Historical question','Historical answer',{})
    login('admin')
    assert c.put('/api/admin/users',json={'email':'user@example.com','role':'user','enabled':False}).status_code==200
    assert c.get(f'/api/admin/chats/{cid}/messages').status_code==200
    assert c.post('/api/admin/users',json={'email':'backup@example.com','role':'admin'}).status_code==200
    app.state.store.user('backup','backup@example.com','Backup')
    assert c.put('/api/admin/users',json={'email':'admin@example.com','role':'editor'}).status_code==200
    assert c.get('/api/admin/chats').status_code==401
    assert c.get(f'/api/admin/chats/{cid}/messages').status_code==401
    login('admin')
    assert c.get('/api/admin/chats').status_code==403


def test_admin_reads_saved_history_while_owner_generation_is_running(tmp_path):
    import asyncio
    import httpx
    from app.main import create_app
    from test_app import live_settings,FakeProviders
    async def scenario():
        class Waiting(FakeProviders):
            def __init__(self):super().__init__();self.started=asyncio.Event();self.finish=asyncio.Event()
            async def complete(self,messages,config):
                self.started.set();await self.finish.wait();return 'New answer',{}
        s=live_settings(tmp_path);p=Waiting();app=create_app(s,p);store=app.state.store
        store.user('user','user@example.com','User');store.user('admin','admin@example.com','Admin')
        user_token=store.session('user',3600);admin_token=store.session('admin',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':user_token},headers={'Origin':s.origin,'X-CSRF-Token':store.auth(user_token)['csrf']}) as owner:
            cid=(await owner.post('/api/chats')).json()['id'];store.add_turn(cid,'Saved question','Saved answer',{})
            pending=asyncio.create_task(owner.post(f'/api/chats/{cid}/messages',json={'text':'New question','request_id':str(uuid4())}))
            await asyncio.wait_for(p.started.wait(),2)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=s.origin,cookies={'gosboard_session':admin_token}) as admin:
                    response=await asyncio.wait_for(admin.get(f'/api/admin/chats/{cid}/messages'),2)
                    assert response.status_code==200 and len(response.json()['messages'])==2
                    assert not pending.done()
            finally:p.finish.set();await pending
    asyncio.run(scenario())
