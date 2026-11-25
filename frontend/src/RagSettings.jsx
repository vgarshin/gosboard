import {Toggle} from './Toggle';
import React, {useEffect, useState} from 'react';
export function RagSettings({api, baseId}) {
 const [bases,setBases]=useState([]),[selected,setSelected]=useState(baseId || ''),[policy,setPolicy]=useState(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 async function load(){const list=await api('/knowledge-bases');setBases(list);return list;}
 function draft(base){return base?{chunk_size:base.chunk_size,chunk_overlap:base.chunk_overlap,top_k:base.top_k,top_k_documents:base.top_k_documents,reindex:false}:null;}
 useEffect(()=>{load().then(list=>{if(baseId)setPolicy(draft(list.find(base=>base.id===baseId)));}).catch(e=>setError(e.message));},[baseId]);
 function choose(id){setSelected(id);setError('');setNotice('');setPolicy(draft(bases.find(base=>base.id===id)));}
 async function save(e){e.preventDefault();setBusy(true);setError('');setNotice('');try{await api(`/knowledge-bases/${selected}/policy`,{method:'PUT',body:JSON.stringify(policy)});await load();setPolicy({...policy,reindex:false});setNotice('Настройки RAG сохранены');}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <><p>Изменение размера или перекрытия фрагментов требует переиндексации.</p>{error&&<div className="error" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
 <form className="management-form" onSubmit={save}>{!baseId && <label>База знаний<select aria-label="Настраиваемая база знаний" value={selected} disabled={busy} onChange={e=>choose(e.target.value)}><option value="">Выберите базу</option>{bases.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>}
 {policy&&<><label>Размер фрагмента, символов<input type="number" min={400} max={2000} aria-label="Размер фрагмента" value={policy.chunk_size} disabled={busy} onChange={e=>setPolicy({...policy,chunk_size:Number(e.target.value)})}/></label>
 <label>Перекрытие, символов<input type="number" min={0} max={400} aria-label="Перекрытие" value={policy.chunk_overlap} disabled={busy} onChange={e=>setPolicy({...policy,chunk_overlap:Number(e.target.value)})}/></label>
 <label>Top K документов<input type="number" min={1} max={10} aria-label="Top K документов" value={policy.top_k_documents} disabled={busy} onChange={e=>setPolicy({...policy,top_k_documents:Number(e.target.value)})}/></label>
 <label>Максимум фрагментов<input type="number" min={1} max={10} aria-label="Top K" value={policy.top_k} disabled={busy} onChange={e=>setPolicy({...policy,top_k:Number(e.target.value)})}/></label>
 <label className="account-enabled"><Toggle aria-label="Подтвердить переиндексацию" checked={policy.reindex} disabled={busy} onChange={e=>setPolicy({...policy,reindex:e.target.checked})}/>Подтверждаю переиндексацию и расходы при изменении фрагментов</label><small>Top K ограничивает число файлов, а лимит фрагментов — общий объём выбранных отрывков. Контекст может вместить меньше источников.</small><button className="primary" disabled={busy}>Сохранить настройки RAG</button></>}
 </form></>;
}
