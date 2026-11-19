import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException


operation_space = ContextVar("operation_space", default=None)


def digest(value):
    if not isinstance(value, bytes):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def cache_key(*parts):
    return digest(json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'gosboard.sqlite3'
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth (
                    hash TEXT PRIMARY KEY, state TEXT NOT NULL, verifier TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS chats_owner ON chats(user_id, updated);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                    created REAL NOT NULL, metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, created);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    value BLOB NOT NULL, expires REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    reserved REAL NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(chats)')}
            if 'space_id' not in columns:
                db.execute("ALTER TABLE chats ADD COLUMN space_id TEXT NOT NULL DEFAULT 'initial'")
            db.execute('CREATE INDEX IF NOT EXISTS chats_owner_space ON chats(user_id,space_id,updated)')
            db.execute('CREATE TABLE IF NOT EXISTS chat_settings (chat_id TEXT PRIMARY KEY, settings TEXT NOT NULL)')
            if 'settings' in columns:
                # Import settings from the unreleased local prototype if present.
                db.execute('INSERT OR IGNORE INTO chat_settings SELECT id,settings FROM chats')
            db.execute('DELETE FROM chat_settings WHERE chat_id NOT IN (SELECT id FROM chats)')
            db.execute('CREATE TABLE IF NOT EXISTS chat_profiles (chat_id TEXT PRIMARY KEY, profiles TEXT NOT NULL)')
            if 'space_id' not in {row['name'] for row in db.execute('PRAGMA table_info(usage)')}:
                db.execute('ALTER TABLE usage ADD COLUMN space_id TEXT')
            if 'space_id' not in {row['name'] for row in db.execute('PRAGMA table_info(cache)')}:
                db.execute('ALTER TABLE cache ADD COLUMN space_id TEXT')
                db.execute("DELETE FROM cache WHERE kind='tts'")
            db.execute('CREATE TABLE IF NOT EXISTS configurations (hash TEXT PRIMARY KEY, snapshot TEXT NOT NULL, created REAL NOT NULL)')
            # Unknown in-flight outcomes may have been billed: retain their reservation.
            db.execute("UPDATE requests SET status='interrupted' WHERE status='pending'")
            db.execute("UPDATE usage SET state='uncertain' WHERE state='pending'")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=5000')
            yield db

    def user(self, uid, email, name):
        with self.connection() as db:
            db.execute('INSERT INTO users VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET email=excluded.email,name=excluded.name',
                       (uid, email, name))
        return {'id': uid, 'email': email, 'name': name}

    def session(self, uid, duration):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), uid, csrf, time.time() + duration))
        return token

    def auth(self, token):
        with self.connection() as db:
            row = db.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>?',
                             (digest(token or ''), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE hash=?', (digest(token or ''),))

    def chat(self, cid, uid):
        with self.connection() as db:
            row = db.execute("SELECT c.*, coalesce(s.settings,'{}') AS selected_settings FROM chats c LEFT JOIN chat_settings s ON s.chat_id=c.id WHERE c.id=? AND c.user_id=?", (cid, uid)).fetchone()
        if not row:
            raise HTTPException(404, 'Чат не найден')
        result = dict(row)
        result['settings'] = self.chat_options(result.pop('selected_settings'))
        return result

    def chat_snapshot(self, cid, uid):
        # A WAL read snapshot stays available while generation holds the workflow lock.
        with self.connection() as db:
            db.execute('BEGIN')
            row=db.execute("SELECT c.*,coalesce(s.settings,'{}') AS selected_settings FROM chats c LEFT JOIN chat_settings s ON s.chat_id=c.id WHERE c.id=? AND c.user_id=?",(cid,uid)).fetchone()
            if not row:raise HTTPException(404,'Чат не найден')
            messages=db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY created,rowid',(cid,)).fetchall()
        chat=dict(row);chat['settings']=self.chat_options(chat.pop('selected_settings'))
        return {'chat':chat,'messages':[{**dict(message),'metadata':json.loads(message['metadata'])} for message in messages]}

    @staticmethod
    def chat_options(raw):
        from .configuration import ChatOptions
        return ChatOptions.model_validate_json(raw).model_dump()

    def chats(self, uid, sid="initial"):
        with self.connection() as db:
            rows = db.execute("SELECT c.*, coalesce(s.settings,'{}') AS selected_settings FROM chats c LEFT JOIN chat_settings s ON s.chat_id=c.id WHERE c.user_id=? AND c.space_id=? ORDER BY c.updated DESC LIMIT 200", (uid, sid)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['settings'] = self.chat_options(item.pop('selected_settings'))
            result.append(item)
        return result

    def new_chat(self, uid, options=None, *, sid="initial", authorize=None, profiles=None):
        cid, now = str(uuid4()), time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if authorize:
                authorize(db)
            db.execute('INSERT INTO chats(id,user_id,title,created,updated,space_id) VALUES (?,?,?,?,?,?)',
                       (cid, uid, 'Новый разговор', now, now, sid))
            db.execute('INSERT INTO chat_settings VALUES (?,?)', (cid, json.dumps(options or {})))
            if profiles is not None:
                db.execute('INSERT INTO chat_profiles VALUES (?,?)', (cid, json.dumps(profiles)))
        return self.chat(cid, uid)

    def update_chat_settings(self, cid, uid, options):
        with self.connection() as db:
            result = db.execute('UPDATE chats SET updated=? WHERE id=? AND user_id=?',
                                (time.time(), cid, uid))
            if result.rowcount != 1:
                raise HTTPException(404, 'Чат не найден')
            db.execute('INSERT INTO chat_settings VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET settings=excluded.settings',
                       (cid, json.dumps(options)))
        return self.chat(cid, uid)

    def save_configuration(self, snapshot):
        key = cache_key(snapshot)
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO configurations VALUES (?,?,?)',
                       (key, json.dumps(snapshot, ensure_ascii=False), time.time()))
        return key

    def messages(self, cid, limit=None):
        if limit == 0:
            return []
        with self.connection() as db:
            if limit is None:
                rows = db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY created,rowid', (cid,)).fetchall()
            else:
                rows = db.execute('SELECT id,chat_id,role,text,created,metadata FROM (SELECT rowid AS ordinal,* FROM messages WHERE chat_id=? ORDER BY created DESC,rowid DESC LIMIT ?) ORDER BY created,ordinal', (cid,limit)).fetchall()
        return [{**dict(r), 'metadata': json.loads(r['metadata'])} for r in rows]

    def add_turn(self, cid, text, answer, metadata):
        now = time.time()
        uid, aid = str(uuid4()), str(uuid4())
        with self.connection() as db:
            count = db.execute('SELECT count(*) FROM messages WHERE chat_id=?', (cid,)).fetchone()[0]
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (uid, cid, 'user', text, now, '{}'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (aid, cid, 'assistant', answer, now + .0001, json.dumps(metadata)))
            if count == 0:
                db.execute('UPDATE chats SET title=? WHERE id=?', (text[:64], cid))
            db.execute('UPDATE chats SET updated=? WHERE id=?', (now, cid))
        return aid

    def delete_chat(self, cid, uid):
        self.chat(cid, uid)
        with self.connection() as db:
            db.execute('DELETE FROM chat_settings WHERE chat_id=?', (cid,))
            db.execute('DELETE FROM chat_profiles WHERE chat_id=?', (cid,))
            db.execute('DELETE FROM messages WHERE chat_id=?', (cid,))
            db.execute('DELETE FROM chats WHERE id=?', (cid,))
            # Purge user's derived content, including cached responses and audio.
            db.execute('DELETE FROM cache WHERE user_id=?', (uid,))
            db.execute('DELETE FROM requests WHERE user_id=?', (uid,))

    def begin_request(self, rid, uid, kind, fingerprint):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            if row:
                if (row['user_id'], row['kind'], row['fingerprint']) != (uid, kind, fingerprint):
                    raise HTTPException(409, 'Идентификатор запроса уже использован')
                if row['status'] == 'done':
                    return json.loads(row['result'])
                raise HTTPException(409, 'Запрос уже выполнялся. Проверьте историю перед повтором')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)',
                       (rid, uid, kind, fingerprint, 'pending', None, time.time()))
        return None

    def end_request(self, rid, result=None):
        with self.connection() as db:
            db.execute('UPDATE requests SET status=?,result=? WHERE id=?',
                       ('done' if result is not None else 'failed', json.dumps(result) if result is not None else None, rid))

    def initialize_budget(self, daily, monthly):
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), daily REAL NOT NULL, monthly REAL NOT NULL, revision INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO budget_policy VALUES (1,?,?,1)', (daily, monthly))

    def budget(self):
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        from datetime import timedelta
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        with self.connection() as db:
            db.execute('BEGIN')
            policy = db.execute('SELECT * FROM budget_policy WHERE id=1').fetchone()
            result = {'revision': policy['revision'], 'timezone': 'UTC', 'accounting': 'conservative reservations; not provider invoice'}
            for name, start, end, limit in [('day', day, day+timedelta(days=1), policy['daily']), ('month', month, next_month, policy['monthly'])]:
                rows = [dict(r) for r in db.execute('SELECT kind,state,count(*) AS calls,sum(reserved) AS reserved_rub FROM usage WHERE created>=? AND created<? GROUP BY kind,state', (start.timestamp(),end.timestamp()))]
                used = sum(row['reserved_rub'] for row in rows)
                result[name] = {'limit_rub': limit, 'used_rub': round(used, 6), 'remaining_rub': round(max(0,limit-used),6), 'resets_at': end.isoformat(), 'rows': rows}
        return result

    def update_budget(self, daily, monthly, revision, actor):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = dict(db.execute('SELECT * FROM budget_policy WHERE id=1').fetchone())
            if previous['revision'] != revision:
                raise HTTPException(409, 'Бюджет изменён другим администратором. Обновите данные')
            db.execute('UPDATE budget_policy SET daily=?,monthly=?,revision=revision+1 WHERE id=1', (daily,monthly))
            db.execute('INSERT INTO audit_events(actor,action,target,details,created) VALUES (?,?,?,?,?)', (actor,'budget.update','global',json.dumps({'before':previous,'after':{'daily':daily,'monthly':monthly,'revision':revision+1}}),time.time()))
        return self.budget()

    def reserve(self, uid, kind, amount, daily, monthly, space_id=None):
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        rid = str(uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            policy = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='budget_policy'").fetchone()
            if policy:
                daily, monthly = db.execute('SELECT daily,monthly FROM budget_policy WHERE id=1').fetchone()
            daily_used = db.execute('SELECT coalesce(sum(reserved),0) FROM usage WHERE created>=?', (day,)).fetchone()[0]
            monthly_used = db.execute('SELECT coalesce(sum(reserved),0) FROM usage WHERE created>=?', (month,)).fetchone()[0]
            if daily_used + amount > daily or monthly_used + amount > monthly:
                raise HTTPException(429, 'Достигнут лимит расходов. Обратитесь к администратору')
            db.execute('INSERT INTO usage(id,user_id,kind,reserved,state,created,space_id) VALUES (?,?,?,?,?,?,?)', (rid, uid, kind, amount, 'pending', time.time(), space_id))
        return rid

    def settle(self, rid, state):
        with self.connection() as db:
            db.execute('UPDATE usage SET state=? WHERE id=?', (state, rid))

    def usage(self, uid=None):
        with self.connection() as db:
            query = 'SELECT kind,state,count(*) AS calls,sum(reserved) AS reserved_rub FROM usage'
            args = ()
            if uid:
                query += ' WHERE user_id=?'
                args = (uid,)
            return [dict(r) for r in db.execute(query + ' GROUP BY kind,state', args)]

    def get_cache(self, key, uid, kind=None):
        with self.connection() as db:
            row = db.execute('SELECT value FROM cache WHERE key=? AND user_id=? AND expires>?', (key, uid, time.time())).fetchone()
        if kind and getattr(self, 'metrics', None):
            self.metrics.cache(kind, row is not None)
        return bytes(row['value']) if row else None

    def put_cache(self, key, uid, kind, value, ttl):
        space_id = operation_space.get()
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO cache(key,user_id,kind,value,expires,created,space_id) VALUES (?,?,?,?,?,?,?)',
                       (key, uid, kind, value, time.time() + ttl, time.time(), space_id))

    def cleanup(self, max_bytes):
        with self.connection() as db:
            now = time.time()
            db.execute('DELETE FROM cache WHERE expires<?', (now,))
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('DELETE FROM oauth WHERE expires<?', (now,))
            db.execute('DELETE FROM requests WHERE created<?', (now - 7 * 86400,))
            total = db.execute('SELECT coalesce(sum(length(value)),0) FROM cache').fetchone()[0]
            if total > max_bytes:
                for r in db.execute('SELECT key,length(value) AS size FROM cache ORDER BY created').fetchall():
                    db.execute('DELETE FROM cache WHERE key=?', (r['key'],))
                    total -= r['size']
                    if total <= max_bytes:
                        break
