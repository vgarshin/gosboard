import {formatDateTime} from './dateTime';
import {Toggle} from './Toggle';
import React, {useEffect, useState} from 'react';
import {Plus, Trash2, X} from 'lucide-react';
import {useDialog} from './useDialog';
import {PanelTabs} from './PanelTabs';
import {OperationsPanel} from './OperationsPanel';
import {BudgetPanel} from './BudgetPanel';
import {ReasoningModes, REASONING_LABELS, reasoningModes, canonicalReasoning} from './ReasoningModes';
import {RagSettings} from './RagSettings';

export function ConfigurationPanel({api,admin,onClose,onSaved}) {
 const dialog=useDialog(onClose);
 const [models,setModels]=useState([]);
 const [tab,setTab]=useState('defaults');
 const voices = [{id:'marina',label:'Марина'},{id:'filipp',label:'Филипп'}];
 const [revision,setRevision]=useState(null),[draft,setDraft]=useState(null),[activeVersion,setActiveVersion]=useState(null),[history,setHistory]=useState([]);
 const [audit,setAudit]=useState(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[limits,setLimits]=useState(null);
 async function load(){const [live,versions]=await Promise.all([api('/editor/config'),api('/editor/config/versions')]);setDraft(live.config);setRevision(live.revision);setActiveVersion(live.config.version);setHistory(versions);if(admin) {
  const [configuration, availability] = await Promise.all([api('/admin/config'), api('/admin/models')]);
  setLimits(configuration);setModels(availability);
 }}
 useEffect(()=>{load().catch(e=>setError(e.message));},[]);
 function field(key,value){setDraft(prev=>({...prev,[key]:value}));}
 function entry(kind,index,key,value){setDraft(prev=>({...prev,[kind]:prev[kind].map((row,i)=>i===index?{...row,[key]:value}:row)}));}
 function remove(kind,index){setDraft(prev=>({...prev,[kind]:prev[kind].filter((_,i)=>i!==index)}));}
 async function action(work){setBusy(true);setError('');setNotice('');try{await work();await onSaved();setNotice('Сохранено');}catch(e){setError(e.message);}finally{setBusy(false);}}
 function saveGlobal(e){e.preventDefault();action(async()=>{const result=await api('/editor/config',{method:'PUT',body:JSON.stringify({expected_revision:revision,config:draft})});setDraft(result.config);setRevision(result.revision);setActiveVersion(result.config.version);setHistory(await api('/editor/config/versions'));});}
 function changeTab(next) {
  setTab(next); setNotice('');
  if(next==='audit') api('/admin/audit').then(setAudit).catch(e=>setError(e.message));
 }
 return <div className="modal-backdrop"><section ref={dialog} className="modal configuration-modal" role="dialog" aria-modal="true" aria-label="Настройки">
  <button type="button" className="icon-button modal-close" aria-label="Закрыть настройки" onClick={onClose}><X size={18}/></button><span className="eyebrow">{admin?'АДМИНИСТРАТОР':'РЕДАКТОР'}</span><h2>Настройки</h2>
  <PanelTabs label="Настройки" tabs={[['defaults','Общие настройки'],['rag','Настройки RAG'],['budget','Бюджет'],['operations','Мониторинг'],['history','История изменений'],['audit','Журнал']]} active={tab} onChange={changeTab}>
  {error&&<div className="error" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
  {tab==='defaults' && <div className="management-form">
   <h3>Доступные модели</h3>
   <p>Блокировка действует на существующие и новые разговоры.</p>
   {models.map(item => <label key={item.id} className="account-enabled">
    <Toggle checked={!item.blocked} disabled={busy} onChange={e => {
     const blocked = !e.target.checked;
     action(async () => {
      await api(`/admin/models/${encodeURIComponent(item.id)}`, {method:'PUT', body:JSON.stringify({blocked})});
      setModels(await api('/admin/models'));
     });
    }}/>{item.label} · {item.id}
   </label>)}
  </div>}
  {tab==='defaults'&&draft&&<><p>Общие настройки задают значения по умолчанию для новых разговоров.</p>
   <form className="management-form" onSubmit={saveGlobal}>
    <div className="configuration-grid"><label>Температура<input type="number" aria-label="Температура" min={0} max={1} step={0.05} value={draft.temperature} disabled={busy} onChange={e=>field('temperature',Number(e.target.value))}/></label><label>Максимум токенов<input type="number" aria-label="Максимум токенов" min={1} max={2000} value={draft.max_tokens} disabled={busy} onChange={e=>field('max_tokens',Number(e.target.value))}/></label><label>Размер контекста, символов<input type="number" aria-label="Размер контекста" min={4000} max={40000} value={draft.context_chars} disabled={busy} onChange={e=>field('context_chars',Number(e.target.value))}/></label></div>
    <label>Голос по умолчанию<select aria-label="Голос по умолчанию" value={draft.voice} disabled={busy} onChange={e=>field('voice',e.target.value)}>{!voices.some(v=>v.id===draft.voice)&&<option value={draft.voice}>{draft.voice}</option>}{voices.map(v=><option key={v.id} value={v.id}>{v.label}</option>)}</select></label>
    <label>Скорость речи<input type="number" aria-label="Скорость речи" min={0.5} max={2} step={0.1} value={draft.tts_speed} disabled={busy} onChange={e=>field('tts_speed',Number(e.target.value))}/></label>
    <label>Стартовый пример<textarea aria-label="Стартовый пример" rows={5} maxLength={4000} value={draft.demo_prompt} required disabled={busy} onChange={e=>field('demo_prompt',e.target.value)}/></label>
    <h3>Модели в списке</h3>
    <fieldset className="catalog-entry"><legend>Модель 1 · Основная</legend>
    <label>Название основной модели<input aria-label="Название основной модели" value={draft.model_label} maxLength={100} required disabled={busy} onChange={e=>field('model_label',e.target.value)}/></label>
    <label>Код основной модели<input aria-label="Основная модель" value={draft.model} maxLength={150} required disabled={busy} onChange={e=>field('model',e.target.value)}/></label>
    <ReasoningModes value={draft} label="Проверенные режимы основной модели" disabled={busy} onChange={modes=>{field('reasoning_efforts',modes);field('reasoning_supported',modes.length>0);field('reasoning_effort','default');}}/>
    <label>Рассуждения по умолчанию<select aria-label="Рассуждения по умолчанию" value={canonicalReasoning(draft.reasoning_effort)} disabled={busy||!reasoningModes(draft).length} onChange={e=>field('reasoning_effort',e.target.value)}><option value="default">По умолчанию модели</option>{reasoningModes(draft).map(mode=><option key={mode} value={mode}>{REASONING_LABELS[mode]||mode}</option>)}</select></label>
    </fieldset>
    {draft.models.map((m,i)=><fieldset className="catalog-entry" key={i}><legend>Модель {i+2}</legend><label>ID<input aria-label={`ID модели ${i+2}`} value={m.id} maxLength={60} required disabled={busy} onChange={e=>entry('models',i,'id',e.target.value)}/></label><label>Название<input aria-label={`Название модели ${i+2}`} value={m.label} maxLength={100} required disabled={busy} onChange={e=>entry('models',i,'label',e.target.value)}/></label><label>Код модели<input aria-label={`Код модели ${i+2}`} value={m.model} maxLength={150} required disabled={busy} onChange={e=>entry('models',i,'model',e.target.value)}/></label><ReasoningModes value={m} label={`Проверенные режимы модели ${i+2}`} disabled={busy} onChange={modes=>{entry('models',i,'reasoning_efforts',modes);entry('models',i,'reasoning_supported',modes.length>0);}}/><button type="button" className="catalog-remove" disabled={busy} onClick={()=>remove('models',i)}><Trash2 size={14}/>Удалить модель</button></fieldset>)}
    <button type="button" className="catalog-add" disabled={busy||draft.models.length>=20} onClick={()=>field('models',[...draft.models,{id:'',label:'',model:'',reasoning_supported:false,reasoning_efforts:[]}])}><Plus size={14}/>Добавить модель</button>
    <small>Подтверждайте поддержку рассуждений только для проверенной модели. Используемые элементы списка нельзя удалить, пока они выбраны в разговорах.</small><button className="primary" disabled={busy}>Сохранить общую конфигурацию</button>
   </form>
   <section className="configuration-version" aria-label="Текущая версия конфигурации">
    <div><h3>Текущая версия конфигурации</h3><p>Версия {revision} · {activeVersion}</p>
     <p>Загрузка заменит черновик сохранёнными настройками сервера.</p></div>
    <button type="button" disabled={busy} onClick={()=>{setError('');load().catch(e=>setError(e.message));}}>Загрузить актуальную версию</button>
   </section>{admin&&limits&&<p className="operating-limits">Лимиты API: {limits.daily_limit_rub} ₽ / день, {limits.monthly_limit_rub} ₽ / месяц. Лимиты расходов меняются во вкладке «Бюджет». Ключи остаются в защищённой конфигурации сервера.</p>}
  </>}
  {tab==='operations'&&admin&&<OperationsPanel api={api}/>}
  {tab==='budget'&&admin&&<BudgetPanel api={api}/>}
  {tab==='rag'&&admin&&<RagSettings api={api}/>}
  {tab==='history'&&<div className="audit-list"><p>Загрузка старой версии создаёт черновик. Изменения вступят в силу только после сохранения общей конфигурации.</p>{history.map(item=><article key={item.revision}><strong>Версия {item.revision}</strong><span>{formatDateTime(item.created*1000)}</span><button type="button" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const old=await api(`/editor/config/versions/${item.revision}`);setDraft(old.config);setTab('defaults');setNotice('Версия загружена в черновик. Сохраните, чтобы применить.');}catch(e){setError(e.message);}finally{setBusy(false);}}}>Загрузить как черновик</button></article>)}</div>}
  {tab==='audit'&&<div className="audit-list">{audit ? audit.length ? audit.map(event=><article key={event.id}>
    <strong>{event.action}</strong><span>{formatDateTime(event.created*1000)} · {event.target}</span><code>{event.details}</code>
  </article>) : <p>Изменений пока нет.</p> : <p role="status">Загружаем журнал…</p>}</div>}
  </PanelTabs>
 </section></div>;
}
