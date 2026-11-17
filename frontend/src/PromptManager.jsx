import React, {useEffect, useState} from 'react';
import {Plus, Trash2} from 'lucide-react';
export function PromptManager({api,initialId,onChanged,onPick,canPick}) {
 const [items,setItems]=useState([]),[revision,setRevision]=useState(null),[selected,setSelected]=useState(initialId==='new'?'':initialId||'default');
 const [label,setLabel]=useState(''),[text,setText]=useState(''),[creating,setCreating]=useState(initialId==='new');
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const item=items.find(p=>p.id===selected);
 function fill(list,id){const p=list.find(p=>p.id===id);setSelected(id);setLabel(p?.label||'');setText(p?.system_prompt||'');setCreating(!p);}
 async function reload(){const data=await api('/editor/prompts');setItems(data.prompts);setRevision(data.revision);if(!creating)fill(data.prompts,selected);return data;}
 useEffect(()=>{reload().catch(e=>setError(e.message)).finally(()=>setLoading(false));},[]);
 function choose(id){fill(items,id);setError('');setNotice('');}
 function create(){setSelected('');setLabel('');setText('');setCreating(true);setError('');setNotice('');}
 async function save(e){
  e.preventDefault();setBusy(true);setError('');setNotice('');
  try{const result=await api(creating?'/editor/prompts':`/editor/prompts/${selected}`,{method:creating?'POST':'PUT',body:JSON.stringify({expected_revision:revision,label,system_prompt:text})});setItems(result.prompts);setRevision(result.revision);fill(result.prompts,result.prompt_id);await onChanged();setNotice(creating?'Промпт создан':'Промпт сохранён. Следующие ответы используют новую версию.');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function remove(){
  if(!window.confirm(`Удалить промпт «${item.label}»? Старые ответы сохранят свои настройки.`))return;
  setBusy(true);setError('');setNotice('');
  try{const result=await api(`/editor/prompts/${selected}`,{method:'DELETE',body:JSON.stringify({expected_revision:revision})});setItems(result.prompts);setRevision(result.revision);fill(result.prompts,'default');await onChanged();setNotice('Промпт удалён');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <section className="prompt-manager" aria-label="Управление промптами"><p>Промпты общие для всех разговоров. Изменения действуют на следующие ответы; прошлые ответы сохраняют свою версию.</p>
  {error&&<div className="error" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
  <div className="prompt-toolbar"><button type="button" className="primary" disabled={busy||loading||items.length>=31} onClick={create}><Plus size={15}/>Создать промпт</button><button type="button" disabled={busy||loading} onClick={()=>{setError('');reload().catch(e=>setError(e.message));}}>Обновить список</button></div>
  <form className="management-form" onSubmit={save}>
   <label>Промпт<select aria-label="Управляемый промпт" value={selected} disabled={busy} onChange={e=>choose(e.target.value)}>{creating&&<option value="">Новый промпт</option>}{items.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
   <label>Название<input aria-label="Название промпта" value={label} maxLength={100} required disabled={busy||!!item?.builtin} onChange={e=>setLabel(e.target.value)}/></label>
   <label>Текст промпта<textarea aria-label="Текст промпта" value={text} rows={9} maxLength={6000} required disabled={busy} onChange={e=>setText(e.target.value)}/></label>
   <small>{text.length}/6000 символов. Текст также должен помещаться в выбранный размер контекста.</small>
   <div className="form-actions"><button className="primary" disabled={busy||revision===null||!label.trim()||!text.trim()}>{creating?'Создать':'Сохранить промпт'}</button>{!creating&&!item?.builtin&&<button type="button" className="catalog-remove" disabled={busy||!!item?.used_by_chats} onClick={remove}><Trash2 size={14}/>Удалить промпт</button>}</div>
   {!creating&&item?.builtin&&<small>Основной промпт можно изменить, но нельзя удалить.</small>}
   {!creating&&!!item?.used_by_chats&&<small>Используется в {item.used_by_chats} разговорах. Для удаления сначала выберите в них другой промпт.</small>}
   {!creating&&canPick&&<button type="button" className="prompt-pick" disabled={busy} onClick={()=>onPick(selected)}>Подставить в настройки разговора</button>}
  </form>
 </section>;
}
