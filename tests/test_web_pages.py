from space_helpers import stored_answer, configure_space_async
import asyncio
import json
import socket
import ssl
import time
from uuid import uuid4

import httpcore
import httpx
import pytest

from app.web_pages import (PageFetcher,PublicNetwork,PageFetchError,extract_page,select_passages,is_public_address,MAX_BYTES)
from app.main import create_app
from app.settings import Settings
from app.store import Store
from test_app import live_settings,CONFIG
from test_web_search import web_setup,new_chat,SearchProvider
from test_rag import ready_base,ask

TEXT='Navigation school taught mathematics, geometry and astronomy. This full article includes the rare detail SEXTANT-1701 that is absent from search snippets. '*5


def response(body=None,status=200,headers=None):
    body=(body if body is not None else '<html><head><title>Navigation school</title></head><body><article><p>'+TEXT+'</p></article></body></html>').encode()
    defaults={'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)),'Connection':'close'}
    defaults.update(headers or {})
    return f'HTTP/1.1 {status} Status\r\n'.encode()+b''.join(f'{k}: {v}\r\n'.encode() for k,v in defaults.items() if v is not None)+b'\r\n'+body


class RecordingStream(httpcore.AsyncMockStream):
    def __init__(self,raw,record):super().__init__([raw]);self.record=record
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        self.record['tls'].append((server_hostname,ssl_context.verify_mode,ssl_context.check_hostname));return self
    async def write(self,buffer,timeout=None):self.record['requests'].append(buffer)


class Backend:
    def __init__(self,responses):self.responses=responses;self.record={'hosts':[],'tls':[],'requests':[]};self.index=0
    async def connect_tcp(self,host,port,**kwargs):
        self.record['hosts'].append(host);raw=self.responses[min(self.index,len(self.responses)-1)];self.index+=1
        return RecordingStream(raw,self.record)


def fetcher(tmp_path,responses,dns=None):
    s=live_settings(tmp_path);s.web_pages=True;store=Store(tmp_path)
    calls=[]
    async def resolve(host,port,**kwargs):
        calls.append(host)
        ips=dns(host) if dns else ['8.8.8.8']
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in ips]
    backend=Backend(responses)
    pool=httpcore.AsyncConnectionPool(network_backend=PublicNetwork({'aitalks.tech'},resolve,backend),max_keepalive_connections=0)
    return PageFetcher(s,store,pool),store,backend,calls


def test_dns_pin_tls_host_no_credentials_and_private_page_cache(tmp_path,monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1234')
    async def scenario():
        page,store,backend,dns=fetcher(tmp_path,[response()])
        try:
            first=await page.fetch('https://news.example/article','u')
            assert first['status']=='loaded' and first['title']=='Navigation school'
            assert not first['cached'] and 'SEXTANT-1701' in ' '.join(first['blocks'])
            second=await page.fetch('https://news.example/article','u')
            assert second['cached'] and backend.record['hosts']==['8.8.8.8']
            assert backend.record['tls']==[('news.example',ssl.CERT_REQUIRED,True)]
            wire=b''.join(backend.record['requests']).lower()
            assert b'host: news.example' in wire
            assert all(header not in wire for header in (b'authorization:',b'cookie:',b'referer:'))
            await page.fetch('https://news.example/article','another-user')
            assert len(backend.record['hosts'])==2
            with store.connection() as db:db.execute("UPDATE cache SET expires=0 WHERE kind='web_page'")
            third=await page.fetch('https://news.example/article','u')
            assert not third['cached'] and third['fetched_at']!=first['fetched_at']
            assert dns==['news.example']*3
        finally:await page.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('ips',[['127.0.0.1'],['169.254.169.254'],['8.8.8.8','10.0.0.1'],['::1'],['100.100.100.200'],['224.0.0.1'],['64:ff9b::a9fe:a9fe']])
def test_nonpublic_or_mixed_dns_never_connects(tmp_path,ips):
    async def scenario():
        page,_,backend,_=fetcher(tmp_path,[response()],lambda _:ips)
        try:
            result=await page.fetch('https://news.example/article','u')
            assert result['status']=='blocked_address'
            assert not backend.record['hosts']
        finally:await page.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('target',['http://127.0.0.1/private','http://169.254.169.254/latest','https://user:pass@public.example/x','https://aitalks.tech/api/me','file:///etc/passwd','https://evil.example/'])
def test_every_redirect_is_checked(tmp_path,target):
    async def scenario():
        page,_,backend,_=fetcher(tmp_path,[response(status=302,headers={'Location':target})],lambda host:['127.0.0.1'] if host=='evil.example' else ['8.8.8.8'])
        try:
            result=await page.fetch('https://news.example/article','u')
            assert result['status']=='blocked_address'
            assert len(backend.record['hosts'])==1
        finally:await page.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('raw,status',[
    (response(status=403),'http_error'),
    (response(headers={'Content-Type':'application/pdf'}),'unsupported_type'),
    (response(headers={'Content-Encoding':'gzip'}),'unsupported_encoding'),
    (response(headers={'Content-Length':str(MAX_BYTES+1)}),'too_large'),
    (response('x'*(MAX_BYTES+1),headers={'Content-Length':None}),'too_large'),
    (response('<html>Enable JavaScript</html>'),'insufficient_text'),
    (response('<div>'*130+TEXT),'invalid_html'),
    (response(status=302,headers={'Location':'/again'}),'redirect_limit'),
],ids=['http-status','pdf','gzip','content-length','stream-size','short-content','deep-html','redirect-loop'])
def test_response_limits_and_types(tmp_path,raw,status):
    async def scenario():
        page,_,backend,_=fetcher(tmp_path,[raw])
        try:assert (await page.fetch('https://news.example/article','u'))['status']==status
        finally:await page.close()
    asyncio.run(scenario())


def test_html_extraction_and_relevant_passages():
    html='<html><title>Статья</title><nav>BAD NAV</nav><article><p>'+('Математика и навигация преподавались в школе. '*8)+'</p><script>BAD SCRIPT</script><p hidden>BAD HIDDEN</p><p style="display:none">BAD CSS</p></article><footer>BAD FOOTER</footer></html>'
    title,blocks=extract_page(html.encode('cp1251'),'text/html;charset=windows-1251')
    assert title=='Статья' and 'Математика' in ' '.join(blocks)
    assert 'BAD' not in ' '.join(blocks)
    selected=select_passages(['Unrelated introduction. '*20,'Astronomy and navigation use the sextant. '*20],'sextant astronomy',300)
    assert 'sextant' in selected and len(selected)<=300


@pytest.mark.parametrize('control',['no-store','no-cache','max-age=0'])
def test_deadline_and_no_store_cache_control(tmp_path,monkeypatch,control):
    async def scenario():
        page,store,backend,_=fetcher(tmp_path,[response(headers={'Cache-Control':control})])
        try:
            await page.fetch('https://news.example/article','u');await page.fetch('https://news.example/article','u')
            assert len(backend.record['hosts'])==2
            async def never(_):await asyncio.Event().wait()
            page._download=never
            monkeypatch.setattr('app.web_pages.PAGE_TIMEOUT',.01)
            result=await page.fetch('https://news.example/timeout','u')
            assert result['status']=='timeout'
        finally:await page.close()
    asyncio.run(scenario())


class FakePages:
    def __init__(self,failure=False):self.calls=[];self.failure=failure;self.timestamp=123
    async def fetch(self,url,uid):
        self.calls.append((url,uid))
        if self.failure:return {'status':'timeout','fetched_at':123,'cached':False}
        return {'status':'loaded','url':url+'/final','title':'Full article','blocks':[TEXT],'fetched_at':self.timestamp,'content_hash':'body-hash','cached':False}
    async def close(self):pass


@pytest.mark.parametrize('rag',[False,True])
def test_page_text_reaches_grounded_answer_and_survives_cache_expiry(web_setup,rag):
    c,app,p=web_setup;pages=FakePages();app.state.web.pages=pages
    bid=ready_base(c)[0] if rag else None;cid=new_chat(c,rag,True,bid)
    result=ask(c,cid);assert result.status_code==200,result.text
    meta=stored_answer(c,cid)['metadata'];web=next(s for s in meta['citations'] if s['type']=='web')
    assert web['content_kind']=='page_excerpt' and web['page_status']=='loaded'
    assert 'SEXTANT-1701' in app.state.store.messages(cid)[-1]['text']
    assert meta['retrieval']['web']['page_fetch']['loaded']==2
    assert len(pages.calls)==2
    with app.state.store.connection() as db:db.execute('DELETE FROM cache')
    preview=c.get('/api/web-sources/'+web['source_id'])
    assert preview.status_code==200 and preview.json()['excerpt']==web['excerpt']
    token=c.cookies.get('gosboard_session');app.state.store.user('other','demo@localhost','Other')
    c.cookies.clear();c.cookies.set('gosboard_session',app.state.store.session('other',3600))
    assert c.get('/api/web-sources/'+web['source_id']).status_code==404
    c.cookies.clear();c.cookies.set('gosboard_session',token)
    assert c.delete('/api/chats/'+cid).status_code==200
    assert c.get('/api/web-sources/'+web['source_id']).status_code==404


def test_failed_page_fetch_is_labelled_snippet_and_internet_off_never_fetches(web_setup):
    c,app,p=web_setup;pages=FakePages(True);app.state.web.pages=pages
    cid=new_chat(c);assert ask(c,cid).status_code==200 and pages.calls==[]
    cid=new_chat(c,web=True);result=ask(c,cid)
    assert result.status_code==200,result.text
    meta=stored_answer(c,cid)['metadata'];source=meta['citations'][0]
    assert source['content_kind']=='search_snippet' and source['page_status']=='timeout'
    assert meta['retrieval']['web']['page_fetch']['fallback']==2


def test_refreshed_page_invalidates_answer_even_with_cached_search(web_setup):
    c,app,p=web_setup;pages=FakePages();app.state.web.pages=pages
    first_id=new_chat(c,web=True);first=ask(c,first_id);assert first.status_code==200
    source=stored_answer(c,first_id)['metadata']['citations'][0]
    second=ask(c,new_chat(c,web=True))
    assert second.json()['metadata']['cached'] and p.completions==1
    pages.timestamp+=1
    third_id=new_chat(c,web=True);third=ask(c,third_id)
    assert third.status_code==200 and not third.json()['metadata']['cached'] and p.completions==2
    assert stored_answer(c,third_id)['metadata']['retrieval']['web']['cached']
    assert c.get('/api/web-sources/'+source['source_id']).json()['page_fetched_at']==123


def test_cancel_during_page_reads_cancels_all_children(tmp_path):
    async def scenario():
        started=asyncio.Event()
        class WaitingPages:
            def __init__(self):self.active=0
            async def fetch(self,url,uid):
                self.active+=1
                if self.active==2:started.set()
                try:await asyncio.Event().wait()
                finally:self.active-=1
            async def close(self):pass
        pages=WaitingPages();p=SearchProvider()
        settings=Settings(environment='development',origin='http://localhost',demo=True,web_enabled=True,data_dir=tmp_path,config_path=CONFIG)
        app=create_app(settings,p);app.state.web.pages=pages;store=app.state.store
        store.user('u','demo@localhost','Demo');token=store.session('u',3600)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.origin,cookies={'gosboard_session':token},headers={'Origin':settings.origin,'X-CSRF-Token':store.auth(token)['csrf']}) as c:
            await configure_space_async(c, web_search_enabled=True)
            cid=(await c.post('/api/chats')).json()['id'];rid=str(uuid4())
            pending=asyncio.create_task(c.post(f'/api/chats/{cid}/messages',json={'text':'Education reform','request_id':rid}))
            await asyncio.wait_for(started.wait(),2)
            assert (await c.post(f'/api/requests/{rid}/cancel')).json()['cancel_requested']
            assert (await pending).status_code==409
            assert pages.active==0 and p.completions==0
            assert store.messages(cid)==[]
        await p.close()
    asyncio.run(scenario())
