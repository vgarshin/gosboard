"""Bounded Yandex Search API snippets: private caches, budgeted calls, durable citations."""
import asyncio
import base64
import json
import re
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from fastapi import HTTPException
from .store import cache_key
from .web_pages import PageFetcher, select_passages, is_public_address

SEARCH_URL = 'https://searchapi.api.cloud.yandex.net/v2/web/search'
WEB_UNSUPPORTED = 'В найденных источниках недостаточно сведений, чтобы ответить на этот вопрос.'


def safe_web_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(char)<=32 for char in value):
        return False
    try:
        parsed=urlsplit(value)
        host=parsed.hostname or ''
        if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password:
            return False
        if host=='localhost' or host.endswith(('.localhost','.local','.internal')) or '.' not in host:
            return False
        try:
            if not is_public_address(host):return False
        except ValueError:
            pass
        return parsed.port in {None,80,443}
    except ValueError:
        return False


def parse_search_response(payload):
    try:
        encoded=payload['rawData']
        if not isinstance(encoded,str) or len(encoded)>3*1024*1024:raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>2*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError()
        root=ET.fromstring(raw)
        if root.tag!='yandexsearch' or root.find('.//error') is not None:raise ValueError()
        results=[];seen=set()
        def text(node):return ' '.join(''.join(node.itertext()).split()) if node is not None else ''
        for doc in root.findall('.//doc')[:30]:
            url=text(doc.find('url'))
            if not safe_web_url(url) or url in seen:continue
            title=text(doc.find('title'))[:180]
            passages=[text(item) for item in doc.findall('.//passage')]
            excerpt=' '.join(item for item in passages if item).strip()[:900]
            if not excerpt:excerpt=text(doc.find('headline'))[:900]
            if not title or not excerpt:continue
            results.append({'title':title,'url':url,'excerpt':excerpt});seen.add(url)
            if len(results)==5:break
        return results
    except (KeyError,TypeError,ValueError,ET.ParseError):
        raise HTTPException(502,'Поиск вернул некорректный ответ. Попробуйте позже') from None


class WebSearch:
    def __init__(self,settings,store,provider,charge):
        self.settings,self.store,self.provider,self.charge=settings,store,provider,charge
        self.slots=asyncio.Semaphore(2)
        self.pages=PageFetcher(settings,store) if settings.web_pages and settings.web_enabled and not settings.demo else None

    async def close(self):
        if self.pages:await self.pages.close()

    def validate_options(self,options):
        if options.web_search_enabled and not self.settings.web_enabled:
            raise HTTPException(409,'Поиск в интернете не настроен')

    async def retrieve(self,question,uid,request_id,max_chars):
        if not self.settings.web_enabled:raise HTTPException(409,'Поиск в интернете не настроен')
        started=time.monotonic()
        if max_chars<40:raise HTTPException(422,'Недостаточно места для результатов поиска')
        query=' '.join(question.split())[:400]
        key=cache_key('web-search-v1',uid,self.settings.search_folder,query)
        cached=self.store.get_cache(key,uid,'web-search')
        if cached:
            data=json.loads(cached)
        else:
            async def operation():
                async with self.slots:
                    return await asyncio.wait_for(self.provider.search(query),timeout=15)
            results=await self.charge(uid,'web-search',self.settings.search_reserve,operation,request_id)
            data={'results':results,'fetched_at':time.time()}
            self.store.put_cache(key,uid,'web_search',json.dumps(data,ensure_ascii=False).encode(),self.settings.search_ttl)
        results=[item for item in data['results'] if safe_web_url(item.get('url'))][:5]
        pages={}
        if self.pages and results:
            fetched=await asyncio.gather(*(self.pages.fetch(item['url'],uid) for item in results[:2]))
            pages=dict(enumerate(fetched))
        weights=[2 if pages.get(i,{}).get('status')=='loaded' else 1 for i in range(len(results))]
        sources=[];length=0
        for index,item in enumerate(results):
            page=pages.get(index,{})
            limit=min(2800 if weights[index]==2 else 900,(max_chars-length)*weights[index]//sum(weights[index:]))
            excerpt=select_passages(page['blocks'],query,limit) if page.get('status')=='loaded' else ''
            loaded=len(excerpt)>=40
            if not loaded:excerpt=item['excerpt'][:limit]
            if len(excerpt)<40:continue
            kind='page_excerpt' if loaded else 'search_snippet'
            url=page['url'] if loaded else item['url']
            title=(page.get('title') or item['title']) if loaded else item['title']
            source_id=cache_key('web-source-v2',uid,query,data['fetched_at'],item,page.get('fetched_at'),page.get('content_hash'),kind,url,excerpt)
            sources.append({'id':'W'+str(len(sources)+1),'type':'web','source_id':source_id,
                'title':title,'url':url,'preview_url':'/web-sources/'+source_id,
                'excerpt':excerpt,'query':query,'fetched_at':data['fetched_at'],'content_kind':kind,
                'search_url':item['url'],'page_fetched_at':page.get('fetched_at') if loaded else None,
                'page_cached':page.get('cached',False),'page_status':page.get('status','not_requested') if loaded or page.get('status')!='loaded' else 'insufficient_excerpt'})
            length+=len(excerpt)
        loaded=sum(source['content_kind']=='page_excerpt' for source in sources)
        return sources,{'query':query,'query_truncated':len(' '.join(question.split()))>400,
                         'fetched_at':data['fetched_at'],'cached':bool(cached),'count':len(sources),
                         'content_kind':'mixed' if loaded and loaded<len(sources) else 'page_excerpt' if loaded else 'search_snippet',
                         'page_fetch':{'enabled':self.pages is not None,'attempted':len(pages),'loaded':loaded,'fallback':sum(not any(source['search_url']==results[index]['url'] and source['content_kind']=='page_excerpt' for source in sources) for index in pages)},
                         'elapsed_ms':round((time.monotonic()-started)*1000)}

    def source(self,source_id,uid):
        with self.store.connection() as db:
            row=db.execute('''SELECT j.value FROM messages m JOIN chats c ON c.id=m.chat_id,
                json_each(m.metadata,'$.citations') j WHERE c.user_id=?
                AND json_extract(j.value,'$.type')='web' AND json_extract(j.value,'$.source_id')=? LIMIT 1''',
                (uid,source_id)).fetchone()
        if not row:raise HTTPException(404,'Источник недоступен или разговор удалён')
        result=json.loads(row['value'])
        if not safe_web_url(result.get('url')):raise HTTPException(410,'Ссылка источника недоступна')
        return result
