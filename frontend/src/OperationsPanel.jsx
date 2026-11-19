import {RefreshCw} from 'lucide-react';
import {formatDateTime} from './dateTime';
import React, {useEffect,useState} from 'react';
export function OperationsPanel({api}) {
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function refresh(){setBusy(true);setError('');try{setData(await api('/admin/operations'));}catch(e){setError(e.message);}finally{setBusy(false);}}
 useEffect(()=>{refresh();},[]);
 return <section aria-label="Мониторинг"><h3>Мониторинг</h3><p>Операционные счётчики без текстов сообщений и данных пользователей. Обновляются по кнопке; после перезапуска сервера начинаются заново. История бюджета сохраняется отдельно.</p><button type="button" className="refresh-action" disabled={busy} onClick={refresh}><RefreshCw size={16} aria-hidden="true"/>Обновить мониторинг</button>{error&&<p className="error" role="alert">{error}</p>}{data&&<>
 <p>Начало наблюдений: {formatDateTime(data.since*1000)}</p><p>Активные операции API: {data.active_paid_operations}; запросы сообщений: {data.active_message_requests}.</p><p>Резервы сегодня: {data.budget.day.used_rub.toFixed(2)} ₽ / {data.budget.day.limit_rub} ₽; за месяц: {data.budget.month.used_rub.toFixed(2)} ₽ / {data.budget.month.limit_rub} ₽. Это резервы приложения, не счёт провайдера. Изменение лимитов — во вкладке «Бюджет».</p>
 <h4>Локальный кеш</h4><p>Показаны проверки кеша, не число ответов. Принудительная генерация обходит кеш и не считается промахом.</p>{Object.keys(data.caches).length?Object.entries(data.caches).map(([name,row])=><p key={name}><strong>{name}</strong>: попадания {row.hits}, промахи {row.misses}, доля попаданий {Math.round(100*row.hits/(row.hits+row.misses))}%</p>):<p>Проверок кеша пока нет.</p>}
 <h4>Операции и запросы</h4><p>Среднее — за всё время наблюдения; p95 — по последним {data.p95_window} измерениям каждого типа. Время операции API может включать ожидание её внутренней очереди; HTTP-время — обработку запроса до формирования ответа, без загрузки ответа клиентом. HTTP-коды и исходы отмен/ошибок показаны отдельно.</p>
 {data.series.length?data.series.map(row=><article className="catalog-entry" key={row.group+row.name}><strong style={{overflowWrap:'anywhere'}}>{row.group==='http'?'HTTP':row.group==='fetch'?'Страница':'API'} · {row.name}</strong><p>{row.count} вызовов · среднее {row.mean_ms} мс · p95 {row.p95_ms} мс ({row.sample_count} измерений)</p><p>{Object.entries(row.outcomes).map(([name,count])=>`${name}: ${count}`).join(' · ')}</p></article>):<p>Наблюдений пока нет.</p>}
 </>}</section>;
}
