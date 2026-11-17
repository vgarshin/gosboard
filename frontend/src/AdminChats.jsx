import React,{useEffect,useState} from 'react';
import {MessageText,CitationList} from './SourceViewer';
export function AdminChats({api,onSource}) {
 const [owner,setOwner]=useState(''),[offset,setOffset]=useState(0),[version,setVersion]=useState(0),[selected,setSelected]=useState('');
 const [listing,setListing]=useState(null),[record,setRecord]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[reading,setReading]=useState(false);
 useEffect(()=>{let live=true;setLoading(true);setError('');api(`/admin/chats?offset=${offset}&limit=50${owner?'&owner='+encodeURIComponent(owner):''}`).then(data=>{if(live)setListing(data);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[owner,offset,version]);
 useEffect(()=>{let live=true;setRecord(null);if(!selected){setReading(false);return()=>{live=false;};}setReading(true);setError('');api(`/admin/chats/${selected}/messages`).then(data=>{if(live)setRecord(data);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setReading(false);});return()=>{live=false;};},[selected,version]);
 function move(value){setSelected('');setRecord(null);setOffset(value);}
 function openSource(reference){
  if(typeof reference==='string'){onSource(reference);return;}
  const source=record?.messages.flatMap(message=>message.metadata.citations||[]).find(source=>source.type==='web'&&source.source_id===reference.id);
  if(source)onSource({...reference,source});
 }
 return <section className="admin-chats" aria-label="Разговоры пользователей"><h3>Все разговоры</h3><p>Просмотр сообщений всех пользователей, включая отключённые учётные записи. Открытие разговора фиксируется в журнале. Здесь нельзя отправлять сообщения или удалять чужие разговоры.</p>
 <div className="admin-chat-controls"><label>Владелец<select aria-label="Владелец разговора" value={owner} disabled={loading} onChange={e=>{setOwner(e.target.value);move(0);}}><option value="">Все пользователи</option>{listing?.owners.map(user=><option key={user.user_id} value={user.user_id}>{user.email} · {user.chat_count}</option>)}</select></label><button type="button" disabled={loading||reading} onClick={()=>setVersion(value=>value+1)}>Обновить разговоры</button></div>
 {error&&<p className="error" role="alert">{error}</p>}{loading&&<p role="status">Загрузка списка…</p>}
 {listing&&<><p>Всего: {listing.total}</p><div className="admin-chat-list">{listing.items.map(chat=><button type="button" className="admin-chat-row" disabled={loading} key={chat.id} aria-pressed={selected===chat.id} onClick={()=>setSelected(chat.id)}><strong>{chat.title}</strong><span>{chat.email} · {new Date(chat.updated*1000).toLocaleString()}</span></button>)}{!listing.items.length&&<p>Разговоров нет.</p>}</div><div className="form-actions"><button type="button" disabled={loading||offset===0} onClick={()=>move(Math.max(0,offset-50))}>Предыдущие</button><span>{listing.total?offset+1:0}–{Math.min(offset+listing.limit,listing.total)} из {listing.total}</span><button type="button" disabled={loading||offset+listing.limit>=listing.total} onClick={()=>move(offset+50)}>Следующие</button></div></>}
 {reading&&<p role="status">Открываем переписку…</p>}
 {record&&<section className="admin-transcript" aria-label="Переписка пользователя"><h3>{record.chat.title}</h3><p className="admin-read-only">Владелец: {listing?.owners.find(user=>user.user_id===record.chat.user_id)?.email||record.chat.user_id} · Только просмотр</p>{record.messages.length?record.messages.map(message=><article key={message.id} className="admin-message"><strong>{message.role==='assistant'?'AI Talks':'Пользователь'}</strong><time>{new Date(message.created*1000).toLocaleString()}</time>{message.metadata.model_label&&<small>{message.metadata.model_label}</small>}<div className="message-text"><MessageText message={message} onSource={openSource}/></div><CitationList sources={message.metadata.citations} onSource={openSource}/></article>):<p>Сообщений пока нет.</p>}</section>}
 </section>;
}
