import React from 'react';
import {Mic,Pause,Square} from 'lucide-react';
const LABELS={off:'Готов к началу',connecting:'Подключаю микрофон',listening:'Слушаю — задайте вопрос',capturing:'Записываю вопрос',recognizing:'Распознаю речь',generating:'Готовлю ответ',speaking:'Озвучиваю ответ',cooldown:'Готовлюсь слушать',paused:'Пауза'};
export function LiveDialogControls({state,disabled,onStart,onPause,onStop}) {
 const microphone=['listening','capturing'].includes(state.phase);
 return <section className={'live-dialog-controls '+(state.active?'is-active':'')} aria-label="Управление живым диалогом">
  <div><strong role="status">{LABELS[state.phase]||'Пауза'}</strong><span>{microphone?'Микрофон включён':'Микрофон выключен'}</span>{state.reason&&<p>{state.reason}</p>}</div>
  <div className="live-dialog-actions">{state.active?<button type="button" onClick={onPause}><Pause size={15}/>Пауза</button>:<button type="button" disabled={disabled} onClick={onStart}><Mic size={15}/>{state.enabled?'Продолжить':'Начать живой диалог'}</button>}{state.enabled&&<button type="button" onClick={onStop}><Square size={14}/>Завершить диалог</button>}</div>
 </section>;
}
