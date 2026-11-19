import React,{useState} from 'react';
import {X} from 'lucide-react';
import {useDialog} from './useDialog';
export function ExportChat({chatId,spaceId,onClose}) {
 const dialog=useDialog(onClose),[format,setFormat]=useState('md'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function download(e){e.preventDefault();setBusy(true);setError('');try{
  const response=await fetch(`/api/chats/${chatId}/export?format=${format}`, {headers:{'X-Space-ID':spaceId}});
  if(!response.ok){const problem=await response.json().catch(()=>({}));throw new Error(problem.detail||'Не удалось скачать разговор');}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=`aitalks-${chatId}.${format}`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);onClose();
 }catch(e){setError(e.message);}finally{setBusy(false);}}
 return <div className="modal-backdrop"><section ref={dialog} className="modal" role="dialog" aria-modal="true" aria-label="Экспорт разговора"><button className="icon-button modal-close" aria-label="Закрыть экспорт" onClick={onClose}><X size={18}/></button><h2>Скачать разговор</h2><p>Сохранённые сообщения текущего разговора.</p><form className="management-form" onSubmit={download}><label>Формат<select aria-label="Формат экспорта" value={format} disabled={busy} onChange={e=>setFormat(e.target.value)}><option value="md">Markdown (.md) — форматированный текст</option><option value="json">JSON (.json) — структурированные данные</option><option value="txt">Текст (.txt) — простой текст</option></select></label>{error&&<p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>Скачать</button></form></section></div>;
}
