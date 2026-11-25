import React, {useState} from 'react';
import {X} from 'lucide-react';
import {PanelTabs} from './PanelTabs';
import {useDialog} from './useDialog';
import manual from './user-manual-ru.html?raw';

const titles = new Map([
  ['user', 'Руководство пользователя'],
  ['editor', 'Руководство редактора'],
  ['admin', 'Руководство администратора'],
]);

// The HTML is the trusted, repository-owned manual.
const manualDocument = new DOMParser().parseFromString(manual, 'text/html');
const sections = Array.from(manualDocument.querySelectorAll('.manual-toc a'), link => {
  const id = link.hash.slice(1);
  return {
    id,
    label: link.textContent,
    roles: link.dataset.roles?.split(' '),
    html: manualDocument.getElementById(id).outerHTML,
  };
});

export function UserManual({role, onClose, onHistory}) {
  const dialog = useDialog(onClose);
  const currentRole = titles.has(role) ? role : 'user';
  const visible = sections.filter(item => !item.roles || item.roles.includes(currentRole));
  const [selected, setSelected] = useState(visible[0].id);
  const section = visible.find(item => item.id === selected) || visible[0];
  const tabs = visible.map(({id, label}) => [id, label]);
  return <div className="modal-backdrop"><section ref={dialog} className="modal manual-modal" role="dialog" aria-modal="true" aria-labelledby="user-manual-title">
    <div className="manual-heading"><h2 id="user-manual-title">{titles.get(currentRole)}</h2><button type="button" className="icon-button" aria-label="Закрыть руководство" onClick={onClose}><X size={20}/></button></div>
    <PanelTabs label="Разделы инструкции" tabs={tabs} active={section.id} onChange={setSelected} panelClassName="manual-scroll">
      {onHistory && section.id === 'manual-chat' && <button className="manual-history" onClick={onHistory}>Архивная история</button>}
      <div key={section.id} className="manual-content" dangerouslySetInnerHTML={{__html:section.html}}/>
    </PanelTabs>
  </section></div>;
}
