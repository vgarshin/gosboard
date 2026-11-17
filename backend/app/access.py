"""Persistent email access/roles and settings-only editor operations."""
import json
import re
import time
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal


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
            if settings.demo:
                db.execute("INSERT OR IGNORE INTO access_accounts VALUES ('demo@localhost','admin',1,?,?)", (time.time(), time.time()))
            if not db.execute("SELECT 1 FROM access_migrations WHERE name='env-access-v1'").fetchone():
                for email in settings.allowed:
                    db.execute('INSERT OR IGNORE INTO access_accounts VALUES (?,?,?,?,?)',
                               (email, 'admin' if email in settings.admins else 'user', 1, time.time(), time.time()))
                db.execute("INSERT INTO access_migrations VALUES ('env-access-v1')")

    def account(self, email):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM access_accounts WHERE email=?', (email.lower(),)).fetchone()
        # Explicitly local demo identities remain usable for offline restore checks.
        if not row and self.settings.demo:
            return {'email': email, 'role': 'admin', 'enabled': 1}
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
            return [dict(row) for row in db.execute('''SELECT a.email,a.role,a.enabled,a.updated,
                (SELECT count(*) FROM users u WHERE lower(u.email)=a.email) AS signed_in
                FROM access_accounts a ORDER BY a.email''')]

    def save(self, body, actor, create_only=False):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM access_accounts WHERE email=?', (body.email,)).fetchone()
            if create_only and previous:
                raise HTTPException(409, 'Этот пользователь уже добавлен. Используйте изменение роли или переключатель доступа')
            if previous and previous['enabled'] and previous['role']=='admin' and (not body.enabled or body.role!='admin'):
                others = db.execute("SELECT count(*) FROM access_accounts a WHERE enabled=1 AND role='admin' AND email!=? AND EXISTS (SELECT 1 FROM users u WHERE lower(u.email)=a.email)", (body.email,)).fetchone()[0]
                if not others:
                    raise HTTPException(409, 'Нельзя отключить или понизить последнего администратора, который уже входил')
            db.execute('''INSERT INTO access_accounts VALUES (?,?,?,?,?) ON CONFLICT(email)
                DO UPDATE SET role=excluded.role,enabled=excluded.enabled,updated=excluded.updated''',
                (body.email, body.role, int(body.enabled), time.time(), time.time()))
            if previous and (previous['role']!=body.role or previous['enabled']!=int(body.enabled)):
                db.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE lower(email)=?)', (body.email,))
            self.audit(actor, 'account.save', body.email, body.model_dump(), db)
        return self.account(body.email)

    def editor_chats(self):
        with self.store.connection() as db:
            rows = db.execute('''SELECT c.id,c.title,c.user_id,u.email,c.updated,coalesce(s.settings,'{}') AS settings
                FROM chats c JOIN users u ON u.id=c.user_id LEFT JOIN chat_settings s ON s.chat_id=c.id
                ORDER BY c.updated DESC LIMIT 500''').fetchall()
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
