import React, {useRef} from 'react';
import {Upload} from 'lucide-react';

export function FilePicker({file, accept, label, disabled, onSelect}) {
  const input = useRef(null);
  return <div className="file-picker">
    <input ref={input} type="file" hidden aria-label={label} accept={accept} disabled={disabled} onChange={event => {
      const selected = event.target.files[0];
      if (selected) onSelect(selected);
      event.target.value = '';
    }}/>
    <button type="button" className="primary" disabled={disabled} onClick={() => input.current?.click()}>
      <Upload size={16} aria-hidden="true"/>{label}
    </button>
    <span role="status">{file?.name || 'Файл не выбран'}</span>
  </div>;
}
