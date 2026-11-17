import React from 'react';
import {X} from 'lucide-react';
import {useDialog} from './useDialog';
import manual from './user-manual-ru.html?raw';

export function UserManual({onClose}) {
  const dialog = useDialog(onClose);
  function navigate(event) {
    const link = event.target.closest('a[href^="#manual-"]');
    if (!link) return;
    const target = dialog.current?.querySelector(link.getAttribute('href'));
    if (target) {
      event.preventDefault();
      target.scrollIntoView({block:'start'});
      const heading = target.querySelector('h3');
      heading?.focus({preventScroll:true});
    }
  }
  return <div className="modal-backdrop"><section ref={dialog} className="modal manual-modal" role="dialog" aria-modal="true" aria-labelledby="user-manual-title">
    <div className="manual-heading"><div><span className="eyebrow">СПРАВКА AI TALKS</span><h2 id="user-manual-title">Руководство пользователя</h2><p>Чат, источники и общение голосом — в одном месте.</p></div><button type="button" className="icon-button" aria-label="Закрыть руководство" onClick={onClose}><X size={20}/></button></div>
    {/* Trusted repository-owned documentation, never model/user-provided HTML. */}
    <div className="manual-content" onClick={navigate} dangerouslySetInnerHTML={{__html:manual}}/>
  </section></div>;
}
