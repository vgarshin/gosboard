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
                sign_in(existing[1])
                response = client.get(f'/api/chats/{existing[0]}/messages')
                if response.status_code != 200:
                    raise ValueError('Restored history cannot be opened')
                for message in response.json():
                    for source in message.get('metadata',{}).get('citations',[]):
                        if source.get('type')=='web':
                            preview=client.get('/api/web-sources/'+source['source_id'])
                            if preview.status_code!=200 or preview.json()['excerpt']!=source['excerpt']:
                                raise ValueError('Restored web citation cannot be opened')
                            web_citations_checked+=1
            first, second = 'restore-a:' + str(uuid4()), 'restore-b:' + str(uuid4())
            app.state.store.user(first, 'restore-a@localhost', 'Restore A')
            app.state.store.user(second, 'restore-b@localhost', 'Restore B')
            sign_in(first)
            response = client.post('/api/chats')
            response.raise_for_status()
            cid = response.json()['id']
            catalog = client.get('/api/config').json()
            if 'capabilities' in catalog:
                options = {'model_id': catalog['models'][-1]['id'], 'prompt_id': catalog['prompts'][-1]['id']}
                changed = client.put(f'/api/chats/{cid}/settings', json=options)
                changed.raise_for_status()
                selected = next(chat for chat in client.get('/api/chats').json() if chat['id'] == cid)
                if selected['settings']['model_id'] != options['model_id'] or selected['settings']['prompt_id'] != options['prompt_id']:
                    raise ValueError('Restored chat settings do not persist')
                selection_checked = True
            response = client.post(f'/api/chats/{cid}/messages', json={'text': 'Offline restore check', 'request_id': str(uuid4())})
            response.raise_for_status()
            if len(client.get(f'/api/chats/{cid}/messages').json()) != 2:
                raise ValueError('Restored database cannot save a new turn')
            sign_in(second)
            for method, path in [('GET', f'/api/chats/{cid}/messages'), ('DELETE', f'/api/chats/{cid}')]:
                if client.request(method, path).status_code != 404:
                    raise ValueError('Cross-user history access was not denied')
            response = client.put(f'/api/chats/{cid}/settings', json={'model_id': 'default'})
            if response.status_code != 404:
                raise ValueError('Cross-user settings mutation was not denied')
    return {'integrity': 'ok', 'restored_messages': original_messages,
            'existing_history': 'opened' if existing else 'empty snapshot',
            'new_turn': 'saved', 'two_user_isolation': 'passed',
            'per_chat_settings': 'passed' if selection_checked else 'not available in snapshot image',
            'web_citations_checked': web_citations_checked, 'runtime_configuration': 'preserved' if live_config else 'bootstrapped from file', 'access_roles': 'preserved' if has_accounts else 'legacy snapshot', 'faiss_indexes_verified': indexes_verified, 'network': 'not used'}


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2]))))
