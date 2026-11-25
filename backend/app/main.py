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

import httpx
from jwt import PyJWTError

from fastapi import Depends, FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response, FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .live_configuration import LiveConfiguration, ConfigurationUpdate
from .access import Access, AccountInput
from .account_import import MAX_CSV_BYTES, preview_accounts, apply_accounts
from .spaces import Spaces, SpaceInput, ArchiveInput, SpaceAssignment, ModeInput, PolicyUpdate, SpacePolicy
from .configuration import AppConfig, ChatOptions
from .providers import Providers, split_text, completion_protocol
from .web_search import WebSearch, WEB_UNSUPPORTED, safe_web_url
from .rag import KnowledgeEngine, MAX_UPLOAD, RAG_CONTRACT, RAG_INSTRUCTION_MAX, RAG_MARKER, UNSUPPORTED, validate_rag_answer, grounding_instruction
from .chat_export import export_chat
from .reply_stream import ReplyStream, provisional_answer
from .retrieval_query import RetrievalQuery
from .metrics import Metrics
from .settings import Settings
from .store import Store, cache_key, digest, operation_space
from . import vtb_edu

logger = logging.getLogger('gosboard')
COOKIE = 'gosboard_session'
SOURCE_ID_GROUP = r'\[[DW]\d+(?:\s*,\s*[DW]\d+)*\]'
SOURCE_MARKER = re.compile(SOURCE_ID_GROUP + r'(?:\([^\s)]+\))?')
INCOMPLETE_MARKER = re.compile(r'\[[^\[\]]*$|' + SOURCE_ID_GROUP + r'(?:\([^\s)]*)?$')
CHAT_FIELDS = {'communication_mode', 'streaming_enabled', 'auto_speech', 'live_pause', 'review_before_send'}
MESSAGE_FIELDS = {'demo', 'cached', 'elapsed_ms', 'delivery'}


def public_message(message):
    return {**message, 'text': SOURCE_MARKER.sub('', message['text']) if message['role']=='assistant' else message['text'],
            'metadata': public_metadata(message['metadata'])}


def public_metadata(metadata):
    return {key: metadata[key] for key in MESSAGE_FIELDS if key in metadata}


def public_chat(chat):
    return {**chat, 'settings': {key: chat['settings'][key] for key in CHAT_FIELDS}}


def chat_profiles(config, options, base_ids=(), prompt=None, classic_text=False, policy_revision=None):
    resolved = config.resolve(options)
    if prompt is not None:
        if len(prompt) + 4000 > resolved.context_chars:
            raise ValueError('Инструкция и сообщение не помещаются в контекст')
        resolved = resolved.model_copy(update={'system_prompt': prompt})
    profiles = {}
    for mode in ('text', 'voice', 'live'):
        mode_options = ChatOptions.model_validate({
            **options.model_dump(), 'communication_mode': mode,
            'streaming_enabled': not classic_text or mode != 'text', 'auto_speech': mode != 'text',
        })
        profiles[mode] = {'settings': mode_options.model_dump(), 'effective': resolved.snapshot(),
                          'base_ids': list(base_ids), 'policy_revision': policy_revision}
    legacy_mode = options.communication_mode or ('voice' if options.auto_speech else 'text')
    profiles[legacy_mode]['settings'] = options.model_dump()
    return profiles


def profile_for(chat, profiles):
    options = chat['settings']
    mode = options['communication_mode'] or ('voice' if options['auto_speech'] else 'text')
    return profiles[mode]


class ModelAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    blocked: bool


class ImportConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: bool = False
    approve_existing: bool = False
    lines: list[int] | None = Field(default=None, max_length=1000)


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


class RagInstructionUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    instruction: str = Field(min_length=1, max_length=RAG_INSTRUCTION_MAX)
    expected_revision: int = Field(ge=0, strict=True)


def create_app(settings=None, providers=None):
    settings = settings or Settings()
    settings.validate()
    if settings.vtb_edu_ready:
        vtb_edu.identity_hub_url(settings.vtb_edu_discovery_url, 'OpenID Configuration')
        vtb_edu.identity_hub_url(settings.vtb_edu_issuer, 'issuer')
    store = Store(settings.data_dir)
    if not settings.vtb_edu_ready:
        with store.connection() as db:
            db.execute("DELETE FROM sessions WHERE user_id LIKE 'vtb-edu:%'")
    metrics = Metrics()
    store.metrics = metrics
    access = Access(store, settings)
    spaces = Spaces(store, access)
    store.initialize_budget(settings.daily_limit, settings.monthly_limit)
    live_config = LiveConfiguration(store, settings.config_path)
    migration_config = live_config.current()
    with store.connection() as db:
        initial_policy = SpacePolicy(prompt=migration_config.system_prompt, prompts=migration_config.prompts)
        db.execute('INSERT OR IGNORE INTO space_policies VALUES (?,?,?,?,?)',
                   ('initial', 1, initial_policy.model_dump_json(), time.time(), 'migration'))
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("""
            SELECT c.id,coalesce(s.settings,'{}') AS settings FROM chats c
            LEFT JOIN chat_settings s ON s.chat_id=c.id
            WHERE NOT EXISTS (SELECT 1 FROM chat_profiles p WHERE p.chat_id=c.id)
        """).fetchall():
            options = ChatOptions.model_validate_json(row['settings'])
            try:
                profiles = chat_profiles(migration_config, options,
                                         [options.knowledge_base_id] if options.rag_enabled else [],
                                         classic_text=not options.streaming_enabled)
            except ValueError:
                profiles = {}
            db.execute('INSERT INTO chat_profiles VALUES (?,?)', (row['id'], json.dumps(profiles)))
    provider = providers or Providers(settings)
    llm_slots, speech_slots = asyncio.Semaphore(2), asyncio.Semaphore(2)
    running = {}
    request_tasks = {}
    space_tasks = {}
    retrieval_tasks = {}

    def cancel_space_tasks(sid, email=None):
        for (task_space, task_email, _), task in list(space_tasks.items()):
            if task_space == sid and (email is None or task_email == email):
                task.cancel()

    @asynccontextmanager
    async def space_operation(sid, current, rid):
        spaces.require(current['email'], sid)
        key = (sid, current['email'], rid)
        if key in space_tasks:
            raise HTTPException(409, 'Операция уже выполняется')
        space_tasks[key] = asyncio.current_task()
        token = operation_space.set(sid)
        try:
            yield
        finally:
            operation_space.reset(token)
            space_tasks.pop(key, None)
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

    def session_user(request: Request):
        result = store.auth(request.cookies.get(COOKIE))
        if not result or (result['id'].startswith('vtb-edu:') and not settings.vtb_edu_ready):
            raise HTTPException(401, 'Войдите в приложение')
        account = access.account(result['email'])
        if not account or account['enabled'] == 0:
            raise HTTPException(403, 'Доступ к приложению закрыт')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), result['csrf']):
                raise HTTPException(403, 'Сессия устарела. Обновите страницу')
        result['requested_space'] = request.headers.get('X-Space-ID')
        result['status'] = 'active' if account['enabled'] == 1 else 'pending'
        result['role'] = account['role'] if result['status'] == 'active' else None
        return result

    def user(current=Depends(session_user)):
        if current['status'] != 'active':
            raise HTTPException(403, 'Дождитесь допуска администратора')
        return current

    def work_chat(cid, current):
        chat = store.chat(str(cid), current['id'])
        if current['requested_space'] not in (None, chat['space_id']):
            raise HTTPException(404, 'Чат не найден в пространстве')
        spaces.require(current['email'], chat['space_id'])
        return chat

    def load_profiles(cid):
        with store.connection() as db:
            row = db.execute('SELECT profiles FROM chat_profiles WHERE chat_id=?', (str(cid),)).fetchone()
        profiles = json.loads(row['profiles']) if row else {}
        if not profiles:
            raise HTTPException(409, 'Сохранённая конфигурация разговора недоступна')
        return profiles

    def effective_profile(chat):
        profile = profile_for(chat, load_profiles(chat['id']))
        effective = AppConfig.model_construct(**profile['effective'])
        with store.connection() as db:
            if db.execute('SELECT 1 FROM blocked_models WHERE model=?', (effective.model,)).fetchone():
                raise HTTPException(409, 'Сохранённая модель заблокирована администратором')
        return profile, effective

    def work_space(request, current):
        sid = request.headers.get('X-Space-ID', 'initial')
        spaces.require(current['email'], sid)
        return sid

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
        return {'demo': settings.demo, 'login_url': '/api/auth/yandex',
                'demo_login_url': '/api/auth/demo' if settings.demo else None,
                'vtb_edu_login_url': vtb_edu.START if settings.vtb_edu_ready else None}

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
        if access.oauth_identity(uid, email, profile.get('display_name') or email):
            response = login_response(uid, request.cookies.get(COOKIE))
        else:
            response = RedirectResponse('/?auth_error=denied', status_code=303)
        response.delete_cookie('gosboard_oauth', path='/api/auth')
        return response

    async def vtb_discovery():
        return await vtb_edu.discover(provider.client, settings.vtb_edu_discovery_url, settings.vtb_edu_issuer)

    @app.get(vtb_edu.START)
    async def vtb_start():
        if not settings.vtb_edu_ready:
            raise HTTPException(404)
        try:
            discovery = await vtb_discovery()
        except ValueError:
            access.audit('anonymous', 'auth.vtb-edu', '', {'result': 'discovery_failed'})
            return RedirectResponse('/?auth_error=vtb-edu', status_code=303)
        browser, attempt = vtb_edu.begin_attempt(store)
        location = vtb_edu.authorization_url(discovery, settings.vtb_edu_client_id,
                                             settings.origin + vtb_edu.CALLBACK, attempt)
        response = RedirectResponse(location, status_code=303)
        response.set_cookie(vtb_edu.ATTEMPT_COOKIE, browser, max_age=600, httponly=True,
                            secure=True, samesite='lax', path=vtb_edu.CALLBACK)
        return response

    @app.get(vtb_edu.CALLBACK)
    async def vtb_callback(request: Request, code: str = '', state: str = ''):
        if not settings.vtb_edu_ready:
            raise HTTPException(404)
        uid, outcome = None, 'invalid_attempt'
        attempt = vtb_edu.consume_attempt(store, request.cookies.get(vtb_edu.ATTEMPT_COOKIE), state)
        if attempt and code and len(code) <= 4096:
            try:
                discovery = await vtb_discovery()
                subject, email, name = await vtb_edu.exchange_identity(
                    provider.client, discovery, settings, code, attempt)
                uid = access.vtb_edu_identity(discovery.issuer, subject, email, name)
                outcome = 'authenticated' if uid else 'denied'
            except vtb_edu.IdentityError as error:
                outcome = str(error)
            except PyJWTError as error:
                outcome = type(error).__name__
            except (ValueError, TypeError, httpx.HTTPError):
                outcome = 'identity_not_verified'
        access.audit(uid or 'anonymous', 'auth.vtb-edu', '', {'result': outcome})
        response = (login_response(uid, request.cookies.get(COOKIE)) if uid
                    else RedirectResponse('/?auth_error=vtb-edu', status_code=303))
        response.delete_cookie(vtb_edu.ATTEMPT_COOKIE, path=vtb_edu.CALLBACK,
                               httponly=True, secure=True, samesite='lax')
        return response

    @app.get('/api/me')
    def me(current=Depends(session_user)):
        return {**{key: value for key, value in current.items() if key != 'requested_space'}, 'admin': current['role']=='admin', 'editor': current['role'] in {'editor','admin'}, 'demo': settings.demo}

    @app.post('/api/auth/logout')
    def logout(request: Request, current=Depends(session_user)):
        store.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.get('/api/spaces')
    def my_spaces(current=Depends(user)):
        return spaces.available(current['email'])

    @app.get('/api/management/spaces')
    def managed_spaces(current=Depends(editor)):
        return spaces.managed(current)

    @app.post('/api/management/spaces', status_code=201)
    def create_space(body: SpaceInput, current=Depends(editor)):
        policy = SpacePolicy(prompt=live_config.current().system_prompt)
        return spaces.create(body, current, policy.model_dump(mode='json'))

    @app.put('/api/management/spaces/{sid}')
    def edit_space(sid: str, body: SpaceInput, revision: int, current=Depends(editor)):
        spaces.update(sid, body, revision, current)
        return {'ok': True}

    @app.post('/api/management/spaces/{sid}/archive')
    async def archive_space(sid: str, body: ArchiveInput, current=Depends(editor)):
        spaces.archive(sid, body, current)
        cancel_space_tasks(sid)
        return {'ok': True}

    @app.put('/api/management/spaces/{sid}/assignments')
    async def assign_space(sid: str, body: SpaceAssignment, current=Depends(editor)):
        spaces.assign(sid, body.email, body.assigned, body.management, current)
        if not body.assigned and not body.management:
            cancel_space_tasks(sid, body.email)
        return {'ok': True}

    @app.get('/api/management/space-accounts')
    def space_accounts(current=Depends(editor)):
        return spaces.accounts(current)

    @app.get('/api/chats/archived')
    def archived_chats(current=Depends(user)):
        return spaces.history(current)

    @app.get('/api/config')
    def public_config(current=Depends(user)):
        return {'demo_prompt': live_config.current().demo_prompt, 'max_record_seconds': 25}

    @app.get('/api/management/spaces/{sid}/policy')
    def get_space_policy(sid: str, current=Depends(editor)):
        spaces.require_manager(sid, current)
        return {'policy': spaces.policy(sid), 'models': live_config.current().catalog()['models'],
                'bases': visible_bases(current, sid), 'web_enabled': settings.web_enabled}

    def policy_prompt(policy):
        selected = policy['prompt_id']
        if selected == 'default':
            return policy['prompt']
        for prompt in policy['prompts']:
            if prompt['id'] == selected:
                return prompt['system_prompt']
        raise HTTPException(422, 'Инструкция пространства недоступна')

    @app.put('/api/management/spaces/{sid}/policy')
    async def change_space_policy(sid: str, body: PolicyUpdate, current=Depends(editor)):
        spaces.require_manager(sid, current)
        policy = body.policy
        if not policy.prompt or not policy.prompt.strip():
            raise HTTPException(422, 'Введите основную инструкцию пространства')
        if policy.settings.prompt_id != 'default' or policy.settings.rag_enabled or policy.settings.knowledge_base_id:
            raise HTTPException(422, 'Укажите инструкцию и базы в политике пространства')
        bids = [str(bid) for bid in policy.base_ids]
        if len(set(bids)) != len(bids) or not set(bids) <= {base['id'] for base in visible_bases(current, sid)}:
            raise HTTPException(422, 'Проверьте список баз знаний')
        if policy.rag_enabled and not bids:
            raise HTTPException(422, 'Подключите базу знаний для поиска')
        prompt_ids = [prompt.id for prompt in policy.prompts]
        if 'default' in prompt_ids or len(prompt_ids) != len(set(prompt_ids)):
            raise HTTPException(422, 'Инструкции пространства должны иметь уникальные идентификаторы')
        web.validate_options(policy.settings)
        if policy.rag_enabled:
            ensure_bases_available(bids)
        try:
            for prompt in [policy_prompt(policy.model_dump()), *(item.system_prompt for item in policy.prompts)]:
                chat_profiles(live_config.current(), policy.settings, bids, prompt, policy.classic_text)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        spaces.save_policy(sid, body.revision, policy.model_dump(mode='json'), current)
        return {'ok': True}

    @app.get('/api/admin/models')
    def available_models(current=Depends(admin)):
        catalog = live_config.current()
        models = {model.model: model.label for model in catalog.models}
        models[catalog.model] = catalog.model_label
        with store.connection() as db:
            for row in db.execute('SELECT profiles FROM chat_profiles'):
                for profile in json.loads(row['profiles']).values():
                    effective = profile['effective']
                    models.setdefault(effective['model'], effective['model_label'])
            blocked = {row['model'] for row in db.execute('SELECT model FROM blocked_models')}
        return [{'id': model, 'label': label, 'blocked': model in blocked} for model, label in models.items()]

    @app.put('/api/admin/models/{model:path}')
    def block_model(model: str, body: ModelAccess, current=Depends(admin)):
        if model not in {item['id'] for item in available_models(current)}:
            raise HTTPException(404, 'Модель не найдена')
        with store.connection() as db:
            if body.blocked:
                db.execute('INSERT OR IGNORE INTO blocked_models VALUES (?)', (model,))
            else:
                db.execute('DELETE FROM blocked_models WHERE model=?', (model,))
            access.audit(current['id'], 'model.access', model, body.model_dump(), db)
        return {'ok': True}

    def read_rag_instruction(db):
        row = db.execute('SELECT instruction,revision FROM rag_instruction WHERE id=1').fetchone()
        return dict(row) if row else {'instruction': RAG_CONTRACT, 'revision': 0}

    @app.get('/api/admin/rag/instruction')
    def rag_instruction(current=Depends(admin)):
        with store.connection() as db:
            return read_rag_instruction(db)

    @app.put('/api/admin/rag/instruction')
    def update_rag_instruction(body: RagInstructionUpdate, current=Depends(admin)):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            before = read_rag_instruction(db)
            if before['revision'] != body.expected_revision:
                raise HTTPException(409, 'Инструкция уже изменена. Загрузите актуальную версию')
            if before['instruction'] == body.instruction:
                return before
            revision = before['revision'] + 1
            db.execute("""
                INSERT INTO rag_instruction (id, revision, instruction) VALUES (1,?,?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, instruction=excluded.instruction
            """, (revision, body.instruction))
            access.audit(current['id'], 'rag.instruction.save', str(revision), {}, db)
            return {'instruction': body.instruction, 'revision': revision}

    @app.get('/api/admin/config')
    def admin_config(current=Depends(admin)):
        config = live_config.current()
        return {'config': config.model_dump(), 'management': 'live; versioned in SQLite',
                'daily_limit_rub': store.budget()['day']['limit_rub'], 'monthly_limit_rub': store.budget()['month']['limit_rub']}

    @app.get('/api/editor/config')
    def editable_configuration(current=Depends(admin)):
        return live_config.read()

    @app.get('/api/editor/prompts')
    def list_prompts(current=Depends(admin)):
        return live_config.prompts()

    @app.post('/api/editor/prompts')
    async def create_prompt(body: PromptWrite, current=Depends(admin)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],label=body.label,text=body.system_prompt)

    @app.put('/api/editor/prompts/{pid}')
    async def edit_prompt(pid: str, body: PromptWrite, current=Depends(admin)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],pid,body.label,body.system_prompt)

    @app.delete('/api/editor/prompts/{pid}')
    async def delete_prompt(pid: str, body: PromptDelete, current=Depends(admin)):
        async with lock(('configuration',)):
            return live_config.change_prompt(body.expected_revision,current['id'],pid,delete=True)

    @app.get('/api/editor/config/versions')
    def configuration_versions(current=Depends(admin)):
        return live_config.versions()

    @app.get('/api/editor/config/versions/{revision}')
    def configuration_version(revision: int, current=Depends(admin)):
        return live_config.version(revision)

    @app.put('/api/editor/config')
    async def save_configuration(body: ConfigurationUpdate, current=Depends(admin)):
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
        changed = previous is not None and (previous['role']!=body.role or previous['enabled']!=int(body.enabled))
        with store.connection() as db:
            affected = {row['id'] for row in db.execute('SELECT id FROM users WHERE lower(email)=?', (body.email,))}
        if changed:
            for sid, email, _ in list(space_tasks):
                if email == body.email:
                    cancel_space_tasks(sid, email)
        for (uid, rid), task in list({**running,**request_tasks, **{key: stream.task for key, stream in reply_streams.items()}}.items()):
            if changed and uid in affected and task:
                task.cancel()
        return result

    imports = {}

    @app.post('/api/admin/users/import')
    async def preview_import(request: Request, space_id: list[str] = Query(default=[]), current=Depends(admin)):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_CSV_BYTES:
                raise HTTPException(413, 'Максимальный размер списка — 1 МБ')
        with store.connection() as db:
            rows = preview_accounts(db, content, space_id)
        now = time.monotonic()
        for key in list(imports):
            if imports[key]['expires'] <= now:
                imports.pop(key)
        if len(imports) >= 20:
            imports.pop(next(iter(imports)))
        batch = secrets.token_urlsafe(24)
        imports[batch] = {'owner': current['id'], 'rows': rows, 'expires': now + 900}
        return {'batch': batch, 'rows': rows}

    @app.post('/api/admin/users/import/{batch}')
    async def confirm_import(batch: str, body: ImportConfirm, current=Depends(admin)):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            spaces.require_role(current, db)
            result = db.execute('SELECT result FROM account_imports WHERE id=? AND actor=?',
                                (batch, current['id'])).fetchone()
            if result:
                return json.loads(result['result'])
            entry = imports.get(batch)
            if not entry or entry['owner'] != current['id'] or entry['expires'] <= time.monotonic():
                raise HTTPException(404, 'Предпросмотр истёк. Загрузите список заново')
            rows = entry['rows']
            if body.lines is not None:
                lines = set(body.lines)
                if not lines or not lines <= {row['line'] for row in rows if 'error' not in row}:
                    raise HTTPException(422, 'Выберите корректные строки списка')
                rows = [row for row in rows if row['line'] in lines]
            result = apply_accounts(db, batch, rows, current['id'], access, body.approve, body.approve_existing)
            db.execute('INSERT INTO account_imports VALUES (?,?,?,?)',
                       (batch, current['id'], json.dumps(result), time.time()))
        imports.pop(batch, None)
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
        return access.editor_chats(current)

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
    def chats(request: Request, current=Depends(user)):
        sid = work_space(request, current)
        return [public_chat(chat) for chat in store.chats(current['id'], sid)]

    @app.get('/api/space-defaults')
    def space_defaults(request: Request, current=Depends(user)):
        policy = spaces.policy(work_space(request, current))
        options = policy['settings']
        return public_chat({'settings': {**options, 'streaming_enabled':
                           not policy['classic_text'] or options['communication_mode'] != 'text'}})['settings']

    @app.post('/api/chats')
    async def new_chat(request: Request, body: ModeInput | None = None, current=Depends(user)):
        async with lock(('configuration',)):
            sid = work_space(request, current)
            policy_revision, policy = spaces.policy_record(sid)
            default_options = ChatOptions.model_validate(policy['settings'])
            default_options = default_options.model_copy(update={
                'streaming_enabled': not policy['classic_text'] or default_options.communication_mode != 'text'
            })
            try:
                bids = policy['base_ids'] if policy.get('rag_enabled', bool(policy['base_ids'])) else []
                profiles = chat_profiles(live_config.current(), default_options, bids,
                                         policy_prompt(policy), policy['classic_text'], policy_revision)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            mode = (body.communication_mode if body else None) or default_options.communication_mode or 'text'
            chat = store.new_chat(current['id'], profiles[mode]['settings'], sid=sid, profiles=profiles,
                                  authorize=lambda db: spaces.require(current['email'], sid, db))
            return public_chat(chat)

    @app.get('/api/chats/{cid}/export')
    async def download_chat(cid: UUID, format: str = 'md', current=Depends(user)):
        if format not in {'json', 'md', 'txt'}:
            raise HTTPException(422, 'Формат экспорта: md, txt или json')
        async with lock(('chat', str(cid))):
            chat = work_chat(cid, current)
            messages = [public_message(message) for message in store.messages(str(cid))]
            content, media_type = export_chat(public_chat(chat), messages, settings.origin, format)
        return Response(content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename="aitalks-{cid}.{format}"'})

    @app.patch('/api/chats/{cid}/mode')
    async def update_mode(cid: UUID, body: ModeInput, current=Depends(user)):
        async with lock(('chat', str(cid))):
            chat = work_chat(cid, current)
            profiles = load_profiles(cid)
            profile = profiles[body.communication_mode] if body.communication_mode else profile_for(chat, profiles)
            return public_chat(store.update_chat_settings(str(cid), current['id'], profile['settings']))

    def citation_available(db, source):
        if source['type'] == 'web':
            return safe_web_url(source.get('url'))
        if source['type'] != 'rag' or not settings.rag_enabled:
            return False
        return db.execute("""
            SELECT 1 FROM rag_documents d JOIN rag_bases b ON d.base_id=b.id
            WHERE d.id=? AND b.id=? AND d.deleted=0 AND b.deleted=0 AND NOT EXISTS (
                SELECT 1 FROM rag_revocations r WHERE r.base_id=b.id
            )
        """, (source.get('document_id'), source.get('base_id'))).fetchone() is not None

    def owner_message(db, message):
        result = public_message(message)
        citations = [source for source in message['metadata'].get('citations', [])
                     if source.get('type') in {'rag', 'web'}] if message['role'] == 'assistant' else []
        if not citations:
            return result
        available = []
        for source in citations:
            permitted = citation_available(db, source)
            available.append({'id': source['id'], 'type': source['type'], 'available': permitted,
                              'title': source['title'] if permitted else 'Источник недоступен',
                              'page': source.get('page') if permitted else None})
        result['metadata']['citations'] = available
        markers = {source['id'] for source in citations}
        def project(match):
            ids = re.findall(r'[DW]\d+', match.group().split(']', 1)[0])
            return ' '.join(f'[{key}]' for key in ids if key in markers)
        result['text'] = SOURCE_MARKER.sub(project, message['text'])
        return result

    @app.get('/api/chats/{cid}/messages')
    def messages(cid: UUID, current=Depends(user)):
        chat = store.chat(str(cid), current['id'])
        saved = store.messages(str(cid))
        with store.connection() as db:
            db.execute('BEGIN')
            try:
                if current['requested_space'] not in (None, chat['space_id']):
                    raise HTTPException(404)
                spaces.require(current['email'], chat['space_id'], db)
            except HTTPException:
                return [public_message(message) for message in saved]
            return [owner_message(db, message) for message in saved]

    @app.get('/api/chats/{cid}/messages/{mid}/citations/{citation_id}')
    def citation_preview(cid: UUID, mid: UUID, citation_id: str, current=Depends(user)):
        chat = work_chat(cid, current)
        with store.connection() as db:
            db.execute('BEGIN')
            spaces.require(current['email'], chat['space_id'], db)
            row = db.execute("SELECT metadata FROM messages WHERE id=? AND chat_id=? AND role='assistant'",
                             (str(mid), str(cid))).fetchone()
            source = next((item for item in json.loads(row['metadata']).get('citations', [])
                           if item['id'] == citation_id), None) if row else None
            if not source:
                raise HTTPException(404, 'Источник не найден в этом ответе')
            if not citation_available(db, source):
                raise HTTPException(410, 'Источник недоступен')
            fields = ['id', 'type', 'title', 'excerpt', 'page']
            if source['type'] == 'web':
                fields.extend(['url', 'content_kind', 'fetched_at', 'page_fetched_at'])
            return {key: source[key] for key in fields if key in source}

    @app.delete('/api/chats/{cid}')
    async def delete_chat(cid: UUID, current=Depends(user)):
        async with lock(('chat', str(cid))):
            work_chat(cid, current)
            store.delete_chat(str(cid), current['id'])
        return {'ok': True}

    @app.post('/api/requests/{rid}/cancel')
    async def cancel(rid: UUID, current=Depends(user)):
        stream = reply_streams.get((current['id'], str(rid)))
        task = (stream.task if stream else None) or request_tasks.get((current['id'],str(rid))) or running.get((current['id'], str(rid)))
        if not task:
            task = next((task for (_, email, request_id), task in space_tasks.items()
                         if email == current['email'] and request_id == str(rid)), None)
        if task:
            task.cancel()
        return {'cancel_requested': task is not None}

    async def paid_operation(uid, kind, amount, operation, request_id):
        reserve_id = None
        if not settings.demo:
            reserve_id = store.reserve(uid, kind, amount, settings.daily_limit, settings.monthly_limit, operation_space.get())
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

    def authorize_document_job(document):
        actor = document['job_actor']
        with store.connection() as db:
            account = db.execute("""
                SELECT a.email,a.role FROM users u JOIN access_accounts a ON a.email=lower(u.email)
                WHERE u.id=? AND a.enabled=1
            """, (actor,)).fetchone()
        if not account or account['role'] not in {'admin','editor'}:
            raise HTTPException(403, 'Доступ к обработке документа отозван')
        require_base_manager(document['base_id'], account)

    query_resolver = RetrievalQuery(store, provider, paid)
    rag = KnowledgeEngine(settings, store, provider, paid)
    web = WebSearch(settings, store, provider, paid)
    app.state.web = web
    app.state.rag = rag
    app.state.live_config = live_config
    rag.authorize_job = authorize_document_job

    def managed_base_ids(current):
        with store.connection() as db:
            return {row['base_id'] for row in db.execute('''
                SELECT base_id FROM rag_owners WHERE email=?
                UNION SELECT base_id FROM rag_managers WHERE email=?
            ''', (current['email'], current['email']))}

    def visible_bases(current, sid=None):
        if not settings.rag_enabled:
            return []
        visible = managed_base_ids(current)
        if sid:
            visible.update(spaces.policy(sid)['base_ids'])
        return [base for base in rag.repo.bases() if current['role']=='admin' or base['id'] in visible]

    def require_base_manager(bid, current):
        with store.connection() as db:
            spaces.require_role(current, db)
        if current['role'] != 'admin' and bid not in managed_base_ids(current):
            raise HTTPException(404, 'База знаний недоступна')

    def ensure_bases_available(bids, revisions=None):
        with store.connection() as db:
            for bid in bids:
                if db.execute('SELECT 1 FROM rag_revocations WHERE base_id=?', (bid,)).fetchone():
                    raise HTTPException(409, 'Поиск в сохранённой базе знаний отозван')
                if revisions is not None:
                    base = db.execute('SELECT revision,deleted FROM rag_bases WHERE id=?', (bid,)).fetchone()
                    if not base or base['deleted'] or base['revision'] != revisions[bid]:
                        raise HTTPException(409, 'База знаний изменилась во время поиска. Повторите запрос')

    async def retrieve_bases(bids, query, uid, max_chars, rid, includes_web):
        ensure_bases_available(bids)
        candidates, revisions, policies = [], {}, []
        for bid in bids:
            policies.append(rag.repo.base(bid))
            found, revisions[bid] = await rag.retrieve(bid, query, uid, max_chars, rid,
                                                       space_id=operation_space.get())
            candidates.extend(found)
        sources, documents = [], set()
        top_k = min(5 if includes_web else 10, max(base['top_k'] for base in policies))
        max_documents = max(base['top_k_documents'] for base in policies)
        for source in sorted(candidates, key=lambda item: (-item['score'], item['chunk_id'])):
            did = source['document_id']
            if did not in documents and len(documents) >= max_documents:
                continue
            if len(source['excerpt']) > max_chars:
                continue
            documents.add(did)
            max_chars -= len(source['excerpt'])
            sources.append({**source, 'id': f'D{len(sources)+1}'})
            if len(sources) == top_k:
                break
        return sources, revisions

    @app.get('/api/knowledge-bases/{bid}/managers')
    def base_manager_options(bid: UUID, current=Depends(admin)):
        rag.require_enabled()
        bid = str(bid)
        rag.repo.base(bid)
        with store.connection() as db:
            rows = db.execute('''
                SELECT a.email, m.email IS NOT NULL AS assigned, o.email IS NOT NULL AS owner
                FROM access_accounts a
                LEFT JOIN rag_managers m ON m.email=a.email AND m.base_id=?
                LEFT JOIN rag_owners o ON o.email=a.email AND o.base_id=?
                WHERE a.role='editor' AND a.enabled=1
                ORDER BY a.email
            ''', (bid, bid)).fetchall()
        return [{'email': row['email'], 'assigned': bool(row['assigned']), 'owner': bool(row['owner'])}
                for row in rows]

    @app.put('/api/knowledge-bases/{bid}/managers')
    def designate_base_manager(bid: UUID, body: SpaceAssignment, current=Depends(admin)):
        bid = str(bid)
        rag.repo.base(bid)
        account = access.account(body.email)
        if body.assigned and (not account or account['enabled'] != 1 or account['role'] != 'editor'):
            raise HTTPException(422, 'Укажите допущенного редактора')
        with store.connection() as db:
            if body.assigned:
                db.execute('INSERT OR IGNORE INTO rag_managers VALUES (?,?)', (bid, body.email))
            else:
                db.execute('DELETE FROM rag_managers WHERE base_id=? AND email=?', (bid, body.email))
            access.audit(current['id'], 'rag.manager', bid, {'email': body.email, 'assigned': body.assigned}, db)
        return {'ok': True}

    @app.post('/api/knowledge-bases/{bid}/revoke')
    async def revoke_base(bid: UUID, current=Depends(editor)):
        rag.require_enabled()
        bid = str(bid)
        require_base_manager(bid, current)
        rag.repo.base(bid)
        with store.connection() as db:
            db.execute('INSERT OR IGNORE INTO rag_revocations VALUES (?,?)', (bid, time.time()))
            access.audit(current['id'], 'rag.revoke', bid, {}, db)
        for task, bases in list(retrieval_tasks.items()):
            if bid in bases:
                task.cancel()
        return {'ok': True}

    @app.get('/api/knowledge-bases')
    def bases(current=Depends(editor)):
        rag.require_enabled()
        return visible_bases(current)

    @app.post('/api/knowledge-bases')
    def create_base(body: BaseInput, current=Depends(editor)):
        rag.require_enabled()
        def register(db, bid):
            spaces.require_role(current, db)
            db.execute('INSERT INTO rag_owners VALUES (?,?)', (bid, current['email']))
            access.audit(current['id'], 'rag.create', bid, {}, db)

        return rag.repo.create_base(body.name, register)

    @app.put('/api/knowledge-bases/{bid}/policy')
    async def tune_base(bid: UUID, body: RagPolicy, current=Depends(editor)):
        rag.require_enabled()

        def authorize():
            require_base_manager(str(bid), current)

        authorize()
        result = await rag.tune(str(bid), body, actor=current['id'], authorize=authorize)
        access.audit(current['id'], 'rag.policy', str(bid), body.model_dump())
        return result

    @app.get('/api/knowledge-bases/{bid}/documents')
    def documents(bid: UUID, current=Depends(editor)):
        rag.require_enabled()
        require_base_manager(str(bid), current)
        return rag.repo.documents(str(bid))

    @app.post('/api/knowledge-bases/{bid}/documents', status_code=202)
    async def upload_document(bid: UUID, request: Request, filename: str, current=Depends(editor)):
        rag.require_enabled()
        require_base_manager(str(bid), current)
        rag.repo.base(str(bid))
        body = bytearray()
        async for block in request.stream():
            body.extend(block)
            if len(body) > MAX_UPLOAD:
                raise HTTPException(413, 'Максимальный размер документа — 10 МБ')
        try:
            require_base_manager(str(bid), current)
            document = await rag.upload(str(bid), filename, request.headers.get('Content-Type', ''), bytes(body), current['id'], actor=current['id'])
            access.audit(current['id'], 'rag.upload', str(bid), {'document_id': document['id']})
            return document
        finally:
            body.clear()

    @app.delete('/api/knowledge-bases/{bid}')
    async def delete_base(bid: UUID, current=Depends(editor)):
        rag.require_enabled()

        def authorize():
            require_base_manager(str(bid), current)

        authorize()
        await rag.remove_base(str(bid), authorize=authorize)
        access.audit(current['id'], 'rag.base.delete', str(bid), {})
        return {'ok': True}

    @app.post('/api/knowledge-bases/{bid}/reindex')
    async def reindex_base(bid: UUID, current=Depends(editor)):
        rag.require_enabled()

        def authorize():
            require_base_manager(str(bid), current)

        authorize()
        await rag.reindex(str(bid), actor=current['id'], authorize=authorize)
        access.audit(current['id'], 'rag.reindex', str(bid), {})
        return {'queued': True}

    @app.delete('/api/documents/{did}')
    async def delete_document(did: UUID, current=Depends(editor)):
        rag.require_enabled()
        document = rag.repo.document(str(did))
        require_base_manager(document['base_id'], current)
        await rag.remove_document(str(did))
        access.audit(current['id'], 'rag.delete', document['base_id'], {'document_id': str(did)})
        return {'ok': True}

    @app.post('/api/documents/{did}/retry')
    async def retry_document(did: UUID, current=Depends(editor)):
        rag.require_enabled()
        document = rag.repo.document(str(did))
        require_base_manager(document['base_id'], current)
        await rag.retry(str(did), actor=current['id'])
        access.audit(current['id'], 'rag.retry', document['base_id'], {'document_id': str(did)})
        return {'queued': True}

    @app.get('/api/documents/{did}/file')
    async def original_document(did: UUID, current=Depends(editor)):
        rag.require_enabled()
        document = rag.repo.document(str(did))
        require_base_manager(document['base_id'], current)
        rag.repo.base(document['base_id'])
        if document['status'] in {'queued_upload', 'uploading'}:
            raise HTTPException(409, 'Документ ещё сохраняется')
        path = await rag.original(document)
        with store.connection() as db:
            spaces.require_role(current, db)
        require_base_manager(document['base_id'], current)
        return FileResponse(path, media_type=document['mime'], filename=document['filename'], content_disposition_type='inline')

    @app.get('/api/sources/{chunk_id}')
    def source(chunk_id: str, current=Depends(editor)):
        rag.require_enabled()
        if not re.fullmatch(r'[0-9a-f]{64}', chunk_id):
            raise HTTPException(404, 'Фрагмент не найден')
        chunk = rag.repo.source(chunk_id)
        require_base_manager(rag.repo.document(chunk['document_id'])['base_id'], current)
        return {'chunk_id': chunk['id'], 'document_id': chunk['document_id'], 'title': chunk['filename'],
                'base_name': chunk['base_name'], 'page': chunk['page'], 'position': chunk['position'],
                'text': chunk['text'], 'document_url': '/api/documents/' + chunk['document_id'] + '/file'}

    @app.get('/api/web-sources/{source_id}')
    def web_source(source_id: str, current=Depends(admin)):
        if not re.fullmatch(r'[0-9a-f]{64}',source_id):
            raise HTTPException(404,'Источник не найден')
        return web.source(source_id,current['id'])

    async def run_send(cid: UUID, body: MessageInput, current, stream=None):
        cid, rid, uid = str(cid), str(body.request_id), current['id']
        work_chat(cid, current)
        async with lock(('chat', cid)):
            fingerprint = cache_key(cid, body.text, body.regenerate)
            previous = store.begin_request(rid, uid, 'llm', fingerprint)
            if previous is not None:
                return {**previous, 'metadata': public_metadata(previous['metadata'])}
            started = time.monotonic()
            committed = False
            request_tasks[(uid,rid)] = asyncio.current_task()
            try:
                chat = work_chat(cid, current)
                options = ChatOptions.model_validate(chat['settings'])
                profile, effective = effective_profile(chat)
                base_ids = profile['base_ids']
                sources_enabled = bool(base_ids) or options.web_search_enabled
                if stream:
                    stream.speech_config = effective
                    stream.auto_speech = options.auto_speech
                    await stream.emit('status', {'text': 'Формирую ответ'})
                sources, base_revision, web_info = [], None, None
                history = store.messages(cid, options.history_messages)
                decision, query_info = {'route': 'direct'}, {'contextual': False}
                if sources_enabled:
                    async with slot(llm_slots):
                        decision, query_info = await query_resolver.resolve(
                            uid, cid, body.text, history, effective, rid, settings.llm_reserve,
                            rag=bool(base_ids), web=options.web_search_enabled,
                        )
                grounded = decision['route'] == 'search'
                clarification = decision.get('question')
                if grounded:
                    if base_ids:
                        ensure_bases_available(base_ids)
                        retrieval_tasks[asyncio.current_task()] = base_ids
                    web.validate_options(options)
                    retrieval_query = decision['query']
                    with store.connection() as db:
                        instruction = read_rag_instruction(db)['instruction']
                    max_source_chars = min(5500,effective.context_chars-len(effective.system_prompt)-len(RAG_MARKER)-len(instruction)-len(body.text)-1000)
                    if max_source_chars<1200:
                        raise HTTPException(422,'Недостаточно места для источников. Сократите запрос или инструкцию')
                    rag_budget = max_source_chars//2 if options.web_search_enabled else max_source_chars
                    if base_ids:
                        sources, base_revision = await retrieve_bases(base_ids, retrieval_query, uid, rag_budget, rid,
                                                                       options.web_search_enabled)
                    if options.web_search_enabled:
                        web_sources,web_info = await web.retrieve(retrieval_query,uid,rid,max_source_chars-sum(len(source['excerpt']) for source in sources), space_id=chat['space_id'])
                        web_sources = web_sources[:10-len(sources)]
                        sources += web_sources
                    if sources:
                        while sources:
                            source_data=[{**{key:source[key] for key in ('id','title','excerpt','type')},'content_kind':source.get('content_kind','document_chunk')} for source in sources]
                            system=effective.system_prompt+'\n'+grounding_instruction(sources, instruction)+('\nRESOLVED_QUESTION='+json.dumps(retrieval_query,ensure_ascii=False) if query_info['rewritten'] else '')+'\nSOURCE_DATA='+json.dumps(source_data,ensure_ascii=False)
                            if len(system)+len(body.text)<=effective.context_chars:
                                break
                            sources.pop()
                        if not sources:
                            raise HTTPException(422,'Источники не помещаются в контекст. Сократите запрос или инструкцию')
                        effective=effective.model_copy(update={'system_prompt':system})
                snapshot = effective.snapshot()
                config_hash = store.save_configuration(snapshot)
                if sources_enabled:
                    # Source IDs are numbered afresh each turn. Historical markers must not be mistaken for fresh evidence.
                    history = [{**message, 'text': re.sub(r'\[(?:[DW]\d+)(?:\s*,\s*[DW]\d+)*\]', '', message['text']) if message['role']=='assistant' else message['text']} for message in history]
                messages = context_messages(history, body.text, effective, options.history_messages)
                key = cache_key(
                    'llm-v5', completion_protocol(effective.model), chat['space_id'], uid, settings.demo,
                    snapshot, options.model_dump(exclude={'streaming_enabled','auto_speech','communication_mode','review_before_send','live_pause'}),
                    base_revision, [source['source_id'] for source in sources if source['type']=='web'], messages,
                    *(['direct-route-v1'] if sources_enabled and not grounded else []),
                )
                cached = None if body.regenerate or clarification else store.get_cache(key, uid, 'llm')
                preview = ''
                async with slot(llm_slots):
                    if cached:
                        data = json.loads(cached)
                        answer, meta = data['text'], data['metadata']
                    elif clarification:
                        answer = clarification
                        meta = {'citations': [], 'grounding': 'clarification', 'model_called': False}
                    elif grounded and not sources:
                        answer, meta = WEB_UNSUPPORTED if options.web_search_enabled else UNSUPPORTED, {'citations': [], 'grounding': 'no_evidence', 'model_called': False}
                    else:
                        raw = ''
                        async def delta(part):
                            nonlocal raw, preview
                            raw += part
                            visible = provisional_answer(raw) if grounded else raw
                            # Keep an unfinished bracketed marker out of incremental output.
                            visible = INCOMPLETE_MARKER.sub('', visible)
                            visible = SOURCE_MARKER.sub('', visible)
                            if visible.startswith(preview) and len(visible) > len(preview):
                                addition = visible[len(preview):]
                                preview = visible
                                await stream.emit('delta', {'text': addition, 'provisional': True})
                                if not grounded:
                                    stream.feed(addition)
                        async def completion():
                            if stream and hasattr(provider, 'complete_stream'):
                                return await provider.complete_stream(messages, effective, delta)
                            return await provider.complete(messages, effective)
                        answer, meta = await paid(uid, 'llm', settings.llm_reserve, completion, rid)
                        if grounded:
                            answer,citations,supported = validate_rag_answer(answer,sources,WEB_UNSUPPORTED if options.web_search_enabled else UNSUPPORTED)
                            rag.verify_sources_live([source for source in citations if source['type']=='rag'])
                            meta.update(citations=citations, grounding='supported' if supported else 'insufficient_evidence', base_version=base_revision)
                        elif sources_enabled:
                            meta.update(citations=[], grounding='not_requested')
                        store.put_cache(key, uid, 'llm', json.dumps({'text': answer, 'metadata': meta}).encode(), 86400)
                if grounded and base_ids:
                    ensure_bases_available(base_ids, base_revision)
                    rag.verify_sources_live([source for source in meta.get('citations',[]) if source['type']=='rag'])
                metadata = {**meta, 'config_version': effective.version, 'config_hash': config_hash,
                            'model': effective.model, 'model_label': effective.model_label,
                            'temperature': effective.temperature, 'max_tokens': effective.max_tokens,
                            'chat_settings': options.model_dump(),
                            'delivery': {'streaming': stream is not None, 'auto_speech': bool(stream and options.auto_speech)},
                           'context': {'history_messages_limit':options.history_messages,'history_messages_included':len(messages)-2 if meta.get('model_called',True) else 0,'input_chars':sum(len(m['text']) for m in messages) if meta.get('model_called',True) else 0,'character_limit':effective.context_chars},
                            'prompt_label': options.prompt_id, 'space_policy_revision': profile.get('policy_revision'),
                            'retrieval': {'context_query':query_info,'rag_enabled':bool(grounded and base_ids),'web_search_enabled':bool(grounded and options.web_search_enabled),'rag_sources':sum(source['type']=='rag' for source in sources),'web':web_info},
                            'cached': bool(cached), 'elapsed_ms': round((time.monotonic() - started) * 1000)}
                work_chat(cid, current)
                aid = store.add_turn(cid, body.text, answer, metadata)
                result = {'message_id': aid, 'metadata': public_metadata(metadata)}
                store.end_request(rid, result)
                committed = True
                if stream:
                    display_text = SOURCE_MARKER.sub('', answer)
                    with store.connection() as db:
                        visible = owner_message(db, {'role':'assistant', 'text':answer, 'metadata':metadata})
                    await stream.emit('answer', {'message_id':aid, 'text':visible['text'], 'metadata':visible['metadata']})
                    if grounded or cached:
                        stream.feed(display_text)
                    elif display_text.startswith(preview):
                        stream.feed(display_text[len(preview):])
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
                retrieval_tasks.pop(asyncio.current_task(), None)
                request_tasks.pop((uid,rid),None)

    @app.post('/api/chats/{cid}/messages')
    async def send(cid: UUID, body: MessageInput, current=Depends(user)):
        chat = work_chat(cid, current)
        async with space_operation(chat['space_id'], current, str(body.request_id)):
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
        chat = work_chat(cid, current)
        if not chat['settings']['streaming_enabled']:
            raise HTTPException(409, 'Включите потоковый ответ в настройках разговора')
        if (uid, rid) in reply_streams:
            raise HTTPException(409, 'Этот ответ уже формируется. Дождитесь завершения или проверьте историю')

        async def synthesize(phrase, config):
            key = cache_key('tts-v2', chat['space_id'], uid, settings.demo, phrase, config.voice, config.tts_model, config.tts_speed, 'wav')
            async with lock(('tts', key)):
                cached = store.get_cache(key, uid, 'tts')
                if cached is None:
                    async with slot(speech_slots):
                        data = await paid(uid, 'tts', settings.tts_reserve * len(split_text(phrase)),
                                          lambda: provider.synthesize(phrase, config), rid + ':audio')
                    spaces.require(current['email'], chat['space_id'])
                    store.put_cache(key, uid, 'tts', data, settings.audio_ttl)
            return {'url': '/api/audio/' + key, 'cached': cached is not None}

        stream = ReplyStream(synthesize)
        reply_streams[(uid, rid)] = stream

        async def produce():
            try:
                async with space_operation(chat['space_id'], current, rid):
                    await produce_reply()
            except HTTPException as error:
                await stream.emit('error', {'detail': error.detail})
                reply_streams.pop((uid, rid), None)

        async def produce_reply():
            try:
                stream.audio_task = asyncio.create_task(stream.audio_worker())
                result = await run_send(cid, body, current, stream)
                # A retry of an already completed request must not regenerate text or speech.
                if not stream.speech_config:
                    message = next((m for m in store.messages(str(cid)) if m['id'] == result['message_id']), None)
                    if message:
                        with store.connection() as db:
                            visible = owner_message(db, message)
                        await stream.emit('answer', {**result, 'text':visible['text'], 'metadata':visible['metadata']})
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
        sid = work_space(request, current)
        key = cache_key('stt-v2', sid, uid, settings.demo, digest(bytes(audio)), 'lpcm', 16000, 'ru-RU')
        async with space_operation(sid, current, rid), lock(('stt', key)):
            previous = store.begin_request(rid, uid, 'stt', key)
            if previous is not None:
                return previous
            started = time.monotonic()
            try:
                cached = store.get_cache(key, uid, 'stt')
                async with slot(speech_slots):
                    text = cached.decode() if cached else await paid(uid, 'stt', settings.stt_reserve, lambda: provider.transcribe(bytes(audio)), rid)
                spaces.require(current['email'], sid)
                store.put_cache(key, uid, 'stt', text.encode(), 3600)
                result = {'text': text, 'cached': bool(cached), 'elapsed_ms': round((time.monotonic() - started) * 1000), 'demo': settings.demo}
                store.end_request(rid, result)
                return result
            except asyncio.CancelledError:
                store.end_request(rid)
                raise HTTPException(409, 'Распознавание остановлено') from None
            except BaseException:
                store.end_request(rid)
                raise
            finally:
                audio.clear()  # Raw audio is never persisted.

    @app.post('/api/chats/{cid}/messages/{mid}/speech')
    async def speech(cid: UUID, mid: UUID, body: SpeechInput, current=Depends(user)):
        cid, mid, uid, rid = str(cid), str(mid), current['id'], str(body.request_id)
        sid = work_chat(cid, current)['space_id']
        async with space_operation(sid, current, rid), lock(('chat', cid)):
            chat = work_chat(cid, current)
            _, speech_config = effective_profile(chat)
            message = next((m for m in store.messages(cid) if m['id'] == mid and m['role'] == 'assistant'), None)
            if not message:
                raise HTTPException(404, 'Ответ не найден')
            if len(message['text']) > 6000:
                raise HTTPException(422, 'Ответ слишком длинный для озвучивания; попросите более краткий')
            spoken_text = SOURCE_MARKER.sub('', message['text']).strip()
            key = cache_key('tts-v2', chat['space_id'], uid, settings.demo, spoken_text, speech_config.voice, speech_config.tts_model, speech_config.tts_speed, 'wav')
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
                            spaces.require(current['email'], sid)
                            store.put_cache(key, uid, 'tts', audio, settings.audio_ttl)
                    result = {'url': f'/api/audio/{key}', 'cached': cached is not None,
                              'elapsed_ms': round((time.monotonic() - started) * 1000), 'demo': settings.demo}
                    store.end_request(rid, result)
                    return result
                except asyncio.CancelledError:
                    store.end_request(rid)
                    raise HTTPException(409, 'Озвучивание остановлено') from None
                except BaseException:
                    store.end_request(rid)
                    raise

    @app.get('/api/audio/{key}')
    def audio(key: str, current=Depends(user)):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise HTTPException(404)
        with store.connection() as db:
            row = db.execute("SELECT value,space_id FROM cache WHERE key=? AND user_id=? AND kind='tts' AND expires>?",
                             (key, current['id'], time.time())).fetchone()
        if not row:
            raise HTTPException(404, 'Аудио истекло. Запросите озвучивание снова')
        spaces.require(current['email'], row['space_id'])
        return Response(bytes(row['value']), media_type='audio/wav')

    @app.exception_handler(TimeoutError)
    async def timeout_handler(request, exc):
        return JSONResponse({'detail': 'Превышено время ожидания. Проверьте историю перед повтором'}, status_code=504)

    return app
