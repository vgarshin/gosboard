"""Global knowledge catalog and atomic FAISS publications in the existing SQLite database."""
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from .store import cache_key, digest


class KnowledgeStore:
    def __init__(self, store):
        self.store = store
        self.connection = store.connection
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS rag_bases (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    index_revision INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
                    rebuilding INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY, base_id TEXT NOT NULL, filename TEXT NOT NULL,
                    sha TEXT NOT NULL, object_key TEXT NOT NULL, mime TEXT NOT NULL,
                    status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    object_deleted INTEGER NOT NULL DEFAULT 0, delete_attempts INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS rag_document_dedup ON rag_documents(base_id,sha) WHERE deleted=0;
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, base_id TEXT NOT NULL,
                    page INTEGER, position INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL, embedding_key TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS rag_chunks_base ON rag_chunks(base_id,active);
                CREATE TABLE IF NOT EXISTS rag_embeddings (
                    key TEXT PRIMARY KEY, model TEXT NOT NULL, model_version TEXT NOT NULL,
                    vector BLOB NOT NULL, dimension INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rag_indexes (
                    base_id TEXT NOT NULL, revision INTEGER NOT NULL, blob BLOB NOT NULL,
                    sha TEXT NOT NULL, ids TEXT NOT NULL, spec TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(base_id,revision));
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(rag_bases)')}
            for column, default in [('chunk_size',1200),('chunk_overlap',180),('top_k',5),('top_k_documents',5)]:
                if column not in columns:
                    db.execute(f'ALTER TABLE rag_bases ADD COLUMN {column} INTEGER NOT NULL DEFAULT {default}')
            db.execute("UPDATE rag_documents SET status='queued_upload' WHERE deleted=0 AND status='uploading'")
            db.execute("UPDATE rag_documents SET status='queued' WHERE deleted=0 AND status='processing'")
            db.execute('UPDATE rag_documents SET delete_attempts=0 WHERE deleted=1 AND object_deleted=0')

    def base(self, bid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM rag_bases WHERE id=? AND deleted=0', (bid,)).fetchone()
        if not row:
            raise HTTPException(404, 'База знаний недоступна')
        return dict(row)

    def bases(self):
        with self.connection() as db:
            rows = db.execute('''SELECT b.*,
                (SELECT count(*) FROM rag_documents d WHERE d.base_id=b.id AND d.deleted=0) AS documents,
                (SELECT count(*) FROM rag_documents d WHERE d.base_id=b.id AND d.deleted=0 AND d.status='ready') AS ready_documents,
                (SELECT count(*) FROM rag_documents d WHERE d.base_id=b.id AND d.deleted=0 AND d.status IN ('queued_upload','uploading','queued','processing')) AS pending_documents,
                (SELECT count(*) FROM rag_chunks c WHERE c.base_id=b.id AND c.active=1) AS chunks
                FROM rag_bases b WHERE b.deleted=0 ORDER BY b.created''').fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['ready'] = bool(item['index_revision'] and item['ready_documents'] and not item['rebuilding'])
            result.append(item)
        return result

    def create_base(self, name):
        bid = str(uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM rag_bases WHERE deleted=0').fetchone()[0] >= 20:
                raise HTTPException(422, 'Достигнут лимит: 20 баз знаний')
            db.execute('INSERT INTO rag_bases(id,name,created) VALUES (?,?,?)', (bid, name, time.time()))
        return self.base(bid)

    def documents(self, bid):
        self.base(bid)
        with self.connection() as db:
            rows = db.execute('''SELECT d.id,d.base_id,d.filename,d.mime,d.status,d.progress,d.total,d.error,d.created,
                (SELECT id FROM rag_chunks c WHERE c.document_id=d.id AND c.active=1 ORDER BY ordinal LIMIT 1) AS first_chunk_id
                FROM rag_documents d WHERE base_id=? AND deleted=0 ORDER BY created''', (bid,)).fetchall()
        return [dict(row) for row in rows]

    def document(self, did, include_deleted=False):
        with self.connection() as db:
            row = db.execute('SELECT * FROM rag_documents WHERE id=?', (did,)).fetchone()
        if not row:
            raise HTTPException(404, 'Документ не найден')
        if row['deleted'] and not include_deleted:
            raise HTTPException(410, 'Документ удалён')
        return dict(row)

    def existing_document(self, bid, sha):
        with self.connection() as db:
            row = db.execute('SELECT * FROM rag_documents WHERE base_id=? AND sha=? AND deleted=0', (bid, sha)).fetchone()
        return dict(row) if row else None

    def add_document(self, did, bid, filename, sha, key, mime, uid, max_documents):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM rag_bases WHERE id=? AND deleted=0', (bid,)).fetchone():
                raise HTTPException(404, 'База знаний недоступна')
            existing = db.execute('SELECT * FROM rag_documents WHERE base_id=? AND sha=? AND deleted=0', (bid, sha)).fetchone()
            if existing:
                return dict(existing)
            if db.execute('SELECT count(*) FROM rag_documents WHERE deleted=0').fetchone()[0] >= max_documents:
                raise HTTPException(422, 'Достигнут лимит документов')
            db.execute('''INSERT INTO rag_documents(id,base_id,filename,sha,object_key,mime,status,requested_by,created)
                          VALUES (?,?,?,?,?,?,'queued_upload',?,?)''', (did, bid, filename, sha, key, mime, uid, time.time()))
        return self.document(did)

    def set_document(self, did, **fields):
        allowed = {'status', 'progress', 'total', 'error', 'object_deleted', 'delete_attempts'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid document update')
        with self.connection() as db:
            db.execute('UPDATE rag_documents SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=? AND deleted=0',
                       (*fields.values(), did))

    def next_job(self):
        with self.connection() as db:
            row = db.execute("SELECT * FROM rag_documents WHERE deleted=0 AND status IN ('queued_upload','queued') ORDER BY created LIMIT 1").fetchone()
        return dict(row) if row else None

    def pending_deletion(self):
        with self.connection() as db:
            row = db.execute('SELECT * FROM rag_documents WHERE deleted=1 AND object_deleted=0 AND delete_attempts<3 ORDER BY created LIMIT 1').fetchone()
        return dict(row) if row else None

    def chunks(self, bid, exclude_document=None):
        with self.connection() as db:
            rows = db.execute('''SELECT c.*,d.filename FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id
                WHERE c.base_id=? AND c.active=1 AND d.deleted=0 AND d.status='ready' AND d.id!=?
                ORDER BY d.created,c.ordinal''', (bid, exclude_document or '')).fetchall()
        return [dict(row) for row in rows]

    def embedding(self, key):
        with self.connection() as db:
            row = db.execute('SELECT * FROM rag_embeddings WHERE key=?', (key,)).fetchone()
        return dict(row) if row else None

    def put_embedding(self, key, model, version, vector, dimension):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO rag_embeddings VALUES (?,?,?,?,?,?)', (key, model, version, vector, dimension, time.time()))

    def publish(self, bid, document, chunks, blob, ids, spec, max_chunks):
        """Called under the engine's per-base lock; metadata/index become visible together."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            base = db.execute('SELECT * FROM rag_bases WHERE id=? AND deleted=0', (bid,)).fetchone()
            if not base or (document and db.execute('SELECT deleted FROM rag_documents WHERE id=?', (document['id'],)).fetchone()[0]):
                raise HTTPException(409, 'Документ был удалён во время индексации')
            if document:
                db.execute('UPDATE rag_chunks SET active=0 WHERE document_id=?', (document['id'],))
                for chunk in chunks:
                    db.execute('INSERT OR REPLACE INTO rag_chunks VALUES (?,?,?,?,?,?,?,?,1)',
                               (chunk['id'], document['id'], bid, chunk['page'], chunk['position'], chunk['ordinal'], chunk['text'], chunk['embedding_key']))
                if db.execute('SELECT count(*) FROM rag_chunks WHERE active=1').fetchone()[0] > max_chunks:
                    raise HTTPException(422, 'Достигнут общий лимит фрагментов базы знаний')
                db.execute("UPDATE rag_documents SET status='ready',progress=?,total=?,error='' WHERE id=?", (len(chunks), len(chunks), document['id']))
            revision = base['revision'] + 1
            db.execute('INSERT INTO rag_indexes VALUES (?,?,?,?,?,?,?)',
                       (bid, revision, blob, digest(blob), json.dumps(ids), json.dumps(spec), time.time()))
            pending = db.execute("SELECT count(*) FROM rag_documents WHERE base_id=? AND deleted=0 AND status IN ('queued_upload','uploading','queued','processing')", (bid,)).fetchone()[0]
            db.execute('UPDATE rag_bases SET revision=?,index_revision=?,rebuilding=CASE WHEN ?=0 THEN 0 ELSE rebuilding END WHERE id=?',
                       (revision, revision, pending, bid))
            db.execute('DELETE FROM rag_indexes WHERE base_id=? AND revision NOT IN (SELECT revision FROM rag_indexes WHERE base_id=? ORDER BY revision DESC LIMIT 2)', (bid, bid))
            db.execute("DELETE FROM cache WHERE kind='llm'")

    def index_snapshot(self, bid):
        with self.connection() as db:
            db.execute('BEGIN')
            base = db.execute('SELECT * FROM rag_bases WHERE id=? AND deleted=0', (bid,)).fetchone()
            if not base or base['rebuilding'] or not base['index_revision']:
                raise HTTPException(409, 'База знаний ещё не готова. Дождитесь индексации')
            index = db.execute('SELECT * FROM rag_indexes WHERE base_id=? AND revision=?', (bid, base['index_revision'])).fetchone()
        if not index:
            raise HTTPException(409, 'Индекс недоступен. Переиндексируйте базу знаний')
        return dict(base), dict(index)

    def source(self, chunk_id):
        with self.connection() as db:
            row = db.execute('''SELECT c.*,d.filename,d.mime,d.deleted,b.name AS base_name,b.deleted AS base_deleted
                FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id JOIN rag_bases b ON b.id=c.base_id WHERE c.id=?''', (chunk_id,)).fetchone()
        if not row:
            raise HTTPException(410, 'Фрагмент недоступен или удалён')
        if row['deleted'] or row['base_deleted']:
            raise HTTPException(410, 'Источник удалён')
        return dict(row)

    def remove_document(self, did):
        document = self.document(did)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE rag_documents SET deleted=1,status='deleted' WHERE id=?", (did,))
            db.execute('DELETE FROM rag_chunks WHERE document_id=?', (did,))
            db.execute('UPDATE rag_bases SET revision=revision+1 WHERE id=?', (document['base_id'],))
            db.execute('DELETE FROM rag_embeddings WHERE key NOT IN (SELECT embedding_key FROM rag_chunks)')
            db.execute("DELETE FROM cache WHERE kind IN ('llm','rag_query')")
        return document

    def queue_reindex(self, bid):
        self.base(bid)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM rag_embeddings WHERE key IN (SELECT embedding_key FROM rag_chunks WHERE base_id=?)', (bid,))
            db.execute("UPDATE rag_documents SET status='queued',progress=0,error='' WHERE base_id=? AND deleted=0 AND status!='queued_upload'", (bid,))
            db.execute('UPDATE rag_bases SET rebuilding=1,revision=revision+1 WHERE id=?', (bid,))
            db.execute("DELETE FROM cache WHERE kind IN ('llm','rag_query')")
