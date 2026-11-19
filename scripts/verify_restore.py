"""Rehearse restore in an isolated directory with fake providers; never opens production for writing."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings


class OfflineProvider:
    async def complete(self, messages, config):
        return 'Restore verification response', {'offline_restore_check': True}

    async def close(self):
        pass


def verify(snapshot, config):
    with sqlite3.connect(snapshot.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot integrity check failed')
        has_live_config = db.execute("SELECT 1 FROM sqlite_master WHERE name='live_config_state'").fetchone()
        live_config = db.execute('SELECT v.revision,v.config FROM live_config_versions v JOIN live_config_state s ON s.revision=v.revision WHERE s.id=1').fetchone() if has_live_config else None
        original_messages = db.execute('SELECT count(*) FROM messages').fetchone()[0]
        has_accounts = db.execute("SELECT 1 FROM sqlite_master WHERE name='access_accounts'").fetchone()
        accounts = db.execute('SELECT email,role,enabled FROM access_accounts ORDER BY email').fetchall() if has_accounts else []
        existing = db.execute('''SELECT c.id,c.user_id FROM chats c JOIN users u ON u.id=c.user_id
            JOIN access_accounts a ON a.email=lower(u.email) WHERE a.enabled=1 LIMIT 1''').fetchone() if has_accounts else db.execute('SELECT id,user_id FROM chats LIMIT 1').fetchone()
        space_tables = ('spaces','space_memberships','space_editors','space_policies','chat_profiles','rag_owners','rag_managers','rag_revocations','blocked_models','account_imports')
        original_spaces = {
            table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            for table in space_tables
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone()
        }
        indexes_verified = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='rag_indexes'").fetchone():
            import faiss
            import numpy as np
            faiss.omp_set_num_threads(1)
            for blob, sha, ids, spec in db.execute('SELECT blob,sha,ids,spec FROM rag_indexes'):
                if hashlib.sha256(blob).hexdigest() != sha:
                    raise ValueError('Restored FAISS checksum mismatch')
                index = faiss.deserialize_index(np.frombuffer(blob, dtype='uint8'))
                if index.ntotal != len(json.loads(ids)) or index.d != json.loads(spec)['dimension']:
                    raise ValueError('Restored FAISS metadata mismatch')
                indexes_verified += 1
    selection_checked = False
    web_citations_checked = 0
    with tempfile.TemporaryDirectory(prefix='aitalks-restore-') as directory:
        data = Path(directory)
        shutil.copy2(snapshot, data / 'gosboard.sqlite3')
        settings = Settings(environment='development', origin='http://localhost', demo=True,
                            data_dir=data, config_path=config)
        app = create_app(settings, OfflineProvider())
        if live_config:
            restored_config = app.state.live_config.read()
            if restored_config['revision']!=live_config[0] or restored_config['config']!=json.loads(live_config[1]):
                raise ValueError('Restored live configuration changed during startup')
        with app.state.store.connection() as db:
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Restored database contains broken foreign keys')
            for table, original in original_spaces.items():
                restored = [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                if any(row not in restored for row in original) or (table != 'chat_profiles' and restored != original):
                    raise ValueError(f'Restored records changed during startup: {table}')
            for email, role, enabled in accounts:
                restored = db.execute('SELECT role,enabled FROM access_accounts WHERE email=?',(email,)).fetchone()
                if not restored or tuple(restored)!=(role,enabled):
                    raise ValueError('Restored access roles changed during startup')
        with TestClient(app, base_url=settings.origin) as client:
            client.headers['Origin'] = settings.origin
            def sign_in(uid):
                token = app.state.store.session(uid, 3600)
                client.cookies.clear()
                client.cookies.set('gosboard_session', token)
                client.headers['X-CSRF-Token'] = app.state.store.auth(token)['csrf']
            if existing:
                if not has_accounts:
                    with app.state.store.connection() as db:
                        email = db.execute('SELECT lower(email) FROM users WHERE id=?', (existing[1],)).fetchone()[0]
                        db.execute("INSERT OR IGNORE INTO access_accounts(email,role,enabled,created,updated) VALUES (?,'user',1,0,0)", (email,))
                sign_in(existing[1])
                response = client.get(f'/api/chats/{existing[0]}/messages')
                if response.status_code != 200:
                    raise ValueError('Restored history cannot be opened')
                for message in app.state.store.messages(existing[0]):
                    web_citations_checked += sum(source.get('type')=='web' for source in message['metadata'].get('citations', []))
            demo = client.post('/api/auth/demo', follow_redirects=False)
            if demo.status_code != 303:
                raise ValueError('Offline demo login failed')
            client.headers['X-CSRF-Token'] = client.get('/api/me').json()['csrf']
            sid = client.post('/api/management/spaces', json={'name':'Offline restore'}).json()['id']
            for email in ('restore-a@example.org', 'restore-b@example.org'):
                client.post('/api/admin/users', json={'email':email}).raise_for_status()
                client.put(f'/api/management/spaces/{sid}/assignments', json={'email':email,'assigned':True}).raise_for_status()
            first, second = 'restore-a:' + str(uuid4()), 'restore-b:' + str(uuid4())
            app.state.store.user(first, 'restore-a@example.org', 'Restore A')
            app.state.store.user(second, 'restore-b@example.org', 'Restore B')
            sign_in(first)
            client.headers['X-Space-ID'] = sid
            response = client.post('/api/chats')
            response.raise_for_status()
            cid = response.json()['id']
            changed = client.patch(f'/api/chats/{cid}/mode', json={'communication_mode':'voice'})
            changed.raise_for_status()
            selected = next(chat for chat in client.get('/api/chats').json() if chat['id'] == cid)
            if selected['settings']['communication_mode'] != 'voice':
                raise ValueError('Restored communication mode does not persist')
            selection_checked = True
            response = client.post(f'/api/chats/{cid}/messages', json={'text': 'Offline restore check', 'request_id': str(uuid4())})
            response.raise_for_status()
            if len(client.get(f'/api/chats/{cid}/messages').json()) != 2:
                raise ValueError('Restored database cannot save a new turn')
            sign_in(second)
            for method, path in [('GET', f'/api/chats/{cid}/messages'), ('DELETE', f'/api/chats/{cid}')]:
                if client.request(method, path).status_code != 404:
                    raise ValueError('Cross-user history access was not denied')
            response = client.patch(f'/api/chats/{cid}/mode', json={'communication_mode': 'text'})
            if response.status_code != 404:
                raise ValueError('Cross-user settings mutation was not denied')
    return {'integrity': 'ok', 'restored_messages': original_messages,
            'existing_history': 'opened' if existing else 'empty snapshot',
            'new_turn': 'saved', 'two_user_isolation': 'passed',
            'per_chat_settings': 'passed' if selection_checked else 'not available in snapshot image',
            'web_citations_checked': web_citations_checked, 'runtime_configuration': 'preserved' if live_config else 'bootstrapped from file', 'access_roles': 'preserved' if has_accounts else 'legacy snapshot', 'faiss_indexes_verified': indexes_verified, 'network': 'not used'}


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2]))))
