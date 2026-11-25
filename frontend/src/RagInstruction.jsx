import React, {useEffect, useState} from 'react';

export function RagInstruction({api}) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function load() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/admin/rag/instruction');
      setSaved(result);
      setDraft(result.instruction);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/admin/rag/instruction', {
        method: 'PUT',
        body: JSON.stringify({instruction: draft, expected_revision: saved.revision}),
      });
      setSaved(result);
      setDraft(result.instruction);
      setNotice('Инструкция сохранена');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return <form className="management-form" onSubmit={save}>
    <p><strong>Системная инструкция. Меняйте с осторожностью.</strong> Она применяется ко всем базам знаний и ответам с веб-источниками. Правила формата и цитирования влияют на то, сможет ли приложение принять ответ модели.</p>
    {error && <div className="error" role="alert">{error}</div>}
    {notice && <p role="status">{notice}</p>}
    {saved && <label>Системная инструкция RAG
      <textarea rows={13} value={draft} onChange={event => setDraft(event.target.value)}
        maxLength={3000} required disabled={busy}/>
    </label>}
    <div className="form-actions">
      {saved && <button className="primary" disabled={busy || !draft.trim() || draft.trim() === saved.instruction}>Сохранить инструкцию</button>}
      {error && <button type="button" disabled={busy} onClick={load}>Загрузить актуальную версию</button>}
    </div>
  </form>;
}
