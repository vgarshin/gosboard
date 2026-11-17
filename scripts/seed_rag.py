"""Run inside the backend container as a trusted operator to seed/rehearse a shared book.
Uses a short-lived session for an existing allowed administrator, revoked in finally.
Never constructs an application/Store against the running database.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4
import httpx
from app.settings import Settings

parser = argparse.ArgumentParser()
parser.add_argument('document', type=Path)
parser.add_argument('--name', default='Государев совет — материалы игры')
parser.add_argument('--question', default='Какие обязанности и ограничения для дворян вводил Пётр I в области образования и государственной службы?')
args = parser.parse_args()
settings = Settings()
settings.validate()
with sqlite3.connect(settings.data_dir / 'gosboard.sqlite3') as db:
    uid = next((row[0] for row in db.execute('SELECT id,email FROM users') if row[1].lower() in settings.admins & settings.allowed), None)
    if not uid:
        raise SystemExit('An allowed administrator must sign in before seeding')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hashed = hashlib.sha256(token.encode()).hexdigest()
    db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (hashed,uid,csrf,time.time()+600))
try:
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=160,
        headers={'Host':urlparse(settings.origin).hostname, 'Origin':settings.origin, 'X-CSRF-Token':csrf},
        cookies={'gosboard_session':token}) as client:
        def call(method, path, **kwargs):
            response = client.request(method, '/api'+path, **kwargs)
            if not response.is_success:
                raise RuntimeError(f'{method} {path}: {response.status_code} '+response.text[:300])
            return response.json()
        bases = call('GET','/knowledge-bases')
        base = next((b for b in bases if b['name']==args.name),None) or call('POST','/knowledge-bases',json={'name':args.name})
        result = call('POST', f"/knowledge-bases/{base['id']}/documents", params={'filename':args.document.name},
                      content=args.document.read_bytes(), headers={'Content-Type':'application/pdf'})
        did = result['id']
        deadline = time.monotonic()+240
        while True:
            doc = next(d for d in call('GET',f"/knowledge-bases/{base['id']}/documents") if d['id']==did)
            if doc['status']=='ready':break
            if doc['status']=='failed':raise RuntimeError('Indexing failed: '+doc['error'])
            if time.monotonic()>deadline:raise RuntimeError('Indexing continues; inspect document status')
            time.sleep(2)
        source = call('GET','/sources/'+doc['first_chunk_id'])
        original = client.get(source['document_url'])
        original.raise_for_status()
        if hashlib.sha256(original.content).digest()!=hashlib.sha256(args.document.read_bytes()).digest():
            raise RuntimeError('Original document checksum mismatch')
        chat = call('POST','/chats',json={'rag_enabled':True,'knowledge_base_id':base['id']})
        try:
            response = call('POST',f"/chats/{chat['id']}/messages",json={'text':args.question,'request_id':str(uuid4())})
            metadata = response['metadata']
            citations = metadata.get('citations',[])
            if not citations or metadata.get('grounding')!='supported':
                raise RuntimeError('Representative question was not grounded')
            for citation in citations:call('GET','/sources/'+citation['chunk_id'])
            print(json.dumps({'base_id':base['id'],'document_id':did,'status':doc['status'],
                'chunks':doc['total'],'original_sha256':'verified','grounding':metadata['grounding'],
                'citations':len(citations),'pages':[c['page'] for c in citations],
                'elapsed_ms':metadata['elapsed_ms'],'temporary_chat':'removed'},ensure_ascii=False),flush=True)
        finally:
            call('DELETE',f"/chats/{chat['id']}")
finally:
    with sqlite3.connect(settings.data_dir / 'gosboard.sqlite3') as db:
        db.execute('DELETE FROM sessions WHERE hash=?',(hashed,))
