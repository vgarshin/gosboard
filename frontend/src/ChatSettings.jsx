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

export function ChatSettings({value, modeDisabled, onChange}) {
  return <div className="composer-mode">
    <label><span className="composer-mode-label">Режим общения</span>
      <select aria-label="Режим общения" title="Режим общения" value={communicationMode(value)} disabled={modeDisabled} onChange={event => onChange(event.target.value)}>
        <option value="text">Текстовый</option>
        <option value="voice">Голосовой</option>
        <option value="live">Живой диалог</option>
      </select>
    </label>
  </div>;
}
