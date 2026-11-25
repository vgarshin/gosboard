import {Toggle} from './Toggle';
import React, {useEffect, useState} from 'react';
import {Plus, X} from 'lucide-react';
import {PanelTabs} from './PanelTabs';
import {AdminChats} from './AdminChats';
import {AccountImport} from './AccountImport';
import {useDialog} from './useDialog';
const roles={user:'Пользователь',editor:'Редактор',admin:'Администратор'};
const empty={email:'',role:'user',enabled:true};
export function ManagementPanel({api,onClose,onSource,admin}) {
 const dialog=useDialog(onClose);
 const [accounts,setAccounts]=useState([]);
 const [spaces,setSpaces]=useState([]);
 const [memberships,setMemberships]=useState([]);
 const [expanded,setExpanded]=useState(null);
 const [account,setAccount]=useState(empty),[mode,setMode]=useState(null),[tab,setTab]=useState('users');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 async function load(){
  const [users, managedSpaces, assignments] = await Promise.all([
   admin ? api('/admin/users') : Promise.resolve(null),
   api('/management/spaces'),
   api('/management/space-accounts'),
  ]);
  setAccounts(users ?? assignments);
  setSpaces(managedSpaces.filter(space => !space.archived));
  setMemberships(assignments);
 }

 async function assign(email, sid, assigned, management) {
  setBusy(true);setError('');setNotice('');
  const previous = memberships;
  const field = management ? 'managed' : 'memberships';
  setMemberships(items => items.map(item => item.email === email ? {
   ...item, [field]: assigned ? [...item[field], sid] : item[field].filter(id => id !== sid),
  } : item));
  try {
   await api(`/management/spaces/${sid}/assignments`, {
    method:'PUT', body:JSON.stringify({email, assigned, management}),
   });
   setNotice('Назначение сохранено');
  } catch (e) {
   setMemberships(previous);setError(e.message);
  } finally {
   setBusy(false);
  }
 }
 useEffect(() => { load().catch(e => setError(e.message)); }, [admin]);
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
  <button type="button" className="icon-button modal-close" aria-label="Закрыть пользователей" onClick={onClose}><X size={18}/></button><h2>Пользователи</h2>
  <PanelTabs label="Пользователи" tabs={admin ? [['users','Пользователи'],['import','Загрузить список'],['chats','Разговоры']] : [['users','Пользователи']]} active={tab} onChange={setTab}>
  {error&&<div className="error" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
  {tab==='users'&&<>
   <p>{admin ? 'Доступ к приложению переключается рядом с аккаунтом. Членство и управление пространствами задаются отдельно.'
    : 'Выдавайте допущенным пользователям доступ к разговорам в пространствах, которыми вы управляете.'}</p>
   {admin && <button type="button" className="primary add-user" disabled={busy} onClick={()=>{setAccount({...empty});setMode('create');setError('');setNotice('');}}><Plus size={16}/>Добавить пользователя</button>}
   {admin && mode && <form className="management-form account-form" onSubmit={save}><h3>{mode==='create'?'Новый пользователь':mode==='admit'?'Допуск пользователя':'Изменение роли'}</h3>
    <label>Email<input type="email" aria-label="Email пользователя" required maxLength={254} value={account.email} disabled={busy||mode!=='create'} onChange={e=>setAccount({...account,email:e.target.value})}/></label>
    <label>Роль<select aria-label="Роль пользователя" value={account.role} disabled={busy} onChange={e=>setAccount({...account,role:e.target.value})}>{Object.entries(roles).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
    <div className="form-actions"><button className="primary" disabled={busy}>{mode==='create'?'Добавить':mode==='admit'?'Допустить':'Сохранить роль'}</button><button type="button" disabled={busy} onClick={()=>setMode(null)}>Отмена</button></div>
   </form>}
   <div className="account-list">{accounts.map(a => {
    const assignment = memberships.find(item => item.email === a.email);
    return <div className="account-card" key={a.email}>
     <article className="account-row">
      <div>
       <strong>{a.name ? `${a.name} · ${a.email}` : a.email}</strong>
       <span>{a.status==='pending' ? 'Ожидает допуска' : roles[a.role]}
        {a.login_method==='vtb-edu' ? ' · VTB Edu' : a.login_method==='yandex' ? ' · Яндекс ID' : ''}
        {admin && !a.signed_in ? ' · ещё не входил' : ''}</span>
       {admin && <button type="button" className="edit-role" disabled={busy} onClick={() => {
        setAccount({email:a.email,role:a.role,enabled:a.status==='pending' || !!a.enabled});
        setMode(a.status==='pending' ? 'admit' : 'edit');setError('');setNotice('');
       }}>{a.status==='pending' ? 'Допустить' : 'Изменить роль'}</button>}
      </div>
      {admin && a.status!=='pending' && <label className="access-toggle">
       <Toggle role="switch" aria-label={`Доступ: ${a.email}`} checked={!!a.enabled} disabled={busy}
        onChange={e=>toggle(a,e.target.checked)}/>
       <span>{a.enabled?'Включён':'Отключён'}</span>
      </label>}
     </article>
     <button className="edit-spaces" type="button" aria-expanded={expanded===a.email}
      onClick={()=>setExpanded(expanded===a.email?null:a.email)}>Назначить пространства</button>
     {expanded===a.email && <>
      <SpaceAssignments title="Доступ к разговорам" spaces={spaces} selected={assignment?.memberships}
       busy={busy} onChange={(sid, checked) => assign(a.email, sid, checked, false)}/>
      {admin && a.role==='editor' && <SpaceAssignments title="Управление пространствами" spaces={spaces}
       selected={assignment?.managed} owned={assignment?.owned} busy={busy || a.status!=='active'}
       onChange={(sid, checked) => assign(a.email, sid, checked, true)}/>}
     </>}
    </div>;
   })}</div>
  </>}
  {admin && tab==='import'&&<AccountImport api={api} onImported={load}/>}
  {admin && tab==='chats'&&<AdminChats api={api} onSource={onSource}/>}
  </PanelTabs>
 </section></div>;
}

function SpaceAssignments({title, spaces, selected = [], owned = [], busy, onChange}) {
 return <fieldset className="account-spaces">
  <legend>{title}</legend>
  {spaces.length ? spaces.map(space => {
   const isOwner = owned.includes(space.id);
   return <label key={space.id}>
    <Toggle checked={isOwner || selected.includes(space.id)} disabled={busy || isOwner}
     onChange={event => onChange(space.id, event.target.checked)}/>
    {space.name}{isOwner && ' · создатель'}
   </label>;
  }) : <p>Нет активных пространств</p>}
 </fieldset>;
}
