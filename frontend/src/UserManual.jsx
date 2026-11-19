import React, {useState} from 'react';
import {X} from 'lucide-react';
import {PanelTabs} from './PanelTabs';
import {useDialog} from './useDialog';
import manual from './user-manual-ru.html?raw';

// The HTML is the trusted, repository-owned manual.
const manualDocument = new DOMParser().parseFromString(manual, 'text/html');
const sections = Array.from(manualDocument.querySelectorAll('.manual-toc a'), link => ({
  id: link.hash.slice(1),
  label: link.textContent,
  html: manualDocument.getElementById(link.hash.slice(1)).outerHTML,
}));
const tabs = sections.map(({id, label}) => [id, label]);

export function UserManual({onClose,onHistory}) {
  const dialog = useDialog(onClose);
  const [section,setSection] = useState(tabs[0][0]);
  return <div className="modal-backdrop"><section ref={dialog} className="modal manual-modal" role="dialog" aria-modal="true" aria-labelledby="user-manual-title">
    <div className="manual-heading"><h2 id="user-manual-title">Руководство пользователя</h2><button type="button" className="icon-button" aria-label="Закрыть руководство" onClick={onClose}><X size={20}/></button></div>
    <PanelTabs label="Разделы инструкции" tabs={tabs} active={section} onChange={setSection} panelClassName="manual-scroll">
      {onHistory && section === 'manual-chat' && <button className="manual-history" onClick={onHistory}>Архивная история</button>}
      <div key={section} className="manual-content" dangerouslySetInnerHTML={{__html:sections.find(item => item.id === section).html}}/>
    </PanelTabs>
  </section></div>;
}
