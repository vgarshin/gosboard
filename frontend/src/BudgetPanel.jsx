import {RefreshCw} from 'lucide-react';
import {formatDateTime} from './dateTime';
import React, {useEffect, useState} from 'react';

const money=value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value)+' ₽';
const kinds={'query-resolution':'Подготовка поискового вопроса',llm:'LLM',stt:'Распознавание речи',tts:'Озвучивание','web-search':'Поиск в интернете','embedding-doc':'Индексация документов','embedding-query':'Поиск по базе знаний'};
const states={complete:'Завершено',pending:'В работе',uncertain:'Результат неизвестен'};
export function BudgetPanel({api}) {
 const [data,setData]=useState(null),[daily,setDaily]=useState(''),[monthly,setMonthly]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 function apply(value){setData(value);setDaily(String(value.day.limit_rub));setMonthly(String(value.month.limit_rub));}
 async function refresh(){setBusy(true);setError('');setNotice('');try{apply(await api('/admin/budget'));}catch(e){setError(e.message);}finally{setBusy(false);}}
 useEffect(()=>{refresh();},[]);
 async function save(e){e.preventDefault();setBusy(true);setError('');setNotice('');try{apply(await api('/admin/budget',{method:'PUT',body:JSON.stringify({expected_revision:data.revision,daily_limit_rub:Number(daily),monthly_limit_rub:Number(monthly)})}));setNotice('Лимиты сохранены и действуют для следующих платных операций.');}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <section aria-label="Бюджет"><h3>Бюджет</h3><p>Общий бюджет всех пользователей. Здесь показаны консервативные резервы приложения, а не фактический счёт провайдера. Кешированные результаты не увеличивают расход; ошибки и отмены сохраняют резерв.</p>
 {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <button type="button" className="refresh-action" disabled={busy} onClick={refresh}><RefreshCw size={16} aria-hidden="true"/>Обновить бюджет</button>
 {data&&<><div className="configuration-grid">{[['day','Сегодня'],['month','Этот месяц']].map(([key,label])=><article key={key}><h4>{label}</h4><p>Зарезервировано: <strong>{money(data[key].used_rub)}</strong> / {money(data[key].limit_rub)}</p><p>Осталось: {money(data[key].remaining_rub)}</p><p>Сброс: {formatDateTime(data[key].resets_at)} (время устройства)</p>{data[key].used_rub>=data[key].limit_rub&&<p role="status">Лимит достигнут. Новые платные операции заблокированы.</p>}</article>)}</div>
 <p>Периоды считаются по UTC: день начинается в 00:00 UTC (03:00 по Москве). Запрос блокируется, если его резерв превышает остаток хотя бы одного лимита.</p>
 <form className="management-form" onSubmit={save}><label>Дневной лимит, ₽<input aria-label="Дневной лимит" type="number" min="0.01" max="1000000000" step="0.01" required disabled={busy} value={daily} onChange={e=>setDaily(e.target.value)}/></label><label>Месячный лимит, ₽<input aria-label="Месячный лимит" type="number" min="0.01" max="1000000000" step="0.01" required disabled={busy} value={monthly} onChange={e=>setMonthly(e.target.value)}/></label><small>Изменения применяются без перезапуска и сохраняются после него. Снижение ниже уже использованной суммы блокирует следующие операции; история расходов не обнуляется. Уже начатые операции продолжаются.</small><button className="primary" disabled={busy}>Сохранить бюджет</button></form>
 <h4>Резервы на операцию</h4><p>LLM: {money(data.allowances_rub.llm)}; подготовка поискового вопроса при наличии истории: {money(data.allowances_rub.llm)} при промахе кеша; STT: {money(data.allowances_rub.stt)}; TTS: {money(data.allowances_rub.tts_per_segment)} за сегмент до 240 символов; интернет: {money(data.allowances_rub.web_search)}; embeddings: {money(data.allowances_rub.embedding)} за вызов.</p>
 {['day','month'].map(key=><div key={key}><h4>{key==='day'?'Расходы сегодня':'Расходы за месяц'}</h4><div className="budget-rows">{data[key].rows.length?data[key].rows.map(row=><p key={row.kind+row.state}>{kinds[row.kind]||row.kind} · {states[row.state]||row.state} · {row.calls} вызовов: <strong>{money(row.reserved_rub)}</strong></p>):<p>Платных операций не было.</p>}</div></div>)}
 </>}
 </section>;
}
