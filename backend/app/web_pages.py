"""Bounded public-page text retrieval with DNS-pinned sockets and no provider credentials."""
import asyncio
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from .store import cache_key, operation_space

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 40000
PAGE_TIMEOUT = 6


def is_public_address(value):
    ip=ipaddress.ip_address(value)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:return False
    if isinstance(ip,ipaddress.IPv6Address):
        if ip.sixtofour or ip.teredo or ip.ipv4_mapped:return False
        if ip in ipaddress.ip_network('64:ff9b::/96') or ip in ipaddress.ip_network('64:ff9b:1::/48'):return False
    return True


class PageFetchError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class PublicNetwork(httpcore.AsyncNetworkBackend):
    """Resolve once, reject every non-public answer, connect only to the validated IP.

    HTTPCore retains the original origin for Host, TLS SNI and certificate verification.
    """
    def __init__(self, blocked_hosts=(), resolver=None, backend=None):
        self.blocked_hosts = {host.rstrip('.').lower() for host in blocked_hosts}
        self.resolver, self.backend = resolver, backend or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        from .web_search import safe_web_url
        if port not in {80,443} or host.rstrip('.').lower() in self.blocked_hosts or not safe_web_url(f'https://[{host}]/' if ':' in host else f'https://{host}/'):
            raise PageFetchError('blocked_address')
        async with asyncio.timeout(min(timeout or 3,3)):
            lookup = self.resolver or asyncio.get_running_loop().getaddrinfo
            addresses = await lookup(host,port,type=socket.SOCK_STREAM)
            ips = list(dict.fromkeys(item[4][0] for item in addresses))
            if not ips or any(not is_public_address(ip) for ip in ips):
                raise PageFetchError('blocked_address')
            last = None
            for ip in ips[:4]:
                try:
                    return await self.backend.connect_tcp(ip,port,timeout=timeout,local_address=local_address,socket_options=socket_options)
                except (OSError,httpcore.ConnectError,httpcore.ConnectTimeout) as error:
                    last = error
            raise PageFetchError('connection_error') from last

    async def connect_unix_socket(self, *args, **kwargs):
        raise PageFetchError('blocked_address')

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class PageText(HTMLParser):
    BLOCKS = {'p','div','section','article','main','li','h1','h2','h3','h4','tr','br','blockquote'}
    SKIP = {'script','style','noscript','svg','iframe','form','nav','footer','header','aside','template','canvas'}
    VOID = {'br','hr','img','input','meta','link','source','wbr','area','base','embed','param','track','col'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.parts=[];self.blocks=[];self.count=0;self.title=[]

    def flush(self):
        text=' '.join(''.join(self.parts).split());self.parts=[]
        if text and self.count<MAX_TEXT:
            text=text[:MAX_TEXT-self.count];self.blocks.append(text);self.count+=len(text)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        blocked=any(skip for _,skip in self.stack) or tag in self.SKIP or 'hidden' in attrs or attrs.get('aria-hidden')=='true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',attrs.get('style') or '',re.I))
        if tag in self.BLOCKS and not blocked:self.flush()
        if tag not in self.VOID:
            if len(self.stack)>=128:raise PageFetchError('invalid_html')
            self.stack.append((tag,blocked))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.BLOCKS:self.flush()
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:];break

    def handle_data(self, data):
        if any(skip for _,skip in self.stack) or self.count>=MAX_TEXT:return
        if any(tag=='title' for tag,_ in self.stack):
            self.title.append(data[:180]);return
        self.parts.append(data)


def extract_page(raw, content_type):
    charset=re.search(r'charset\s*=\s*["\']?([\w-]+)',content_type,re.I)
    if not charset:charset=re.search(br'charset\s*=\s*["\']?([\w-]+)',raw[:4096],re.I)
    encoding=charset[1] if charset else 'utf-8'
    if isinstance(encoding,bytes):encoding=encoding.decode('ascii','ignore')
    encoding=encoding.lower()
    if encoding not in {'utf-8','utf8','windows-1251','cp1251','koi8-r','iso-8859-1'}:encoding='utf-8'
    text=raw.decode(encoding,errors='replace')
    if content_type.startswith('text/plain'):
        blocks=[' '.join(line.split()) for line in text[:MAX_TEXT].splitlines() if line.strip()]
        return '',blocks
    parser=PageText()
    try:
        parser.feed(text);parser.close();parser.flush()
    except AssertionError:
        raise PageFetchError('invalid_html') from None
    return ' '.join(''.join(parser.title).split())[:180],parser.blocks


def select_passages(blocks, question, limit):
    terms={word[:6] for word in re.findall(r'\w{3,}',question.casefold())}
    chunks=[];pending=[]
    for block in blocks:
        if len(block)<80:
            pending.append(block)
            if len(' '.join(pending))<80:continue
            block=' '.join(pending);pending=[]
        elif pending:
            block=' '.join(pending)+' '+block;pending=[]
        for offset in range(0,len(block),1000):
            chunk=block[offset:offset+1000]
            if len(chunk)>=40:chunks.append(chunk)
    if pending and len(' '.join(pending))>=40:chunks.append(' '.join(pending))
    if not chunks:return ''
    ranked=sorted(range(len(chunks)),key=lambda i:(-len(terms & {word[:6] for word in re.findall(r'\w{3,}',chunks[i].casefold())}),i))
    chosen=[];used=0
    for index in ranked:
        if used>=limit:break
        chunk=chunks[index][:limit-used]
        if len(chunk)<40:continue
        chosen.append((index,chunk));used+=len(chunk)+5
        if len(chosen)==3:break
    return '\n\n…\n\n'.join(chunk for _,chunk in sorted(chosen))[:limit]


class PageFetcher:
    def __init__(self, settings, store, pool=None):
        self.settings,self.store=settings,store
        host=urlsplit(settings.origin).hostname or ''
        self.blocked_hosts={host,'www.'+host}
        self.pool=pool or httpcore.AsyncConnectionPool(network_backend=PublicNetwork(self.blocked_hosts),max_connections=2,max_keepalive_connections=2,retries=0)
        self.slots=asyncio.Semaphore(2)

    async def close(self):
        await self.pool.aclose()

    async def fetch(self, url, uid):
        started=time.monotonic();key=cache_key('web-page-v2',operation_space.get(),uid,url)
        cached=self.store.get_cache(key,uid,'web_page')
        if cached is not None:return {**json.loads(cached),'cached':True}
        try:
            async with asyncio.timeout(PAGE_TIMEOUT):
                async with self.slots:
                    result=await self._download(url)
            result={**result,'status':'loaded','fetched_at':time.time()}
        except asyncio.CancelledError:
            raise
        except PageFetchError as error:
            result={'status':error.reason,'fetched_at':time.time()}
        except (TimeoutError,httpcore.TimeoutException):
            result={'status':'timeout','fetched_at':time.time()}
        except (OSError,ValueError,httpx.InvalidURL,httpcore.NetworkError,httpcore.ProtocolError):
            result={'status':'unavailable','fetched_at':time.time()}
        if result.get('cacheable',True):
            self.store.put_cache(key,uid,'web_page',json.dumps(result,ensure_ascii=False).encode(),result.get('cache_ttl',self.settings.web_page_ttl) if result['status']=='loaded' else 60)
        if getattr(self.store,'metrics',None):
            self.store.metrics.observe('fetch','web-page',(time.monotonic()-started)*1000,result['status'])
        return {**result,'cached':False}

    async def _download(self, url):
        from .web_search import safe_web_url
        current=url
        for hop in range(4):
            if not safe_web_url(current) or (urlsplit(current).hostname or '').rstrip('.').lower() in self.blocked_hosts:
                raise PageFetchError('blocked_address')
            normalized=httpx.URL(current).copy_with(fragment=None)
            target=httpcore.URL(scheme=normalized.raw_scheme,host=normalized.raw_host,port=normalized.port,target=normalized.raw_path)
            # No cookies, Authorization, Referer, environment proxies or automatic redirects.
            async with self.pool.stream('GET',target,headers={'User-Agent':'AI-Talks/1.0','Accept':'text/html,application/xhtml+xml,text/plain','Accept-Encoding':'identity'},extensions={'timeout':{'connect':3,'read':3,'write':3,'pool':1}}) as response:
                headers={key.decode('ascii','ignore').lower():value.decode('latin-1') for key,value in response.headers}
                if response.status in {301,302,303,307,308}:
                    if hop==3 or not headers.get('location'):raise PageFetchError('redirect_limit')
                    current=urljoin(str(normalized),headers['location']);continue
                if response.status!=200:raise PageFetchError('http_error')
                content_type=headers.get('content-type','').lower()
                if content_type.split(';')[0].strip() not in {'text/html','application/xhtml+xml','text/plain'}:raise PageFetchError('unsupported_type')
                if headers.get('content-encoding','identity').strip().lower() not in {'','identity'}:raise PageFetchError('unsupported_encoding')
                size=headers.get('content-length','')
                if size.isdigit() and int(size)>MAX_BYTES:raise PageFetchError('too_large')
                data=bytearray()
                async for block in response.aiter_stream():
                    data.extend(block)
                    if len(data)>MAX_BYTES:raise PageFetchError('too_large')
                title,blocks=await asyncio.to_thread(extract_page,bytes(data),content_type)
                if sum(map(len,blocks))<160:raise PageFetchError('insufficient_text')
                control=headers.get('cache-control','').lower()
                age=re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)',control)
                ttl=min(self.settings.web_page_ttl,int(age[1])) if age and len(age[1])<=10 else self.settings.web_page_ttl
                return {'url':str(normalized),'title':title,'blocks':blocks,'content_hash':hashlib.sha256('\n'.join(blocks).encode()).hexdigest(),'cacheable':not any(value in control for value in ('no-store','no-cache')) and ttl>0,'cache_ttl':ttl}
        raise PageFetchError('redirect_limit')
