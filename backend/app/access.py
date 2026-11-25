"""Persistent email access/roles and settings-only editor operations."""
import json
import re
import time
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from .store import cache_key


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(max_length=254)
    role: Literal['user', 'editor', 'admin'] = 'user'
    enabled: bool = True

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value):
            raise ValueError('Укажите корректный email')
        return value


class Access:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS access_accounts (
                    email TEXT PRIMARY KEY, role TEXT NOT NULL, enabled INTEGER NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS access_migrations (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, details TEXT NOT NULL, created REAL NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'import_name' not in {row['name'] for row in db.execute('PRAGMA table_info(access_accounts)')}:
                db.execute('ALTER TABLE access_accounts ADD COLUMN import_name TEXT')
            if settings.demo:
                db.execute("INSERT OR IGNORE INTO access_accounts(email,role,enabled,created,updated) VALUES ('demo@localhost','admin',1,?,?)", (time.time(), time.time()))
            if not db.execute("SELECT 1 FROM access_migrations WHERE name='env-access-v1'").fetchone():
                for email in settings.allowed:
                    db.execute('INSERT OR IGNORE INTO access_accounts(email,role,enabled,created,updated) VALUES (?,?,?,?,?)',
                               (email, 'admin' if email in settings.admins else 'user', 1, time.time(), time.time()))
                db.execute("INSERT INTO access_migrations VALUES ('env-access-v1')")

    def oauth_identity(self, uid, email, name):
        try:
            email = AccountInput(email=email).email
        except ValueError:
            raise HTTPException(502, 'Яндекс не предоставил основной email') from None
        return self._register_identity(uid, email, name)

    def vtb_edu_identity(self, issuer, subject, email, name):
        """Register claims that the OIDC callback has already verified."""
        try:
            email = AccountInput(email=email).email
        except ValueError:
            return False
        if email.rsplit('@', 1)[1] != 'vtb.education' or not issuer or not subject:
            return False
        uid = 'vtb-edu:' + cache_key(issuer, subject)
        if self._register_identity(uid, email, name, corporate=True):
            return uid
        return False

    def _register_identity(self, uid, email, name, *, corporate=False):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            identity = db.execute('SELECT email FROM users WHERE id=?', (uid,)).fetchone()
            other = db.execute('SELECT id FROM users WHERE lower(email)=? AND id!=?', (email, uid)).fetchone()
            if (identity and identity['email'].lower() != email) or other:
                return False
            now = time.time()
            new_account = db.execute("INSERT OR IGNORE INTO access_accounts(email,role,enabled,created,updated) VALUES (?,'user',-1,?,?)",
                                     (email, now, now))
            if new_account.rowcount:
                details = {'login_method': 'vtb-edu'} if corporate else {}
                self.audit(uid, 'account.request', email, details, db)
            if db.execute('SELECT enabled FROM access_accounts WHERE email=?', (email,)).fetchone()[0] == 0:
                return False
            db.execute("""
                INSERT INTO users(id,email,name) VALUES (?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name
            """, (uid, email, name))
        return True

    def account(self, email):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM access_accounts WHERE email=?', (email.lower(),)).fetchone()
        return dict(row) if row else None

    def audit(self, actor, action, target, details, db=None):
        args = (actor, action, target, json.dumps(details, ensure_ascii=False), time.time())
        if db is not None:
            db.execute('INSERT INTO audit_events(actor,action,target,details,created) VALUES (?,?,?,?,?)', args)
        else:
            with self.store.connection() as connection:
                connection.execute('INSERT INTO audit_events(actor,action,target,details,created) VALUES (?,?,?,?,?)', args)

    def accounts(self):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute('''SELECT a.email,a.role,a.import_name AS name,(a.enabled=1) AS enabled,a.updated,
                CASE a.enabled WHEN -1 THEN 'pending' WHEN 1 THEN 'active' ELSE 'disabled' END AS status,
                (SELECT count(*) FROM users u WHERE lower(u.email)=a.email) AS signed_in,
                (SELECT CASE WHEN u.id LIKE 'vtb-edu:%' THEN 'vtb-edu'
                             WHEN u.id LIKE 'yandex:%' THEN 'yandex' END
                 FROM users u WHERE lower(u.email)=a.email ORDER BY u.id LIMIT 1) AS login_method
                FROM access_accounts a ORDER BY a.email''')]

    def save(self, body, actor, create_only=False):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM access_accounts WHERE email=?', (body.email,)).fetchone()
            if create_only and previous:
                raise HTTPException(409, 'Этот пользователь уже добавлен. Используйте изменение роли или переключатель доступа')
            if previous and previous['enabled']==1 and previous['role']=='admin' and (not body.enabled or body.role!='admin'):
                others = db.execute("SELECT count(*) FROM access_accounts a WHERE enabled=1 AND role='admin' AND email!=? AND EXISTS (SELECT 1 FROM users u WHERE lower(u.email)=a.email)", (body.email,)).fetchone()[0]
                if not others:
                    raise HTTPException(409, 'Нельзя отключить или понизить последнего администратора, который уже входил')
            db.execute('''INSERT INTO access_accounts(email,role,enabled,created,updated) VALUES (?,?,?,?,?) ON CONFLICT(email)
                DO UPDATE SET role=excluded.role,enabled=excluded.enabled,updated=excluded.updated''',
                (body.email, body.role, int(body.enabled), time.time(), time.time()))
            if previous and (previous['role']!=body.role or previous['enabled']!=int(body.enabled)):
                if body.role != 'editor' or not body.enabled:
                    db.execute('UPDATE space_editors SET active=0,revision=revision+1 WHERE email=? AND active=1', (body.email,))
                    db.execute('DELETE FROM rag_managers WHERE email=?', (body.email,))
                if previous['enabled'] != -1 or not body.enabled:
                    db.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE lower(email)=?)', (body.email,))
            self.audit(actor, 'account.save', body.email, body.model_dump(), db)
        return self.account(body.email)

    def editor_chats(self, current):
        with self.store.connection() as db:
            rows = db.execute('''SELECT c.id,c.title,c.user_id,u.email,c.updated,c.space_id,coalesce(s.settings,'{}') AS settings
                FROM chats c JOIN users u ON u.id=c.user_id LEFT JOIN chat_settings s ON s.chat_id=c.id
                WHERE ?='admin' OR EXISTS (
                    SELECT 1 FROM space_managers e WHERE e.space_id=c.space_id AND e.email=?
                ) ORDER BY c.updated DESC LIMIT 500''', (current['role'], current['email'])).fetchall()
        return [{**dict(row), 'settings': self.store.chat_options(row['settings'])} for row in rows]

    def admin_chats(self, owner=None, offset=0, limit=50):
        where=' WHERE c.user_id=?' if owner else ''
        args=(owner,) if owner else ()
        with self.store.connection() as db:
            db.execute('BEGIN')
            total=db.execute('SELECT count(*) FROM chats c'+where,args).fetchone()[0]
            rows=db.execute('SELECT c.id,c.title,c.user_id,u.email,c.updated FROM chats c JOIN users u ON u.id=c.user_id'+where+' ORDER BY c.updated DESC,c.id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
            owners=db.execute('SELECT u.id AS user_id,u.email,count(*) AS chat_count FROM users u JOIN chats c ON c.user_id=u.id GROUP BY u.id,u.email ORDER BY u.email').fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'owners':[dict(row) for row in owners],'offset':offset,'limit':limit}

    def chat_owner(self, cid):
        with self.store.connection() as db:
            row = db.execute('SELECT user_id FROM chats WHERE id=?', (cid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Чат не найден')
        return row['user_id']
