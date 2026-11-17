import asyncio
import base64
from contextlib import asynccontextmanager, suppress
import json
import logging
import re
import secrets
import time
from urllib.parse import urlencode, urlparse
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response, FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .live_configuration import LiveConfiguration, ConfigurationUpdate
from .access import Access, AccountInput
from .configuration import AppConfig, ChatOptions, SourceOptions
from .providers import Providers, split_text, LLM_PROTOCOL
from .web_search import WebSearch, WEB_UNSUPPORTED
from .rag import KnowledgeEngine, MAX_UPLOAD, RAG_CONTRACT, UNSUPPORTED, validate_rag_answer, grounding_instruction
from .chat_export import export_chat
from .reply_stream import ReplyStream, provisional_answer
from .retrieval_query import RetrievalQuery
from .metrics import Metrics
from .settings import Settings
from .store import Store, cache_key, digest

logger = logging.getLogger('gosboard')
COOKIE = 'gosboard_session'


class BudgetUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)
    daily_limit_rub: float = Field(gt=0, le=1000000000, allow_inf_nan=False)
    monthly_limit_rub: float = Field(gt=0, le=1000000000, allow_inf_nan=False)


class MessageInput(BaseModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=4000)
    regenerate: bool = False

    @field_validator('text')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Message must not be blank')
        return value.strip()


class BaseInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError('Недопустимое название базы')
        return value


class PromptWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(min_length=1, max_length=6000)

    @field_validator('label', 'system_prompt')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('Поле не может быть пустым')
        return value.strip()


class PromptDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)


class RagPolicy(BaseModel):
    chunk_size: int = Field(default=1200, ge=400, le=2000)
    chunk_overlap: int = Field(default=180, ge=0, le=400)
    top_k: int = Field(default=5, ge=1, le=10)
    top_k_documents: int = Field(default=5, ge=1, le=10)
    reindex: bool = False

    @field_validator('chunk_overlap')
    @classmethod
    def overlap_limit(cls, value, info):
        if value >= info.data.get('chunk_size', 1200):
            raise ValueError('Перекрытие должно быть меньше размера фрагмента')
        return value


class SpeechInput(BaseModel):
    request_id: UUID


def context_messages(history, text, config, history_messages=20):
    # Keep whole turns, never cut a role's text mid-message.
    available = config.context_chars - len(config.system_prompt) - len(text)
    turns = []
    history = history[-history_messages:] if history_messages else []
    for i in range(len(history) - 2, -1, -2):
        pair = history[i:i + 2]
        size = sum(len(m['text']) for m in pair)
        if size > available:
            break
        turns[0:0] = [{'role': m['role'], 'text': m['text']} for m in pair]
        available -= size
    return [{'role': 'system', 'text': config.system_prompt}, *turns, {'role': 'user', 'text': text}]


def create_app(settings=None, providers=None):
    settings = settings or Settings()
    settings.validate()
    store = Store(settings.data_dir)
    metrics = Metrics()
    store.metrics = metrics
    access = Access(store, settings)
    store.initialize_budget(settings.daily_limit, settings.monthly_limit)
    live_config = LiveConfiguration(store, settings.config_path)
    live_config.current()  # Validate the persisted active revision before startup.
    provider = providers or Providers(settings)
    llm_slots, speech_slots = asyncio.Semaphore(2), asyncio.Semaphore(2)
    running = {}
    request_tasks = {}
    reply_streams = {}
    locks = {}  # Reference-counted locks, discarded when no waiter remains.

    @asynccontextmanager
    async def lock(key):
        item = locks.setdefault(key, [asyncio.Lock(), 0])
        item[1] += 1
        try:
            try:
                await asyncio.wait_for(item[0].acquire(), timeout=5)
            except TimeoutError:
                raise HTTPException(429, 'Операция уже выполняется. Попробуйте позже') from None
            try:
                yield
            finally:
                item[0].release()
        finally:
            item[1] -= 1
            if item[1] == 0:
                locks.pop(key, None)

    @asynccontextmanager
    async def slot(semaphore):
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=5)
        except TimeoutError:
            raise HTTPException(429, 'Сервис занят. Попробуйте через несколько секунд') from None
        try:
            yield
        finally:
            semaphore.release()

    async def cleaner():
        while True:
            store.cleanup(settings.cache_bytes)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        await rag.start()
        cleanup = asyncio.create_task(cleaner())
        try:
            yield
        finally:
            tasks = [stream.task for stream in reply_streams.values() if stream.task]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await rag.close()
            await web.close()
            await provider.close()

    app = FastAPI(title='AI Talks', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.settings = settings

    @app.middleware('http')
    async def protections(request, call_next):
        # The demo can never be reached through a public Host header.
        if settings.demo and request.url.hostname not in {'localhost', '127.0.0.1', '::1', 'testserver'}:
            return JSONResponse({'detail': 'Demo is local-only'}, status_code=403)
        if not settings.demo and request.url.hostname != urlparse(settings.origin).hostname:
            return JSONResponse({'detail': 'Invalid host'}, status_code=400)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if request.headers.get('origin') != settings.origin:
                return JSONResponse({'detail': 'Недопустимый источник запроса'}, status_code=403)
        started = time.monotonic()
        outcome = '500'
        try:
            response = await call_next(request)
            outcome = str(response.status_code)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            route = request.scope.get('route')
            template = getattr(route, 'path', None)
            if template and template not in {'/api/admin/operations', '/api/health'}:
                metrics.observe('http', request.method+' '+template, (time.monotonic()-started)*1000, outcome)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def user(request: Request):
        result = store.auth(request.cookies.get(COOKIE))
        if not result:
            raise HTTPException(401, 'Войдите в приложение')
        account = access.account(result['email'])
        if not account or not account['enabled']:
            raise HTTPException(403, 'Доступ к приложению закрыт')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), result['csrf']):
                raise HTTPException(403, 'Сессия устарела. Обновите страницу')
        result['role'] = account['role']
        return result

    def editor(current=Depends(user)):
        if current['role'] not in {'editor', 'admin'}:
            raise HTTPException(403, 'Доступ только для редактора или администратора')
        return current

    def admin(current=Depends(user)):
        if current['role'] != 'admin':
            raise HTTPException(403, 'Доступ только для администратора')
        return current

    def login_response(uid, old_token=None):
        if old_token:
            store.logout(old_token)
        response = RedirectResponse('/', status_code=303)
        token = store.session(uid, settings.session_seconds)
        response.set_cookie(COOKIE, token, max_age=settings.session_seconds, httponly=True,
                            secure=settings.secure, samesite='lax', path='/')
        return response

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.get('/api/bootstrap')
    def bootstrap():
        return {'demo': settings.demo, 'login_url': '/api/auth/yandex', 'demo_login_url': '/api/auth/demo' if settings.demo else None}

    @app.post('/api/auth/demo')
    def demo_login(request: Request):
        if not settings.demo:
            raise HTTPException(404)
        identity = store.user('demo', 'demo@localhost', 'Демо-пользователь')
        return login_response(identity['id'], request.cookies.get(COOKIE))

    @app.get('/api/auth/yandex')
    def oauth_start():
        if settings.demo:
            return RedirectResponse('/?auth_error=demo', status_code=303)
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with store.connection() as db:
            db.execute('INSERT INTO oauth VALUES (?,?,?,?)', (digest(browser), state, verifier, time.time() + 600))
        challenge = base64.urlsafe_b64encode(bytes.fromhex(digest(verifier))).decode().rstrip('=')
        params = {'response_type': 'code', 'client_id': settings.oauth_id,
                  'redirect_uri': settings.origin + '/api/auth/callback', 'scope': 'login:email login:info',
                  'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
        response = RedirectResponse('https://oauth.yandex.ru/authorize?' + urlencode(params))
        response.set_cookie('gosboard_oauth', browser, max_age=600, httponly=True,
                            secure=settings.secure, samesite='lax', path='/api/auth')
        return response

    @app.get('/api/auth/callback')
    async def oauth_callback(request: Request, code: str = '', state: str = ''):
        browser = request.cookies.get('gosboard_oauth', '')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM oauth WHERE hash=?', (digest(browser),)).fetchone()
            db.execute('DELETE FROM oauth WHERE hash=?', (digest(browser),))
        if not row or row['expires'] < time.time() or not secrets.compare_digest(row['state'], state) or not code:
            raise HTTPException(400, 'Вход не подтверждён. Начните заново')
        token_response = await provider.request('POST', 'https://oauth.yandex.ru/token', data={
            'grant_type': 'authorization_code', 'code': code, 'client_id': settings.oauth_id,
            'client_secret': settings.oauth_secret, 'code_verifier': row['verifier'],
        })
        try:
            token = token_response.json()['access_token']
            profile_response = await provider.request('GET', 'https://login.yandex.ru/info',
                                                       headers={'Authorization': f'OAuth {token}'}, params={'format': 'json'})
            profile = profile_response.json()
            email = profile.get('default_email', '').strip().lower()
            uid = 'yandex:' + str(profile['id'])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(502, 'Не удалось подтвердить учётную запись Яндекса') from None
        account = access.account(email)
        if not account or not account['enabled']:
            response = RedirectResponse('/?auth_error=denied', status_code=303)
        else:
            store.user(uid, email, profile.get('display_name') or email)
            response = login_response(uid, request.cookies.get(COOKIE))
        response.delete_cookie('gosboard_oauth', path='/api/auth')
        return response

    @app.get('/api/me')
    def me(current=Depends(user)):
        return {**current, 'admin': current['role']=='admin', 'editor': current['role'] in {'editor','admin'}, 'demo': settings.demo}

    @app.post('/api/auth/logout')
    def logout(request: Request, current=Depends(user)):
        store.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.get('/api/config')
    def public_config(current=Depends(user)):
        config = live_config.current()
        return {'version': config.version, 'model_label': config.model_label,
                'demo_prompt': config.demo_prompt, 'voice': config.voice, 'max_record_seconds': 25,
                **config.catalog(), 'default_chat_settings': ChatOptions().model_dump(),
                'voices': [{'id':'marina','label':'Марина · женский'}, {'id':'filipp','label':'Филипп · мужской'}],
                'capabilities': {'rag': settings.rag_enabled, 'web_search': settings.web_enabled},
                'knowledge_bases': rag.repo.bases() if settings.rag_enabled else []}

    @app.get('/api/admin/config')
    def admin_config(current=Depends(admin)):
        config = live_config.current()
        return {'config': config.model_dump(), 'management': 'live; versioned in SQLite',
                'daily_limit_rub': store.budget()['day']['limit_rub'], 'monthly_limit_rub': store.budget()['month']['limit_rub']}

    @app.get('/api/editor/config')
    def editable_configuration(current=Depends(editor)):
        return live_config.read()

    @app.get('/api/editor/prompts')
    def list_prompts(current=Depends(editor)):
        return live_config.prompts()

    @app.post('/api/editor/prompts')
    async def create_prompt(body: PromptWrite, current=Depends(editor)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],label=body.label,text=body.system_prompt)

    @app.put('/api/editor/prompts/{pid}')
    async def edit_prompt(pid: str, body: PromptWrite, current=Depends(editor)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],pid,body.label,body.system_prompt)

    @app.delete('/api/editor/prompts/{pid}')
    async def delete_prompt(pid: str, body: PromptDelete, current=Depends(editor)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],pid,delete=True)

    @app.get('/api/editor/config/versions')
    def configuration_versions(current=Depends(editor)):
        return live_config.versions()

    @app.get('/api/editor/config/versions/{revision}')
    def configuration_version(revision: int, current=Depends(editor)):
        return live_config.version(revision)

    @app.put('/api/editor/config')
    async def save_configuration(body: ConfigurationUpdate, current=Depends(editor)):
        async with lock(('configuration',)):
            return live_config.save(body, current['id'])

    @app.get('/api/admin/users')
    def list_accounts(current=Depends(admin)):
        return access.accounts()

    @app.post('/api/admin/users')
    def create_account(body: AccountInput, current=Depends(admin)):
        return access.save(body, current['id'], create_only=True)

    @app.put('/api/admin/users')
    async def save_account(body: AccountInput, current=Depends(admin)):
        previous = access.account(body.email)
        result = access.save(body, current['id'])
        changed = previous is not None and (previous['role']!=body.role or bool(previous['enabled'])!=body.enabled)
        with store.connection() as db:
            affected = {row['id'] for row in db.execute('SELECT id FROM users WHERE lower(email)=?', (body.email,))}
        for (uid, rid), task in list({**running,**request_tasks, **{key: stream.task for key, stream in reply_streams.items()}}.items()):
            if changed and uid in affected and task:
                task.cancel()
        return result

    @app.get('/api/admin/audit')
    def audit_events(current=Depends(admin)):
        with store.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 100')]

    @app.get('/api/admin/chats')
    def admin_chats(owner: str | None = Query(default=None,max_length=200), offset: int = Query(default=0,ge=0), limit: int = Query(default=50,ge=1,le=100), current=Depends(admin)):
        return access.admin_chats(owner,offset,limit)

    @app.get('/api/admin/chats/{cid}/messages')
    def admin_messages(cid: UUID, current=Depends(admin)):
        owner=access.chat_owner(str(cid))
        snapshot=store.chat_snapshot(str(cid),owner)
        access.audit(current['id'],'chat.read',str(cid),{'owner_id':owner,'message_count':len(snapshot['messages'])})
        return snapshot

    @app.get('/api/editor/chats')
    def editable_chats(current=Depends(editor)):
        return access.editor_chats()

    @app.put('/api/editor/chats/{cid}/model')
    async def editorial_chat(cid: UUID, options: ChatOptions, current=Depends(editor)):
        async with lock(('configuration',)):
            async with lock(('chat', str(cid))):
                config = live_config.current()
                owner = access.chat_owner(str(cid))
                previous = store.chat(str(cid), owner)['settings']
                # Preserve source controls and history unless an administrator explicitly changes it.
                changes = {key: getattr(options,key) for key in ('model_id','prompt_id','voice','reasoning_effort')}
                if 'history_messages' in options.model_fields_set:
                    if current['role']!='admin' and options.history_messages!=previous['history_messages']:
                        raise HTTPException(403, 'Размер истории настраивает администратор')
                    if current['role']=='admin':
                        changes['history_messages']=options.history_messages
                updated = ChatOptions.model_validate({**previous, **changes})
                try:
                    config.resolve(updated)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                result = store.update_chat_settings(str(cid), owner, updated.model_dump())
                access.audit(current['id'], 'chat.model', str(cid), {key:getattr(updated,key) for key in ('model_id','prompt_id','voice','reasoning_effort','history_messages')})
                return result

    @app.get('/api/usage')
    def usage(current=Depends(user)):
        return {'rows': store.usage(current['id']), 'accounting': 'conservative reservations; not provider invoice'}

    @app.get('/api/admin/usage')
    def all_usage(current=Depends(admin)):
        return {'rows': store.usage(), 'daily_limit_rub': store.budget()['day']['limit_rub'], 'monthly_limit_rub': store.budget()['month']['limit_rub']}

    @app.get('/api/admin/operations')
    def operations(current=Depends(admin)):
        return {**metrics.snapshot(), 'budget': store.budget(), 'active_paid_operations': len(running), 'active_message_requests': len(request_tasks)}

    @app.get('/api/admin/budget')
    def budget(current=Depends(admin)):
        return {**store.budget(), 'allowances_rub': {'llm': settings.llm_reserve, 'stt': settings.stt_reserve, 'tts_per_segment': settings.tts_reserve, 'web_search': settings.search_reserve, 'embedding': settings.embedding_reserve}}

    @app.put('/api/admin/budget')
    def update_budget(body: BudgetUpdate, current=Depends(admin)):
        store.update_budget(body.daily_limit_rub, body.monthly_limit_rub, body.expected_revision, current['id'])
        return budget(current)

    @app.get('/api/chats')
    def chats(current=Depends(user)):
        return store.chats(current['id'])

    @app.post('/api/chats')
    async def new_chat(options: ChatOptions | None = None, current=Depends(user)):
        async with lock(('configuration',)):
            config = live_config.current()
            selected = options or ChatOptions()
            if current['role']!='admin' and selected.history_messages!=20:
                raise HTTPException(403, 'Размер истории настраивает администратор')
            if current['role']=='user' and (selected.prompt_id!='default' or selected.model_id!='default' or selected.voice is not None or selected.reasoning_effort!='default'):
                raise HTTPException(403, 'Модель, инструкцию и голос настраивает редактор')
            rag.validate_options(selected)
            web.validate_options(selected)
            try:
                config.resolve(selected)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            return store.new_chat(current['id'], selected.model_dump())

    @app.get('/api/chats/{cid}/export')
    async def download_chat(cid: UUID, format: str = 'md', current=Depends(user)):
        if format not in {'json', 'md', 'txt'}:
            raise HTTPException(422, 'Формат экспорта: md, txt или json')
        async with lock(('chat', str(cid))):
            chat = store.chat(str(cid), current['id'])
            messages = store.messages(str(cid))
            content, media_type = export_chat(chat, messages, settings.origin, format)
        return Response(content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename="aitalks-{cid}.{format}"'})

    @app.put('/api/chats/{cid}/settings')
    async def update_chat_settings(cid: UUID, options: ChatOptions, current=Depends(user)):
        async with lock(('configuration',)):
            async with lock(('chat', str(cid))):
                config = live_config.current()
                existing = store.chat(str(cid), current['id'])['settings']
                if current['role']=='user' and any(getattr(options, key)!=existing[key] for key in ('model_id','prompt_id','voice','reasoning_effort')):
                    raise HTTPException(403, 'Модель, инструкцию и голос настраивает редактор')
                if 'history_messages' not in options.model_fields_set:
                    options=options.model_copy(update={'history_messages':existing['history_messages']})
                elif current['role']!='admin' and options.history_messages!=existing['history_messages']:
                    raise HTTPException(403, 'Размер истории настраивает администратор')
                rag.validate_options(options)
                web.validate_options(options)
                try:
                    config.resolve(options)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                result = store.update_chat_settings(str(cid), current['id'], options.model_dump())
                access.audit(current['id'], 'chat.settings', str(cid), {key:getattr(options,key) for key in ('model_id','voice','reasoning_effort','rag_enabled','knowledge_base_id','history_messages')})
                return result

    @app.patch('/api/chats/{cid}/sources')
    async def update_sources(cid: UUID, sources: SourceOptions, current=Depends(user)):
        async with lock(('configuration',)):
            async with lock(('chat', str(cid))):
                existing = store.chat(str(cid), current['id'])['settings']
                selected = ChatOptions.model_validate({**existing, **sources.model_dump(exclude_unset=True)})
                rag.validate_options(selected)
                web.validate_options(selected)
                try:
                    live_config.current().resolve(selected)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                result = store.update_chat_settings(str(cid), current['id'], selected.model_dump())
                access.audit(current['id'], 'chat.sources', str(cid), sources.model_dump())
                return result

    @app.get('/api/chats/{cid}/messages')
    def messages(cid: UUID, current=Depends(user)):
        store.chat(str(cid), current['id'])
        return store.messages(str(cid))

    @app.delete('/api/chats/{cid}')
    async def delete_chat(cid: UUID, current=Depends(user)):
        async with lock(('chat', str(cid))):
            store.delete_chat(str(cid), current['id'])
        return {'ok': True}

    @app.post('/api/requests/{rid}/cancel')
    async def cancel(rid: UUID, current=Depends(user)):
        stream = reply_streams.get((current['id'], str(rid)))
        task = (stream.task if stream else None) or request_tasks.get((current['id'],str(rid))) or running.get((current['id'], str(rid)))
        if task:
            task.cancel()
        return {'cancel_requested': task is not None}

    async def paid_operation(uid, kind, amount, operation, request_id):
        reserve_id = None
        if not settings.demo:
            reserve_id = store.reserve(uid, kind, amount, settings.daily_limit, settings.monthly_limit)
        task = asyncio.create_task(operation())
        running[(uid, request_id)] = task
        try:
            result = await asyncio.wait_for(task, timeout=150)
        except asyncio.CancelledError:
            if reserve_id:
                store.settle(reserve_id, 'uncertain')
            if asyncio.current_task().cancelling():
                raise
            raise HTTPException(409, 'Операция остановлена. Провайдер мог учесть часть расходов') from None
        except BaseException:
            # Errors/cancellation can still incur provider charges. Never release automatically.
            if reserve_id:
                store.settle(reserve_id, 'uncertain')
            raise
        finally:
            running.pop((uid, request_id), None)
        if reserve_id:
            store.settle(reserve_id, 'complete')
        return result

    async def paid(uid, kind, amount, operation, request_id):
        started = time.monotonic()
        outcome = 'complete'
        try:
            return await paid_operation(uid, kind, amount, operation, request_id)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except BaseException as error:
            outcome = str(error.status_code) if isinstance(error, HTTPException) else 'error'
            raise
        finally:
            metrics.observe('operation', kind, (time.monotonic()-started)*1000, outcome)

    query_resolver = RetrievalQuery(store, provider, paid)
    rag = KnowledgeEngine(settings, store, provider, paid)
    web = WebSearch(settings, store, provider, paid)
    app.state.web = web
    app.state.rag = rag
    app.state.live_config = live_config

    @app.get('/api/knowledge-bases')
    def bases(current=Depends(user)):
        rag.require_enabled()
        return rag.repo.bases()

    @app.post('/api/knowledge-bases')
    def create_base(body: BaseInput, current=Depends(editor)):
        rag.require_enabled()
        return rag.repo.create_base(body.name)

    @app.put('/api/knowledge-bases/{bid}/policy')
    async def tune_base(bid: UUID, body: RagPolicy, current=Depends(admin)):
        rag.require_enabled()
        result = await rag.tune(str(bid), body)
        access.audit(current['id'], 'rag.policy', str(bid), body.model_dump())
        return result

    @app.get('/api/knowledge-bases/{bid}/documents')
    def documents(bid: UUID, current=Depends(user)):
        rag.require_enabled()
        return rag.repo.documents(str(bid))

    @app.post('/api/knowledge-bases/{bid}/documents', status_code=202)
    async def upload_document(bid: UUID, request: Request, filename: str, current=Depends(editor)):
        rag.require_enabled()
        rag.repo.base(str(bid))
        body = bytearray()
        async for block in request.stream():
            body.extend(block)
            if len(body) > MAX_UPLOAD:
                raise HTTPException(413, 'Максимальный размер документа — 10 МБ')
        try:
            return await rag.upload(str(bid), filename, request.headers.get('Content-Type', ''), bytes(body), current['id'])
        finally:
            body.clear()

    @app.delete('/api/knowledge-bases/{bid}')
    async def delete_base(bid: UUID, current=Depends(editor)):
        rag.require_enabled()
        await rag.remove_base(str(bid))
        return {'ok': True}

    @app.post('/api/knowledge-bases/{bid}/reindex')
    async def reindex_base(bid: UUID, current=Depends(editor)):
        rag.require_enabled()
        await rag.reindex(str(bid))
        return {'queued': True}

    @app.delete('/api/documents/{did}')
    async def delete_document(did: UUID, current=Depends(editor)):
        rag.require_enabled()
        await rag.remove_document(str(did))
        return {'ok': True}

    @app.post('/api/documents/{did}/retry')
    async def retry_document(did: UUID, current=Depends(editor)):
        rag.require_enabled()
        await rag.retry(str(did))
        return {'queued': True}

    @app.get('/api/documents/{did}/file')
    async def original_document(did: UUID, current=Depends(user)):
        rag.require_enabled()
        document = rag.repo.document(str(did))
        rag.repo.base(document['base_id'])
        if document['status'] in {'queued_upload', 'uploading'}:
            raise HTTPException(409, 'Документ ещё сохраняется')
        path = await rag.original(document)
        return FileResponse(path, media_type=document['mime'], filename=document['filename'], content_disposition_type='inline')

    @app.get('/api/sources/{chunk_id}')
    def source(chunk_id: str, current=Depends(user)):
        rag.require_enabled()
        if not re.fullmatch(r'[0-9a-f]{64}', chunk_id):
            raise HTTPException(404, 'Фрагмент не найден')
        chunk = rag.repo.source(chunk_id)
        return {'chunk_id': chunk['id'], 'document_id': chunk['document_id'], 'title': chunk['filename'],
                'base_name': chunk['base_name'], 'page': chunk['page'], 'position': chunk['position'],
                'text': chunk['text'], 'document_url': '/api/documents/' + chunk['document_id'] + '/file'}

    @app.get('/api/web-sources/{source_id}')
    def web_source(source_id: str, current=Depends(user)):
        if not re.fullmatch(r'[0-9a-f]{64}',source_id):
            raise HTTPException(404,'Источник не найден')
        return web.source(source_id,current['id'])

    async def run_send(cid: UUID, body: MessageInput, current, stream=None):
        cid, rid, uid = str(cid), str(body.request_id), current['id']
        store.chat(cid, uid)
        async with lock(('chat', cid)):
            fingerprint = cache_key(cid, body.text, body.regenerate)
            previous = store.begin_request(rid, uid, 'llm', fingerprint)
            if previous is not None:
                return previous
            started = time.monotonic()
            committed = False
            request_tasks[(uid,rid)] = asyncio.current_task()
            try:
                config = live_config.current()
                chat = store.chat(cid, uid)
                options = ChatOptions.model_validate(chat['settings'])
                try:
                    effective = config.resolve(options)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                rag.validate_options(options)
                web.validate_options(options)
                grounded = options.rag_enabled or options.web_search_enabled
                if stream:
                    stream.speech_config = effective
                    stream.auto_speech = options.auto_speech
                    await stream.emit('status', {'text': 'Ищу источники' if grounded else 'Формирую ответ'})
                sources, base_revision, web_info = [], None, None
                history = store.messages(cid, options.history_messages)
                retrieval_query,query_info=body.text,{'contextual':False}
                if grounded:
                    candidate_history=context_messages(history,body.text,effective,options.history_messages)[1:-1]
                    if candidate_history:
                        async with slot(llm_slots):
                            retrieval_query,query_info=await query_resolver.resolve(uid,cid,body.text,candidate_history,effective,rid,settings.llm_reserve)
                    else:
                        retrieval_query,query_info=await query_resolver.resolve(uid,cid,body.text,[],effective,rid,settings.llm_reserve)
                    max_source_chars = min(5500,effective.context_chars-len(effective.system_prompt)-len(RAG_CONTRACT)-len(body.text)-1000)
                    if max_source_chars<1200:
                        raise HTTPException(422,'Недостаточно места для источников. Сократите запрос или инструкцию')
                    rag_budget = max_source_chars//2 if options.web_search_enabled else max_source_chars
                    if options.rag_enabled:
                        sources,base_revision = await rag.retrieve(options.knowledge_base_id,retrieval_query,uid,rag_budget,rid)
                        if options.web_search_enabled:
                            sources=sources[:5]
                    if options.web_search_enabled:
                        web_sources,web_info = await web.retrieve(retrieval_query,uid,rid,max_source_chars-sum(len(source['excerpt']) for source in sources))
                        sources += web_sources
                    if sources:
                        while sources:
                            source_data=[{**{key:source[key] for key in ('id','title','excerpt','type')},'content_kind':source.get('content_kind','document_chunk')} for source in sources]
                            system=effective.system_prompt+'\n'+grounding_instruction(sources)+('\nRESOLVED_QUESTION='+json.dumps(retrieval_query,ensure_ascii=False) if query_info['contextual'] else '')+'\nSOURCE_DATA='+json.dumps(source_data,ensure_ascii=False)
                            if len(system)+len(body.text)<=effective.context_chars:
                                break
                            sources.pop()
                        if not sources:
                            raise HTTPException(422,'Источники не помещаются в контекст. Сократите запрос или инструкцию')
                        effective=effective.model_copy(update={'system_prompt':system})
                snapshot = effective.snapshot()
                config_hash = store.save_configuration(snapshot)
                if grounded:
                    # Source IDs are numbered afresh each turn. Historical markers must not be mistaken for fresh evidence.
                    history = [{**message, 'text': re.sub(r'\[(?:[DW]\d+)(?:\s*,\s*[DW]\d+)*\]', '', message['text']) if message['role']=='assistant' else message['text']} for message in history]
                messages = context_messages(history, body.text, effective, options.history_messages)
                key = cache_key('llm-v5', LLM_PROTOCOL, uid, settings.demo, snapshot, options.model_dump(exclude={'streaming_enabled','auto_speech','communication_mode','review_before_send','live_pause'}), base_revision, [source['source_id'] for source in sources if source['type']=='web'], messages)
                cached = None if body.regenerate else store.get_cache(key, uid, 'llm')
                async with slot(llm_slots):
                    if cached:
                        data = json.loads(cached)
                        answer, meta = data['text'], data['metadata']
                    elif grounded and not sources:
                        answer, meta = WEB_UNSUPPORTED if options.web_search_enabled else UNSUPPORTED, {'citations': [], 'grounding': 'no_evidence', 'model_called': False}
                    else:
                        raw, preview = '', ''
                        async def delta(part):
                            nonlocal raw, preview
                            raw += part
                            visible = provisional_answer(raw) if grounded else raw
                            if visible.startswith(preview) and len(visible) > len(preview):
                                addition = visible[len(preview):]
                                preview = visible
                                await stream.emit('delta', {'text': addition, 'provisional': grounded})
                                if not grounded:
                                    stream.feed(addition)
                        async def completion():
                            if stream and hasattr(provider, 'complete_stream'):
                                return await provider.complete_stream(messages, effective, delta)
                            return await provider.complete(messages, effective)
                        answer, meta = await paid(uid, 'llm', settings.llm_reserve, completion, rid)
                        if grounded:
                            if stream:
                                await stream.emit('status', {'text': 'Проверяю ссылки'})
                            answer,citations,supported = validate_rag_answer(answer,sources,WEB_UNSUPPORTED if options.web_search_enabled else UNSUPPORTED)
                            rag.verify_sources_live([source for source in citations if source['type']=='rag'])
                            meta.update(citations=citations, grounding='supported' if supported else 'insufficient_evidence', base_version=base_revision)
                        store.put_cache(key, uid, 'llm', json.dumps({'text': answer, 'metadata': meta}).encode(), 86400)
                if options.rag_enabled:
                    rag.verify_sources_live([source for source in meta.get('citations',[]) if source['type']=='rag'])
                metadata = {**meta, 'config_version': effective.version, 'config_hash': config_hash,
                            'model': effective.model, 'model_label': effective.model_label,
                            'temperature': effective.temperature, 'max_tokens': effective.max_tokens,
                            'chat_settings': options.model_dump(),
                            'delivery': {'streaming': stream is not None, 'auto_speech': bool(stream and options.auto_speech)},
                           'context': {'history_messages_limit':options.history_messages,'history_messages_included':len(messages)-2 if meta.get('model_called',True) else 0,'input_chars':sum(len(m['text']) for m in messages) if meta.get('model_called',True) else 0,'character_limit':effective.context_chars},
                            'prompt_label': next(item['label'] for item in config.catalog()['prompts'] if item['id'] == options.prompt_id),
                            'retrieval': {'context_query':query_info,'rag_enabled':options.rag_enabled,'web_search_enabled':options.web_search_enabled,'rag_sources':sum(source['type']=='rag' for source in sources),'web':web_info},
                            'cached': bool(cached), 'elapsed_ms': round((time.monotonic() - started) * 1000)}
                aid = store.add_turn(cid, body.text, answer, metadata)
                result = {'message_id': aid, 'metadata': metadata}
                store.end_request(rid, result)
                committed = True
                if stream:
                    await stream.emit('answer', {'text': answer, **result})
                    if grounded or cached or not locals().get('preview'):
                        stream.feed(re.sub(r'\[[DW]\d+(?:\s*,\s*[DW]\d+)*\]', '', answer))
                    stream.feed('', final=True)
                    if options.auto_speech and len(answer) > 6000:
                        await stream.emit('audio_error', {'detail':'Озвучиваются только первые 6000 символов. Полный текст сохранён.'})
                logger.info('stage=llm elapsed_ms=%s cached=%s', metadata['elapsed_ms'], bool(cached))
                return result
            except asyncio.CancelledError:
                if not committed:
                    store.end_request(rid)
                raise HTTPException(409,'Операция остановлена. Уже выполненная работа провайдера могла быть учтена') from None
            except BaseException:
                if not committed:
                    store.end_request(rid)
                raise
            finally:
                request_tasks.pop((uid,rid),None)

    @app.post('/api/chats/{cid}/messages')
    async def send(cid: UUID, body: MessageInput, current=Depends(user)):
        return await run_send(cid, body, current)

    @app.post('/api/requests/{rid}/audio/stop')
    async def stop_stream_audio(rid: UUID, current=Depends(user)):
        stream = reply_streams.get((current['id'], str(rid)))
        if stream:
            stream.stop_audio()
        return {'stopped': stream is not None}

    @app.post('/api/chats/{cid}/messages/stream')
    async def send_stream(cid: UUID, body: MessageInput, request: Request, current=Depends(user)):
        uid, rid = current['id'], str(body.request_id)
        chat = store.chat(str(cid), uid)
        if not chat['settings']['streaming_enabled']:
            raise HTTPException(409, 'Включите потоковый ответ в настройках разговора')
        if (uid, rid) in reply_streams:
            raise HTTPException(409, 'Этот ответ уже формируется. Дождитесь завершения или проверьте историю')

        async def synthesize(phrase, config):
            key = cache_key('tts-v1', uid, settings.demo, phrase, config.voice, config.tts_model, config.tts_speed, 'wav')
            async with lock(('tts', key)):
                cached = store.get_cache(key, uid, 'tts')
                if cached is None:
                    async with slot(speech_slots):
                        data = await paid(uid, 'tts', settings.tts_reserve * len(split_text(phrase)),
                                          lambda: provider.synthesize(phrase, config), rid + ':audio')
                    store.put_cache(key, uid, 'tts', data, settings.audio_ttl)
            return {'url': '/api/audio/' + key, 'cached': cached is not None}

        stream = ReplyStream(synthesize)
        reply_streams[(uid, rid)] = stream

        async def produce():
            try:
                stream.audio_task = asyncio.create_task(stream.audio_worker())
                result = await run_send(cid, body, current, stream)
                # A retry of an already completed request must not regenerate text or speech.
                if not stream.speech_config:
                    message = next((m for m in store.messages(str(cid)) if m['id'] == result['message_id']), None)
                    if message:
                        await stream.emit('answer', {'text': message['text'], **result})
                await stream.emit('text_done', result)
                stream.phrases.put_nowait(None)
                with suppress(asyncio.CancelledError):
                    await stream.audio_task
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError()
                await stream.emit('done', result)
            except asyncio.CancelledError:
                stream.stop_audio()
                with suppress(asyncio.QueueFull):
                    stream.events.put_nowait({'event':'error', 'detail': 'Ответ остановлен. Уже выполненная работа могла быть учтена в бюджете.', 'cancelled': True})
            except Exception as error:
                stream.stop_audio()
                detail = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, str) else 'Поток прерван. Проверьте историю перед повтором.'
                await stream.emit('error', {'detail': detail})
            finally:
                stream.stop_audio()
                if stream.audio_task:
                    with suppress(asyncio.CancelledError):
                        await stream.audio_task
                reply_streams.pop((uid, rid), None)

        async def events():
            stream.task = asyncio.create_task(produce())
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(stream.events.get(), timeout=10)
                    except TimeoutError:
                        yield ': keepalive\n\n'
                        continue
                    yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    if event['event'] in {'done', 'error'}:
                        return
            finally:
                while not stream.events.empty():
                    stream.events.get_nowait()
                stream.task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream.task
                reply_streams.pop((uid, rid), None)

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @app.post('/api/stt')
    async def stt(request: Request, current=Depends(user)):
        # Browser sends mono 16-bit little-endian PCM at 16 kHz, bounded to 25 s.
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            raise HTTPException(415, 'Ожидается PCM-аудио')
        try:
            rid = str(UUID(request.headers.get('X-Request-ID', '')))
        except ValueError:
            raise HTTPException(422, 'Требуется идентификатор запроса') from None
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > 800000:
                raise HTTPException(413, 'Запись длиннее 25 секунд')
        if len(audio) < 3200 or len(audio) % 2:
            raise HTTPException(422, 'Запись слишком короткая или повреждена')
        uid = current['id']
        key = cache_key('stt-v1', uid, settings.demo, digest(bytes(audio)), 'lpcm', 16000, 'ru-RU')
        async with lock(('stt', key)):
            previous = store.begin_request(rid, uid, 'stt', key)
            if previous is not None:
                return previous
            started = time.monotonic()
            try:
                cached = store.get_cache(key, uid, 'stt')
                async with slot(speech_slots):
                    text = cached.decode() if cached else await paid(uid, 'stt', settings.stt_reserve, lambda: provider.transcribe(bytes(audio)), rid)
                store.put_cache(key, uid, 'stt', text.encode(), 3600)
                result = {'text': text, 'cached': bool(cached), 'elapsed_ms': round((time.monotonic() - started) * 1000), 'demo': settings.demo}
                store.end_request(rid, result)
                return result
            except BaseException:
                store.end_request(rid)
                raise
            finally:
                audio.clear()  # Raw audio is never persisted.

    @app.post('/api/chats/{cid}/messages/{mid}/speech')
    async def speech(cid: UUID, mid: UUID, body: SpeechInput, current=Depends(user)):
        cid, mid, uid, rid = str(cid), str(mid), current['id'], str(body.request_id)
        async with lock(('chat', cid)):
            config = live_config.current()
            chat = store.chat(cid, uid)
            speech_config = config.resolve(ChatOptions.model_validate(chat['settings']))
            message = next((m for m in store.messages(cid) if m['id'] == mid and m['role'] == 'assistant'), None)
            if not message:
                raise HTTPException(404, 'Ответ не найден')
            if len(message['text']) > 6000:
                raise HTTPException(422, 'Ответ слишком длинный для озвучивания; попросите более краткий')
            spoken_text = re.sub(r'\[[DW]\d+\]', '', message['text']).strip() if message['metadata'].get('citations') else message['text']
            key = cache_key('tts-v1', uid, settings.demo, spoken_text, speech_config.voice, speech_config.tts_model, speech_config.tts_speed, 'wav')
            async with lock(('tts', key)):
                previous = store.begin_request(rid, uid, 'tts', key)
                if previous is not None:
                    if store.get_cache(key, uid):
                        return previous
                    raise HTTPException(409, 'Аудио истекло. Запросите озвучивание снова')
                started = time.monotonic()
                try:
                    cached = store.get_cache(key, uid, 'tts')
                    async with slot(speech_slots):
                        if cached is None:
                            amount = settings.tts_reserve * len(split_text(spoken_text))
                            audio = await paid(uid, 'tts', amount, lambda: provider.synthesize(spoken_text, speech_config), rid)
                            store.put_cache(key, uid, 'tts', audio, settings.audio_ttl)
                    result = {'url': f'/api/audio/{key}', 'cached': cached is not None,
                              'elapsed_ms': round((time.monotonic() - started) * 1000), 'demo': settings.demo}
                    store.end_request(rid, result)
                    return result
                except BaseException:
                    store.end_request(rid)
                    raise

    @app.get('/api/audio/{key}')
    def audio(key: str, current=Depends(user)):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise HTTPException(404)
        with store.connection() as db:
            row = db.execute("SELECT value FROM cache WHERE key=? AND user_id=? AND kind='tts' AND expires>?",
                             (key, current['id'], time.time())).fetchone()
        if not row:
            raise HTTPException(404, 'Аудио истекло. Запросите озвучивание снова')
        return Response(bytes(row['value']), media_type='audio/wav')

    @app.exception_handler(TimeoutError)
    async def timeout_handler(request, exc):
        return JSONResponse({'detail': 'Превышено время ожидания. Проверьте историю перед повтором'}, status_code=504)

    return app
