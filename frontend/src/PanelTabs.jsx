import React, {useId} from 'react';

export function PanelTabs({label, tabs, active, onChange, panelClassName, children}) {
  const id = useId();
  function move(event, index) {
    const last = tabs.length - 1;
    const target = {ArrowLeft:(index + last) % tabs.length, ArrowRight:(index + 1) % tabs.length,
      Home:0, End:last}[event.key];
    if (target === undefined) return;
    event.preventDefault();
    onChange(tabs[target][0]);
    event.currentTarget.parentElement.children[target].focus();
  }
  return <>
    <div className="panel-tabs" role="tablist" aria-label={label}>
      {tabs.map(([key, title], index) => <button key={key} type="button" role="tab" id={id + key}
        aria-selected={key === active} aria-controls={id + 'panel'} tabIndex={key === active ? 0 : -1}
        onClick={() => onChange(key)} onKeyDown={event => move(event, index)}>{title}</button>)}
    </div>
    <div className={panelClassName} role="tabpanel" id={id + 'panel'} aria-labelledby={id + active}>{children}</div>
  </>;
}
