import asyncio
import io
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.providers import Providers
from app.rag import RAG_CONTRACT, UNSUPPORTED, validate_rag_answer
from app.settings import Settings
from test_app import CONFIG

ORIGIN = 'http://localhost'
TEXT = ('Education reform established the navigation school in 1701. Mathematics and navigation were taught to prepare specialists. ' * 8).encode()


class FakeObjects:
    def __init__(self):
        self.values = {}
        self.puts = self.gets = self.deletes = 0
        self.fail_upload = False

    async def put(self, key, path, mime):
        if self.fail_upload:
            raise HTTPException(502, 'Storage unavailable')
        self.puts += 1
        self.values[key] = Path(path).read_bytes()

    async def get(self, key, target):
        self.gets += 1
        Path(target).write_bytes(self.values[key])

    async def delete(self, key):
        self.deletes += 1
        self.values.pop(key, None)


class RagProvider(Providers):
    def __init__(self):
        super().__init__(Settings(environment='development', origin=ORIGIN, demo=True))
        self.embeddings = self.completions = 0
        self.invalid_citation = False
        self.no_support = False
        self.query_version = 'demo-hash-v1'
        self.spoken = None

    async def embed(self, text, kind, settings):
        self.embeddings += 1
        vector, version = await super().embed(text, kind, settings)
        return vector, self.query_version if kind == 'query' else version

    async def complete(self, messages, config):
        self.completions += 1
        if RAG_CONTRACT in messages[0]['text']:
            if self.invalid_citation:
                return json.dumps({'supported': True, 'answer': 'Invented source [D99]', 'used_sources': ['D99']}), {}
            if self.no_support:
                return json.dumps({'supported': False, 'answer': 'Unverified model text must not escape', 'used_sources': []}), {}
        return await super().complete(messages, config)

    async def synthesize(self, text, config):
        self.spoken = text
        self.spoken_voice = config.voice
        return await super().synthesize(text, config)


@pytest.fixture
def rag_setup(tmp_path):
    settings = Settings(environment='development', origin=ORIGIN, demo=True, rag_enabled=True,
                        config_path=CONFIG, data_dir=tmp_path)
    provider, objects = RagProvider(), FakeObjects()
    app = create_app(settings, provider)
    app.state.rag.objects = objects
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        client.post('/api/auth/demo', follow_redirects=False)
        client.headers['X-CSRF-Token'] = client.get('/api/me').json()['csrf']
        yield client, app, provider, objects


def new_base(client, name='Global base'):
    response = client.post('/api/knowledge-bases', json={'name': name})
    assert response.status_code == 200, response.text
    return response.json()['id']


def upload(client, bid, content=TEXT, filename='education.txt'):
    mime = {'.pdf':'application/pdf', '.txt':'text/plain', '.doc':'application/msword', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}[Path(filename).suffix]
    response = client.post(f'/api/knowledge-bases/{bid}/documents', params={'filename': filename},
                           content=content, headers={'Content-Type': mime})
    assert response.status_code == 202, response.text
    return response.json()


def wait_document(client, bid, did, state='ready'):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        documents = client.get(f'/api/knowledge-bases/{bid}/documents').json()
        document = next((doc for doc in documents if doc['id'] == did), None)
        if document and document['status'] == state:
            return document
        if document and document['status'] == 'failed' and state != 'failed':
            pytest.fail(document['error'])
        time.sleep(.03)
    pytest.fail('Document job did not finish')


def ready_base(client):
    bid = new_base(client)
    did = upload(client, bid)['id']
    return bid, did, wait_document(client, bid, did)


def rag_chat(client, bid):
    response = client.post('/api/chats', json={'rag_enabled': True, 'knowledge_base_id': bid})
    assert response.status_code == 200, response.text
    return response.json()['id']


def ask(client, cid, text='Education reform navigation school mathematics'):
    return client.post(f'/api/chats/{cid}/messages', json={'text': text, 'request_id': str(uuid4())})


def test_upload_index_citations_source_and_tts(rag_setup):
    client, app, provider, objects = rag_setup
    bid, did, doc = ready_base(client)
    assert objects.puts == 1
    bases = client.get('/api/knowledge-bases').json()
    assert bases[0]['ready'] and bases[0]['chunks'] > 0
    cid = rag_chat(client, bid)
    result = ask(client, cid)
    assert result.status_code == 200, result.text
    messages = client.get(f'/api/chats/{cid}/messages').json()
    answer = messages[-1]
    source = answer['metadata']['citations'][0]
    assert '[D1]' in answer['text'] and source['document_id'] == did
    assert source['url'].startswith('/sources/')
    excerpt = client.get('/api/sources/' + source['chunk_id'])
    assert excerpt.status_code == 200
    assert excerpt.json()['text'] == source['excerpt']
    original = client.get(source['document_url'])
    assert original.status_code == 200 and original.content == TEXT
    speech = client.post(f'/api/chats/{cid}/messages/{answer["id"]}/speech', json={'request_id': str(uuid4())})
    assert speech.status_code == 200
    assert '[D1]' not in provider.spoken
    with app.state.store.connection() as db:
        row = db.execute('SELECT blob,sha FROM rag_indexes WHERE base_id=? ORDER BY revision DESC LIMIT 1', (bid,)).fetchone()
        assert len(row['blob']) > 0


def test_off_mode_never_calls_embeddings_and_query_cache_scoped(rag_setup):
    client, app, provider, objects = rag_setup
    bid, _, _ = ready_base(client)
    baseline = provider.embeddings
    off = client.post('/api/chats', json={'knowledge_base_id': bid, 'rag_enabled': False}).json()['id']
    assert ask(client, off).status_code == 200
    assert provider.embeddings == baseline
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 200
    count, completions = provider.embeddings, provider.completions
    other = rag_chat(client, bid)
    assert ask(client, other).json()['metadata']['cached'] is True
    assert provider.embeddings == count and provider.completions == completions


def test_duplicate_upload_has_no_extra_jobs_or_charges(rag_setup):
    client, app, provider, objects = rag_setup
    bid, did, _ = ready_base(client)
    before = provider.embeddings
    duplicate = upload(client, bid)
    assert duplicate == {'id': did, 'duplicate': True}
    assert objects.puts == 1 and provider.embeddings == before


def test_shared_base_private_chats_and_deleted_sources(rag_setup):
    client, app, provider, objects = rag_setup
    bid, did, _ = ready_base(client)
    first = rag_chat(client, bid)
    assert ask(client, first).status_code == 200
    source = app.state.store.messages(first)[-1]['metadata']['citations'][0]
    app.state.store.user('second', 'second@localhost', 'Second')
    token = app.state.store.session('second', 3600)
    client.cookies.clear(); client.cookies.set('gosboard_session', token)
    client.headers['X-CSRF-Token'] = app.state.store.auth(token)['csrf']
    assert client.get('/api/knowledge-bases').json()[0]['id'] == bid
    assert client.get('/api/sources/' + source['chunk_id']).status_code == 200
    assert client.get(f'/api/chats/{first}/messages').status_code == 404
    # Demo users are admins; production admin checks are exercised separately.
    assert client.delete(f'/api/documents/{did}').status_code == 200
    assert client.get('/api/sources/' + source['chunk_id']).status_code == 410
    assert client.get(source['document_url']).status_code == 410
    assert app.state.store.messages(first)[-1]['metadata']['citations'][0]['excerpt']
    assert client.post('/api/chats', json={'rag_enabled': True, 'knowledge_base_id': bid}).status_code == 409
    deadline = time.monotonic() + 3
    while objects.values and time.monotonic() < deadline:
        time.sleep(.02)
    assert not objects.values


def test_invalid_citation_is_not_saved_or_retried(rag_setup):
    client, app, provider, _ = rag_setup
    bid, _, _ = ready_base(client)
    provider.invalid_citation = True
    before = provider.completions
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 502
    assert provider.completions == before + 1
    assert app.state.store.messages(cid) == []


def test_unsupported_question_does_not_expose_unattributed_model_text(rag_setup):
    client, app, provider, _ = rag_setup
    bid, _, _ = ready_base(client)
    provider.no_support = True
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 200
    message = app.state.store.messages(cid)[-1]
    assert message['text'] == UNSUPPORTED
    assert message['metadata']['citations'] == []


def test_missing_base_and_index_model_drift_fail_safely(rag_setup):
    client, app, provider, _ = rag_setup
    empty = new_base(client)
    assert client.post('/api/chats', json={'rag_enabled': True, 'knowledge_base_id': empty}).status_code == 409
    bid, _, _ = ready_base(client)
    provider.query_version = 'changed-query-model'
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 409
    assert provider.completions == 0
    assert client.delete(f'/api/knowledge-bases/{bid}').status_code == 200
    assert client.put(f'/api/chats/{cid}/settings', json={'rag_enabled': False, 'knowledge_base_id': bid}).status_code == 200


def test_failed_upload_requires_explicit_retry(rag_setup):
    client, app, provider, objects = rag_setup
    objects.fail_upload = True
    bid = new_base(client)
    did = upload(client, bid)['id']
    wait_document(client, bid, did, 'failed')
    assert provider.embeddings == 0
    objects.fail_upload = False
    assert client.post(f'/api/documents/{did}/retry').status_code == 200
    wait_document(client, bid, did)


def test_original_roundtrip_after_local_eviction(rag_setup):
    client, app, _, objects = rag_setup
    bid, did, _ = ready_base(client)
    app.state.rag.originals.joinpath(did).unlink()
    response = client.get(f'/api/documents/{did}/file')
    assert response.status_code == 200 and response.content == TEXT
    assert objects.gets == 1


def test_malformed_files_and_oversize_denied_before_indexing(rag_setup):
    client, app, provider, objects = rag_setup
    bid = new_base(client)
    route = f'/api/knowledge-bases/{bid}/documents'
    assert client.post(route, params={'filename':'bad.pdf'}, content=b'not PDF', headers={'Content-Type':'application/pdf'}).status_code == 422
    assert client.post(route, params={'filename':'bad.html'}, content=b'<html>', headers={'Content-Type':'text/html'}).status_code == 422
    assert client.post(route, params={'filename':'huge.txt'}, content=b'x'*(10*1024*1024+1), headers={'Content-Type':'text/plain'}).status_code == 413
    assert provider.embeddings == objects.puts == 0


def test_index_integrity_checked_before_deserialization(rag_setup):
    client, app, provider, _ = rag_setup
    bid, _, _ = ready_base(client)
    with app.state.store.connection() as db:
        db.execute('UPDATE rag_indexes SET blob=? WHERE base_id=?', (b'bad-index', bid))
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 409
    assert provider.completions == 0


def test_reindex_publishes_new_version_and_preserves_old_source(rag_setup):
    client, app, provider, _ = rag_setup
    bid, _, doc = ready_base(client)
    old = client.get('/api/knowledge-bases').json()[0]['revision']
    assert client.post(f'/api/knowledge-bases/{bid}/reindex').status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        base = next(base for base in client.get('/api/knowledge-bases').json() if base['id'] == bid)
        if base['revision'] > old and base['ready']:
            break
        time.sleep(.03)
    else:
        pytest.fail('Reindex did not publish')
    assert client.get('/api/sources/' + doc['first_chunk_id']).status_code == 200


def pdf_bytes(pages):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 50 750 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def test_pdf_page_citation(rag_setup):
    client, app, _, _ = rag_setup
    bid = new_base(client)
    pdf = pdf_bytes(['Manufacturing produced textile supplies and ships for trade.', 'Education reform established navigation school mathematics in 1701.'])
    did = upload(client, bid, pdf, 'book.pdf')['id']
    wait_document(client, bid, did)
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 200
    source = app.state.store.messages(cid)[-1]['metadata']['citations'][0]
    assert source['page'] == 2
    assert client.get('/api/sources/' + source['chunk_id']).json()['page'] == 2


def test_scanned_pdf_is_flagged_without_embedding_spend(rag_setup):
    from pypdf import PdfWriter
    client, app, provider, _ = rag_setup
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    output = io.BytesIO(); writer.write(output)
    bid = new_base(client)
    did = upload(client, bid, output.getvalue(), 'scan.pdf')['id']
    doc = wait_document(client, bid, did, 'failed')
    assert 'OCR' in doc['error']
    assert provider.embeddings == 0


def test_nonadmin_can_read_global_sources_but_cannot_mutate(tmp_path):
    from test_app import live_settings
    settings = live_settings(tmp_path)
    settings.rag_enabled = True; settings.s3_key = 'fake'; settings.s3_secret = 'fake'
    app = create_app(settings, RagProvider()); app.state.rag.objects = FakeObjects()
    store = app.state.store
    store.user('admin', 'admin@example.com', 'Admin');store.user('reader', 'user@example.com', 'Reader')
    with TestClient(app, base_url=settings.origin) as client:
        client.headers['Origin'] = settings.origin
        token = store.session('reader', 3600); client.cookies.set('gosboard_session', token)
        client.headers['X-CSRF-Token'] = store.auth(token)['csrf']
        assert client.get('/api/knowledge-bases').status_code == 200
        assert client.post('/api/knowledge-bases', json={'name':'Forbidden'}).status_code == 403
        assert client.delete('/api/knowledge-bases/' + str(uuid4())).status_code == 403
        assert client.post('/api/documents/' + str(uuid4()) + '/retry').status_code == 403
        client.cookies.clear()
        assert client.get('/api/sources/' + 'a'*64).status_code == 401


def test_restore_rehearsal_checks_serialized_index(rag_setup, tmp_path):
    import importlib.util
    import sys
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    spec = importlib.util.spec_from_file_location('restore_rag_check', scripts / 'verify_restore.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    backup_spec = importlib.util.spec_from_file_location('snapshot_rag_check', scripts / 'backup.py')
    backup = importlib.util.module_from_spec(backup_spec)
    backup_spec.loader.exec_module(backup)
    client, app, _, _ = rag_setup
    ready_base(client)
    target = tmp_path / 'snapshot.sqlite3'
    backup.snapshot(app.state.store.path, target)
    checked = module.verify(target, CONFIG)
    assert checked['faiss_indexes_verified'] == 1
    assert checked['network'] == 'not used'
    with sqlite3.connect(target) as db:
        db.execute("UPDATE rag_indexes SET sha='corrupt'")
    with pytest.raises(ValueError, match='FAISS checksum'):
        module.verify(target, CONFIG)


def test_grouped_citation_markers_are_validated_and_linkable():
    sources = [{'id': 'D1'}, {'id': 'D2'}]
    answer, citations, supported = validate_rag_answer(json.dumps({
        'supported': True, 'answer': 'Confirmed [D1, D2]', 'used_sources': ['D1', 'D2']}), sources)
    assert answer == 'Confirmed [D1] [D2]'
    assert supported and citations == sources
    with pytest.raises(HTTPException):
        validate_rag_answer(json.dumps({'supported': True, 'answer': 'Unverified [D1] [D99, D98]', 'used_sources': ['D1']}), sources)


def test_stop_during_query_embedding_leaves_no_half_turn(rag_setup):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    client, app, provider, _ = rag_setup
    bid, _, _ = ready_base(client)
    cid, rid = rag_chat(client, bid), str(uuid4())
    started = threading.Event()
    async def slow_embedding(text, kind, settings):
        started.set()
        await asyncio.sleep(60)
    provider.embed = slow_embedding
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f'/api/chats/{cid}/messages', json={'text':'A fresh query','request_id':rid})
        assert started.wait(3)
        assert client.post(f'/api/requests/{rid}/cancel').json()['cancel_requested']
        assert pending.result(timeout=3).status_code == 409
    assert app.state.store.messages(cid) == []
    assert provider.completions == 0



def docx_bytes(xml=None):
    import zipfile
    data = io.BytesIO()
    xml = xml or ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                  '<w:p><w:r><w:t>Education reform navigation school mathematics in 1701.</w:t></w:r></w:p>'
                  '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Navigation and mathematics prepared specialists.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                  '</w:body></w:document>')
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', xml)
    return data.getvalue()


@pytest.mark.parametrize('kind', ['docx', 'doc'])
def test_word_ingestion_citations_and_original(rag_setup, kind):
    client, app, provider, _ = rag_setup
    bid = new_base(client)
    content = docx_bytes() if kind == 'docx' else (Path(__file__).parent / 'fixtures/education.doc').read_bytes()
    did = upload(client, bid, content, 'education.' + kind)['id']
    document = wait_document(client, bid, did)
    assert document['total'] >= 1
    cid = rag_chat(client, bid)
    assert ask(client, cid).status_code == 200
    citation = client.get(f'/api/chats/{cid}/messages').json()[-1]['metadata']['citations'][0]
    source = client.get('/api/sources/' + citation['chunk_id']).json()
    assert source['page'] is None
    assert 'navigation' in source['text'].lower()
    if kind == 'docx': assert 'prepared specialists' in source['text']
    original = client.get(source['document_url'])
    assert original.content == content
    assert original.headers['content-type'].startswith('application/')


@pytest.mark.parametrize('content', [b'not ZIP', docx_bytes('<invalid'), docx_bytes('<!DOCTYPE foo [<!ENTITY x "expanded">]><foo>&x;</foo>'), docx_bytes('x' * (8 * 1024 * 1024 + 1))])
def test_invalid_docx_never_embeds(rag_setup, content):
    client, _, provider, _ = rag_setup
    bid = new_base(client)
    result = client.post(f'/api/knowledge-bases/{bid}/documents', params={'filename':'invalid.docx'}, content=content,
                        headers={'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})
    if result.status_code == 202:
        document = wait_document(client, bid, result.json()['id'], 'failed')
        assert document['error']
    else:
        assert result.status_code == 422
    assert provider.embeddings == 0
