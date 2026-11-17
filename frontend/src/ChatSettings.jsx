import React from 'react';

export const DEFAULT_CHAT_SETTINGS = {
  communication_mode: 'text', review_before_send: false, live_pause:'normal',
  streaming_enabled: true, auto_speech: false,
  model_id: 'default', prompt_id: 'default', rag_enabled: false,
  web_search_enabled: false, knowledge_base_id: null, voice: null, reasoning_effort: 'default',
};

export function communicationMode(value) {
  return value.communication_mode || (value.streaming_enabled && value.auto_speech ? 'voice' : 'text');
}

export function ChatSettings({config, value, disabled, modeDisabled=disabled, onChange}) {
  if (!config) return null;
  const selected = config.knowledge_bases?.find(base => base.id === value.knowledge_base_id);
  return <section className="chat-settings composer-sources" aria-label="Источники разговора">

    <div className="source-options">
      <label title={config.capabilities?.web_search ? 'Использовать результаты поиска в интернете' : 'Поиск в интернете не настроен'}><input type="checkbox" role="switch" aria-label="Поиск в интернете" checked={value.web_search_enabled} disabled={disabled || !config.capabilities?.web_search} onChange={event => onChange('web_search_enabled',event.target.checked)}/> Интернет</label>
      <label title={!selected?.ready ? 'Выберите готовую базу знаний' : 'Использовать материалы выбранной базы'}><input type="checkbox" role="switch" aria-label="RAG" checked={value.rag_enabled} disabled={disabled || !config.capabilities?.rag || (!value.rag_enabled && !selected?.ready)} onChange={event => onChange('rag_enabled', event.target.checked)}/> RAG</label>
    </div>
    <label className="base-option">База знаний
      <select aria-label="База знаний" disabled={disabled || !config.capabilities?.rag} value={value.knowledge_base_id || ''} onChange={event => onChange('knowledge_base_id', event.target.value || null)}>
        <option value="">Выберите базу знаний</option>
        {value.knowledge_base_id && !selected && <option value={value.knowledge_base_id}>База недоступна</option>}
        {(config.knowledge_bases || []).map(base => <option key={base.id} value={base.id}>{base.name}{!base.ready ? ' · не готова' : ''}</option>)}
      </select>
    </label>
    <div className="communication-options">
      <label>Режим общения
        <select aria-label="Режим общения" value={communicationMode(value)} disabled={modeDisabled} onChange={event => onChange('communication_mode', event.target.value)}>
          <option value="text">Текстовый</option><option value="voice">Голосовой</option><option value="live">Живой диалог</option>
        </select>
      </label>
      <span className="mode-description">{communicationMode(value) === 'live' ? 'Включите прослушивание. Пауза завершает вопрос; после ответа микрофон включится снова.' : communicationMode(value) === 'voice' ? (value.review_before_send ? 'Запишите вопрос → проверьте текст → отправьте. Ответ прозвучит автоматически.' : 'Запишите вопрос → остановите запись. Вопрос отправится, ответ прозвучит автоматически.') : 'Отправляйте сообщения вручную. Для звука нажмите «Озвучить».'}</span>
      <details className="delivery-details"><summary>Дополнительные настройки</summary>
        {communicationMode(value) === 'live' && <label className="live-pause-setting">Пауза перед отправкой<select aria-label="Пауза перед отправкой" disabled={disabled} value={value.live_pause || 'normal'} onChange={event=>onChange('live_pause',event.target.value)}><option value="normal">Обычная · 1 секунда</option><option value="long">Длинная · 1,6 секунды</option></select><small>Пауза может быть принята за конец вопроса. Для неспешной речи выберите длинную.</small></label>}
        <div className="source-options">
          <label><input type="checkbox" role="switch" aria-label="Проверять текст перед отправкой" checked={!!value.review_before_send} disabled={disabled || communicationMode(value) !== 'voice'} onChange={event => onChange('review_before_send', event.target.checked)}/> Проверять текст перед отправкой</label>
          <label title="Классическая выдача доступна в текстовом режиме"><input type="checkbox" role="switch" aria-label="Классический ответ" checked={!value.streaming_enabled} disabled={disabled || communicationMode(value) !== 'text'} onChange={event => onChange('streaming_enabled', !event.target.checked)}/> Классический ответ целиком</label>
        </div>
      </details>
    </div>
  </section>;
}
