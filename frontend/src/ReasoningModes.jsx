import React from 'react';
export const REASONING_LABELS={none:'Выключены',low:'Низкий',medium:'Средний',high:'Высокий'};
export const canonicalReasoning=value=>({enabled:'medium',disabled:'none'}[value]||value);
export const reasoningModes=value=>value?.reasoning_efforts??(value?.reasoning_supported?['none','medium']:[]);
export function ReasoningModes({value,onChange,label,disabled}) {
 const selected=reasoningModes(value);
 return <fieldset className="catalog-entry"><legend>{label}</legend><small>Отметьте только режимы, подтверждённые для этой модели. Пустой список оставляет управление провайдеру.</small>{Object.entries(REASONING_LABELS).map(([mode,title])=><label className="account-enabled" key={mode}><input type="checkbox" aria-label={`${label}: ${title}`} checked={selected.includes(mode)} disabled={disabled} onChange={e=>onChange(Object.keys(REASONING_LABELS).filter(key=>key===mode?e.target.checked:selected.includes(key)))}/>{title}</label>)}</fieldset>;
}
