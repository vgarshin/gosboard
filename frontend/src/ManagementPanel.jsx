import React, {useEffect, useState} from 'react';
import {Plus, X} from 'lucide-react';
import {AdminChats} from './AdminChats';
import {useDialog} from './useDialog';
const roles={user:'Пользователь',editor:'Редактор',admin:'Администратор'};
const empty={email:'',role:'user',enabled:true};
export function ManagementPanel({api,onClose,onSource}) {
 const dialog=useDialog(onClose);
 const [accounts,setAccounts]=useState([]),[audit,setAudit]=useState([]);
 const [account,setAccount]=useState(empty),[mode,setMode]=useState(null),[tab,setTab]=useState('users');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 async function load(){const [users,events]=await Promise.all([api('/admin/users'),api('/admin/audit')]);setAccounts(users);setAudit(events);}
 useEffect(()=>{load().catch(e=>setError(e.message));},[]);
 async function save(event){
  event.preventDefault();setBusy(true);setError('');setNotice('');
  try{await api('/admin/users',{method:mode==='create'?'POST':'PUT',body:JSON.stringify(account)});await load();setMode(null);setNotice(mode==='create'?'Пользователь добавлен':'Роль сохранена');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function toggle(a,enabled){
  setBusy(true);setError('');setNotice('');setAccounts(list=>list.map(row=>row.email===a.email?{...row,enabled}:row));
  try{await api('/admin/users',{method:'PUT',body:JSON.stringify({email:a.email,role:a.role,enabled})});await load();setNotice(enabled?'Доступ включён':'Доступ отключён; сессии завершены');}
  catch(e){setAccounts(list=>list.map(row=>row.email===a.email?a:row));setError(e.message);}finally{setBusy(false);}
 }
 return <div className="modal-backdrop"><section ref={dialog} className="modal management-modal" role="dialog" aria-modal="true" aria-label="Пользователи">
  <button type="button" className="icon-button modal-close" aria-label="Закрыть пользователей" onClick={onClose}><X size={18}/></button><span className="eyebrow">АДМИНИСТРАТОР</span><h2>Пользователи</h2>
  <div className="management-tabs"><button type="button" aria-pressed={tab==='users'} onClick={()=>setTab('users')}>Пользователи</button><button type="button" aria-pressed={tab==='chats'} onClick={()=>setTab('chats')}>Разговоры</button><button type="button" aria-pressed={tab==='audit'} onClick={()=>{setTab('audit');load().catch(e=>setError(e.message));}}>Журнал</button></div>
  {error&&<div className="error" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
  {tab==='users'&&<><p>Доступ переключается рядом с пользователем. Для новой учётной записи укажите основной email Яндекса.</p>
   <button type="button" className="primary add-user" disabled={busy} onClick={()=>{setAccount({...empty});setMode('create');setError('');setNotice('');}}><Plus size={16}/>Добавить пользователя</button>
   {mode&&<form className="management-form account-form" onSubmit={save}><h3>{mode==='create'?'Новый пользователь':'Изменение роли'}</h3>
    <label>Email<input type="email" aria-label="Email пользователя" required maxLength={254} value={account.email} disabled={busy||mode==='edit'} onChange={e=>setAccount({...account,email:e.target.value})}/></label>
    <label>Роль<select aria-label="Роль пользователя" value={account.role} disabled={busy} onChange={e=>setAccount({...account,role:e.target.value})}>{Object.entries(roles).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
    <div className="form-actions"><button className="primary" disabled={busy}>{mode==='create'?'Добавить':'Сохранить роль'}</button><button type="button" disabled={busy} onClick={()=>setMode(null)}>Отмена</button></div>
   </form>}
   <div className="account-list">{accounts.map(a=><article className="account-row" key={a.email}><div><strong>{a.email}</strong><span>{roles[a.role]}{!a.signed_in?' · ещё не входил':''}</span><button type="button" className="edit-role" disabled={busy} onClick={()=>{setAccount({email:a.email,role:a.role,enabled:!!a.enabled});setMode('edit');setError('');setNotice('');}}>Изменить роль</button></div>
    <label className="access-toggle"><input type="checkbox" role="switch" aria-label={`Доступ: ${a.email}`} checked={!!a.enabled} disabled={busy} onChange={e=>toggle(a,e.target.checked)}/><span>{a.enabled?'Включён':'Отключён'}</span></label>
   </article>)}</div>
  </>}
  {tab==='chats'&&<AdminChats api={api} onSource={onSource}/>}
  {tab==='audit'&&<div className="audit-list">{audit.length?audit.map(a=><article key={a.id}><strong>{a.action}</strong><span>{new Date(a.created*1000).toLocaleString()} · {a.target}</span><code>{a.details}</code></article>):<p>Изменений пока нет.</p>}</div>}
 </section></div>;
}
