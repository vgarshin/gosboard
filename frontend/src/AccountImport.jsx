import {FilePicker} from './FilePicker';
import {Toggle} from './Toggle';
import {Download} from 'lucide-react';
import React, {useEffect, useState} from 'react';

export function AccountImport({api, onImported}) {
  const [spaces, setSpaces] = useState([]);
  const [defaults, setDefaults] = useState([]);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [approve, setApprove] = useState(false);
  const [approveExisting, setApproveExisting] = useState(false);
  const [lines, setLines] = useState([]);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api('/management/spaces').then(items => setSpaces(items.filter(item => !item.archived)))
      .catch(e => setError(e.message));
  }, []);

  async function run(work) {
    setBusy(true); setError('');
    try {await work();}
    catch (e) {setError(e.message);}
    finally {setBusy(false);}
  }

  return <div className="management-form">
    <p>До 1000 строк и 1 МБ. Укажите основной email Яндекс ID. Поле space_ids принимает идентификаторы через точку с запятой.</p>
    <a className="csv-template" download="accounts.csv" href="data:text/csv;charset=utf-8,email%2Cname%2Crole%2Cspace_ids%0A"><Download size={16} aria-hidden="true"/>Скачать шаблон CSV</a>
    {error && <p className="error" role="alert">{error}</p>}
    <FilePicker file={file} accept=".csv,text/csv" label="Выбрать CSV" disabled={busy}
      onSelect={selected => {setFile(selected); setPreview(null); setReport(null); setError('');}}/>
    <label>Пространства для строк без назначений<select multiple value={defaults} onChange={e => {
      setDefaults([...e.target.selectedOptions].map(option => option.value)); setPreview(null);
    }}>{spaces.map(space => <option key={space.id} value={space.id}>{space.name} ({space.id})</option>)}</select></label>
    <button disabled={!file || busy} onClick={() => run(async () => {
      if (file.size > 1024 * 1024) throw new Error('Максимальный размер списка — 1 МБ');
      const params = new URLSearchParams(defaults.map(sid => ['space_id', sid]));
      const data = await api(`/admin/users/import?${params}`, {method:'POST', body:file});
      setPreview(data);
      setLines(data.rows.filter(row => !row.error).map(row => row.line));
      setReport(null);
    })}>Предпросмотр</button>
    {preview && <>
      <ul className="import-preview">{preview.rows.map(row => <li key={row.line}>
        <label className="account-enabled"><Toggle checked={lines.includes(row.line)} disabled={busy || !!row.error}
          onChange={e => setLines(e.target.checked ? [...lines,row.line] : lines.filter(line => line !== row.line))}/>
        <span>Строка {row.line} · {row.error || [
          row.email, row.name, row.role, row.space_ids.map(id => spaces.find(space => space.id === id)?.name || id).join(', '),
          row.existing === -1 ? 'ожидает допуска' : row.existing !== null ? 'будет пропущена' : 'новая запись',
        ].filter(Boolean).join(' · ')}</span></label>
      </li>)}</ul>
      <label><Toggle checked={approve} onChange={e => setApprove(e.target.checked)}/> Одобрить при импорте</label>
      {approve && <label><Toggle checked={approveExisting} onChange={e => setApproveExisting(e.target.checked)}/> Также одобрить существующие заявки из списка</label>}
      <button disabled={busy || !lines.length} onClick={() => run(async () => {
        const result = await api(`/admin/users/import/${preview.batch}`, {
          method:'POST', body:JSON.stringify({approve, approve_existing:approve && approveExisting,lines}),
        });
        setReport(result); setPreview(null); await onImported();
      })}>Подтвердить импорт</button>
    </>}
    {report && <p role="status">Создано {report.created}, допущено {report.admitted}, пропущено {report.skipped}.</p>}
  </div>;
}
