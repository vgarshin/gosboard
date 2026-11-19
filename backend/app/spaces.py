"""Space membership and administration backed by the application's SQLite store."""
import json
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .access import AccountInput
from .configuration import ChatOptions, PromptOption


class SpaceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    revision: int


class SpaceAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str
    assigned: bool
    management: bool = False

    @field_validator('email')
    @classmethod
    def validate_email(cls, email):
        return email if email == "demo@localhost" else AccountInput(email=email).email


class Spaces:
    def __init__(self, store, access):
        self.store = store
        self.access = access
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, archived REAL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS space_memberships (
                    email TEXT REFERENCES access_accounts(email), space_id TEXT REFERENCES spaces(id),
                    PRIMARY KEY(email,space_id));
                CREATE TABLE IF NOT EXISTS account_imports (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS blocked_models (model TEXT PRIMARY KEY);
                CREATE TRIGGER IF NOT EXISTS immutable_chat_space
                BEFORE UPDATE OF space_id ON chats WHEN NEW.space_id != OLD.space_id
                BEGIN SELECT RAISE(ABORT,'Conversation space is immutable'); END;
                CREATE TABLE IF NOT EXISTS rag_owners (
                    base_id TEXT PRIMARY KEY, email TEXT NOT NULL REFERENCES access_accounts(email));
                CREATE TABLE IF NOT EXISTS rag_managers (
                    base_id TEXT NOT NULL, email TEXT REFERENCES access_accounts(email),
                    PRIMARY KEY(base_id,email));
                CREATE TABLE IF NOT EXISTS rag_revocations (
                    base_id TEXT PRIMARY KEY, revoked REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS space_policies (
                    space_id TEXT REFERENCES spaces(id), revision INTEGER NOT NULL, policy TEXT NOT NULL,
                    created REAL NOT NULL, actor TEXT NOT NULL, PRIMARY KEY(space_id,revision));
                CREATE TABLE IF NOT EXISTS space_editors (
                    email TEXT REFERENCES access_accounts(email), space_id TEXT REFERENCES spaces(id),
                    PRIMARY KEY(email,space_id));
            """)
            db.execute('BEGIN IMMEDIATE')
            for table in ('space_memberships', 'space_editors'):
                columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if 'active' not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
                    db.execute(f'ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            if not db.execute("SELECT 1 FROM access_migrations WHERE name='spaces-v1'").fetchone():
                db.execute("INSERT INTO spaces(id,name,description,created) VALUES ('initial',?,?,?)",
                           ('Основное пространство', 'Ваши разговоры и история AI Talks', time.time()))
                db.execute("INSERT INTO space_memberships(email,space_id) SELECT email,'initial' FROM access_accounts WHERE enabled=1")
                db.execute("INSERT INTO access_migrations VALUES ('spaces-v1')")

    def available(self, email):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("""
                SELECT s.id,s.name,s.description FROM spaces s
                JOIN space_memberships m ON m.space_id=s.id
                WHERE m.email=? AND m.active=1 AND s.archived IS NULL ORDER BY s.name,s.id
            """, (email,))]

    def require(self, email, sid, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.require(email, sid, connection)
        if not db.execute("""
            SELECT 1 FROM space_memberships m JOIN spaces s ON s.id=m.space_id
            JOIN access_accounts a ON a.email=m.email
            WHERE m.email=? AND m.space_id=? AND m.active=1 AND s.archived IS NULL AND a.enabled=1
        """, (email, sid)).fetchone():
            raise HTTPException(403, 'Пространство недоступно')

    def managed(self, current):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("""
                SELECT s.*,
                    (SELECT count(*) FROM chats c WHERE c.space_id=s.id) AS chats,
                    (SELECT count(*) FROM space_memberships m WHERE m.space_id=s.id AND m.active=1) AS members,
                    (SELECT count(*) FROM space_memberships m JOIN access_accounts a ON a.email=m.email
                     WHERE m.space_id=s.id AND m.active=1 AND a.enabled=1 AND NOT EXISTS (
                        SELECT 1 FROM space_memberships other JOIN spaces os ON os.id=other.space_id
                        WHERE other.email=m.email AND other.active=1 AND os.id!=s.id AND os.archived IS NULL
                     )) AS affected_members
                FROM spaces s WHERE ?='admin' OR EXISTS (
                    SELECT 1 FROM space_editors e WHERE e.space_id=s.id AND e.email=? AND e.active=1
                ) ORDER BY s.name,s.id
            """, (current['role'], current['email']))]

    def create(self, body, current, policy):
        sid = str(uuid4())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_role(current, db)
            db.execute('INSERT INTO spaces(id,name,description,created) VALUES (?,?,?,?)',
                       (sid, body.name, body.description, time.time()))
            db.execute('INSERT INTO space_policies VALUES (?,?,?,?,?)', (sid, 1, json.dumps(policy), time.time(), current['id']))
            self.access.audit(current['id'], 'space.create', sid, {}, db)
        return {'id': sid}

    def update(self, sid, body, revision, current):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_manager(sid, current, db)
            changed = db.execute('''
                UPDATE spaces SET name=?,description=?,revision=revision+1
                WHERE id=? AND revision=? AND archived IS NULL
            ''', (body.name, body.description, sid, revision))
            if not changed.rowcount:
                raise HTTPException(409, 'Пространство изменилось. Обновите данные')
            self.access.audit(current['id'], 'space.update', sid, {'revision':revision+1}, db)

    def archive(self, sid, body, current):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_role(current, db)
            result = db.execute("""
                UPDATE spaces SET archived=?,revision=revision+1
                WHERE id=? AND name=? AND revision=? AND archived IS NULL
            """, (time.time(), sid, body.name, body.revision))
            if not result.rowcount:
                raise HTTPException(409, 'Проверьте название и обновите данные пространства')
            for table in ('space_memberships', 'space_editors'):
                db.execute(f'UPDATE {table} SET active=0,revision=revision+1 WHERE space_id=? AND active=1', (sid,))
            self.access.audit(current['id'], 'space.archive', sid, {}, db)

    def assign(self, sid, email, assigned, management, current):
        table = 'space_editors' if management else 'space_memberships'
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_manager(sid, current, db)
            account = db.execute('SELECT role,enabled FROM access_accounts WHERE email=?', (email,)).fetchone()
            if not account or (current['role'] != 'admin' and (management or account['role'] != 'user' or account['enabled'] != 1)):
                raise HTTPException(404, 'Аккаунт недоступен для назначения')
            if management and assigned and (account['role'] != 'editor' or not account['enabled']):
                raise HTTPException(422, 'Управление назначается допущенному редактору')
            if assigned:
                db.execute(f'''
                    INSERT INTO {table}(email,space_id) VALUES (?,?)
                    ON CONFLICT(email,space_id) DO UPDATE SET active=1,revision=revision+1 WHERE active=0
                ''', (email, sid))
            else:
                db.execute(f'UPDATE {table} SET active=0,revision=revision+1 WHERE email=? AND space_id=? AND active=1',
                           (email, sid))
            self.access.audit(current['id'], 'space.assignment', sid,
                              {'email': email, 'assigned': assigned, 'management': management}, db)

    def accounts(self, current):
        with self.store.connection() as db:
            accounts = [dict(row) for row in db.execute("""
                SELECT email, role, CASE enabled WHEN 1 THEN 'active'
                    WHEN -1 THEN 'pending' ELSE 'disabled' END AS status
                FROM access_accounts WHERE ?='admin' OR (enabled=1 AND role='user')
                ORDER BY email
            """, (current['role'],))]
            visible = {space['id'] for space in self.managed(current) if space['archived'] is None}
            assignments = {}
            for table in ('space_memberships', 'space_editors'):
                assignments[table] = {}
                for row in db.execute(f'SELECT email,space_id FROM {table} WHERE active=1'):
                    if row['space_id'] in visible:
                        assignments[table].setdefault(row['email'], []).append(row['space_id'])
        return [{**account, 'memberships': assignments['space_memberships'].get(account['email'], []),
                 'managed': assignments['space_editors'].get(account['email'], [])} for account in accounts]

    def history(self, current):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("""
                SELECT c.id,c.title,c.updated,s.name AS space_name
                FROM chats c JOIN spaces s ON s.id=c.space_id
                WHERE c.user_id=? AND (s.archived IS NOT NULL OR NOT EXISTS (
                    SELECT 1 FROM space_memberships m WHERE m.email=? AND m.space_id=s.id AND m.active=1
                )) ORDER BY c.updated DESC LIMIT 200
            """, (current['id'], current['email']))]

    @staticmethod
    def require_role(current, db):
        account = db.execute('SELECT enabled,role FROM access_accounts WHERE email=?', (current['email'],)).fetchone()
        if not account or account['enabled'] != 1 or account['role'] != current['role']:
            raise HTTPException(403, 'Полномочия аккаунта изменились')

    def require_manager(self, sid, current, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.require_manager(sid, current, connection)
        self.require_role(current, db)
        if not db.execute("""
            SELECT 1 FROM spaces s WHERE id=? AND archived IS NULL AND
            (?='admin' OR EXISTS (SELECT 1 FROM space_editors e WHERE e.space_id=s.id AND e.email=? AND e.active=1))
        """, (sid, current['role'], current['email'])).fetchone():
            raise HTTPException(404, 'Управление пространством недоступно')

    def policy_record(self, sid, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.policy_record(sid, connection)
        row = db.execute('SELECT * FROM space_policies WHERE space_id=? ORDER BY revision DESC LIMIT 1',
                         (sid,)).fetchone()
        if not row:
            raise HTTPException(409, 'Политика пространства недоступна')
        return row['revision'], json.loads(row['policy'])

    def policy(self, sid):
        return self.policy_record(sid)[1]

    def save_policy(self, sid, revision, policy, current):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_manager(sid, current, db)
            updated = db.execute('UPDATE spaces SET revision=revision+1 WHERE id=? AND revision=?',
                                 (sid, revision))
            if not updated.rowcount:
                raise HTTPException(409, 'Настройки изменились. Обновите страницу управления')
            db.execute('INSERT INTO space_policies VALUES (?,?,?,?,?)',
                       (sid, revision+1, json.dumps(policy), time.time(), current['id']))
            self.access.audit(current['id'], 'space.policy', sid, {'revision': revision+1}, db)


class ModeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    communication_mode: Literal['text', 'voice', 'live'] | None = None


class SpacePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings: ChatOptions = Field(default_factory=lambda: ChatOptions(communication_mode='text'))
    prompt: str | None = Field(default=None, min_length=1, max_length=6000)
    prompts: list[PromptOption] = Field(default_factory=list, max_length=30)
    prompt_id: str = 'default'
    base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    rag_enabled: bool = False
    classic_text: bool = False


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int
    policy: SpacePolicy
