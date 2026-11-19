import {FilePicker} from './FilePicker';
import React, {useEffect, useState} from 'react';
import {PanelTabs} from './PanelTabs';
import {useDialog} from './useDialog';
import {FileText, Plus, RefreshCw, Trash2, X} from 'lucide-react';

const statuses = {queued_upload: 'Сохраняется', uploading: 'Сохраняется', queued: 'В очереди', processing: 'Индексация', ready: 'Готов', failed: 'Ошибка'};

export function KnowledgeManager({api, admin, editor, initialBases, onClose, refresh, onSource}) {
  const dialog = useDialog(onClose);
  const [tab, setTab] = useState('existing');
  const [managedEmail, setManagedEmail] = useState('');
  const canManage = admin || editor;
  const [bases, setBases] = useState(initialBases || []);
  const [selected, setSelected] = useState(initialBases?.[0]?.id || '');
  const [documents, setDocuments] = useState([]);
  const [name, setName] = useState('');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [list, docs] = await Promise.all([api('/knowledge-bases'), selected ? api(`/knowledge-bases/${selected}/documents`) : Promise.resolve([])]);
        if (live) { setBases(list); setDocuments(docs); }
      } catch (e) { if (live) setError(e.message); }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [selected]);
  async function operation(work) {
    if (busy) return;
    setBusy(true); setError('');
    try { await work(); await refresh(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  function create(event) {
    event.preventDefault();
    operation(async () => {
      const base = await api('/knowledge-bases', {method: 'POST', body: JSON.stringify({name})});
      setName(''); setBases(await api('/knowledge-bases')); setSelected(base.id); setTab('existing');
    });
  }
  function upload(event) {
    event.preventDefault();
    if (!file || !selected) return;
    operation(async () => {
      const extension = file.name.split('.').pop().toLowerCase();
      const mime = {pdf: 'application/pdf', txt: 'text/plain', doc: 'application/msword', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}[extension];
      if (!mime) throw new Error('Выберите PDF, DOC, DOCX или TXT');
      await api(`/knowledge-bases/${selected}/documents?filename=${encodeURIComponent(file.name)}`, {method: 'POST', headers: {'Content-Type': mime}, body: file});
      setFile(null);
      setDocuments(await api(`/knowledge-bases/${selected}/documents`));
    });
  }
  function removeDocument(document) {
    if (!window.confirm(`Удалить «${document.filename}»? Ссылки на этот источник станут недоступны.`)) return;
    operation(async () => { await api(`/documents/${document.id}`, {method: 'DELETE'}); setDocuments(await api(`/knowledge-bases/${selected}/documents`)); });
  }
  function removeBase() {
    if (!window.confirm('Удалить базу знаний и все её документы?')) return;
    operation(async () => {
      await api(`/knowledge-bases/${selected}`, {method: 'DELETE'});
      const remaining = await api('/knowledge-bases'); setBases(remaining); setSelected(remaining[0]?.id || '');
    });
  }
  return <div className="modal-backdrop"><section ref={dialog} className="modal knowledge-modal" role="dialog" aria-modal="true" aria-label="Базы знаний" onKeyDown={e => {if (e.key === 'Escape') onClose();}}>
    <button className="icon-button modal-close" onClick={onClose} aria-label="Закрыть базы знаний"><X size={18}/></button>
    <span className="eyebrow">ОБЩИЕ МАТЕРИАЛЫ</span><h2>Базы знаний</h2>
    <PanelTabs label="Базы знаний" tabs={[['existing','Существующие базы'],['new','Новая база']]} active={tab} onChange={setTab}>
    <p>Подключайте базы в настройках пространства. Документы меняют администраторы и назначенные менеджеры базы.</p>
    {error && <div className="error" role="alert">{error}</div>}
    {tab === 'new' && canManage && <form className="kb-create" onSubmit={create}><input aria-label="Название новой базы" placeholder="Название новой базы" value={name} maxLength={100} onChange={e => setName(e.target.value)} disabled={busy}/><button className="primary" disabled={busy || !name.trim()}><Plus size={15}/>Создать</button></form>}
    {tab === 'existing' && <>
    <div className="kb-selection"><label>База знаний<select aria-label="Управляемая база знаний" value={selected} disabled={busy} onChange={e => {setSelected(e.target.value); setError('');}}><option value="">Выберите базу</option>{bases.map(base => <option key={base.id} value={base.id}>{base.name} · {base.ready_documents}/{base.documents}</option>)}</select></label>
      {admin && selected && <><button className="icon-button" title="Переиндексировать базу" aria-label="Переиндексировать базу" disabled={busy} onClick={() => operation(() => api(`/knowledge-bases/${selected}/reindex`, {method: 'POST'}))}><RefreshCw size={16}/></button><button className="icon-button" title="Удалить базу" aria-label="Удалить базу" disabled={busy} onClick={removeBase}><Trash2 size={16}/></button></>}
    </div>
    {admin && selected && <div className="management-form">
      <label>Email редактора<input type="email" value={managedEmail} onChange={e => setManagedEmail(e.target.value)}/></label>
      <div className="form-actions">
        {[true,false].map(assigned => <button type="button" key={String(assigned)} disabled={busy || !managedEmail} onClick={() =>
          operation(() => api(`/knowledge-bases/${selected}/managers`, {method:'PUT', body:JSON.stringify({email:managedEmail,assigned})}))
        }>{assigned ? 'Назначить менеджера' : 'Снять менеджера'}</button>)}
        <button type="button" disabled={busy} onClick={() => {
          if (window.confirm('Отозвать поиск в этой базе, включая существующие разговоры?')) {
            operation(() => api(`/knowledge-bases/${selected}/revoke`, {method:'POST'}));
          }
        }}>Отозвать поиск</button>
      </div>
    </div>}
    {canManage && selected && <form className="kb-upload" onSubmit={upload}><div><p>PDF, DOC, DOCX или UTF-8 TXT · до 10 МБ</p><FilePicker file={file} accept=".pdf,.txt,.doc,.docx"
      label="Выбрать документ" disabled={busy} onSelect={setFile}/></div><button className="primary" disabled={!file || busy}>Загрузить документ</button></form>}
    <div className="kb-documents">{documents.map(document => <article key={document.id} className="kb-document"><FileText size={19}/><div className="kb-document-body"><strong>{document.filename}</strong><span className={'document-status ' + document.status}>{statuses[document.status] || document.status}{document.status === 'processing' ? ` · ${document.progress}/${document.total || '…'}` : ''}</span>{document.error && <p>{document.error}</p>}{document.first_chunk_id && <a onClick={e => {e.preventDefault(); onSource(document.first_chunk_id);}} href={`/sources/${document.first_chunk_id}`}>Открыть фрагмент</a>}</div>{canManage && <div className="kb-document-actions">{document.status === 'failed' && <button className="icon-button" title="Повторить обработку" aria-label={`Повторить ${document.filename}`} disabled={busy} onClick={() => operation(() => api(`/documents/${document.id}/retry`, {method: 'POST'}))}><RefreshCw size={15}/></button>}<button className="icon-button" title="Удалить документ" aria-label={`Удалить ${document.filename}`} disabled={busy} onClick={() => removeDocument(document)}><Trash2 size={15}/></button></div>}</article>)}</div>
    {selected && !documents.length && <p className="kb-empty">В этой базе пока нет документов.</p>}
    {!bases.length && <p className="kb-empty">Администратор может создать первую общую базу знаний.</p>}
    {canManage && <p className="kb-note">Индексация выполняется в фоне. Повторная загрузка того же файла не создаёт копию. Для сканированных PDF сначала нужен OCR.</p>}
    </>}
    </PanelTabs>
  </section></div>;
}
