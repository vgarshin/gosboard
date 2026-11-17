import json
from uuid import uuid4
from test_roles import roles


def test_owner_exports_three_formats_with_sources_and_no_provider_calls(roles):
    c,app,p,access,login,s=roles
    login('user');cid=c.post('/api/chats').json()['id']
    app.state.store.add_turn(cid,'Привет','Исторический ответ [D1] [W1]',{'citations':[{'id':'D1','title':'Книга','page':3,'url':'/sources/'+'a'*64,'excerpt':'Сохранённый фрагмент','chunk_id':'a'*64},{'id':'W1','type':'web','title':'Веб-страница','url':'https://example.org/source','excerpt':'Поисковый фрагмент'}]})
    before=app.state.store.usage()
    for format,mime in [('md','text/markdown'),('txt','text/plain'),('json','application/json')]:
        r=c.get(f'/api/chats/{cid}/export?format={format}')
        assert r.status_code==200,r.text
        assert r.headers['content-type'].startswith(mime)
        assert r.headers['content-disposition']==f'attachment; filename="aitalks-{cid}.{format}"'
        assert r.headers['cache-control']=='no-store'
        assert 'Привет' in r.text and 'Сохранённый фрагмент' in r.text
        assert 'https://example.org/source' in r.text
        if format=='json':
            data=r.json();assert data['schema_version']==1 and len(data['messages'])==2
            assert 'user_id' not in data['chat']
        else:assert s.origin+'/sources/'+'a'*64 in r.text
    assert app.state.store.usage()==before
    assert c.get(f'/api/chats/{cid}/export?format=html').status_code==422
    for role in ('editor','admin'):
        login(role)
        assert c.get(f'/api/chats/{cid}/export').status_code==404
    c.cookies.clear();assert c.get(f'/api/chats/{cid}/export').status_code==401


def test_empty_and_deleted_chat_exports(roles):
    c,app,p,access,login,s=roles
    login('user');cid=c.post('/api/chats').json()['id']
    assert c.get(f'/api/chats/{cid}/export?format=json').json()['messages']==[]
    assert c.delete(f'/api/chats/{cid}').status_code==200
    assert c.get(f'/api/chats/{cid}/export').status_code==404
