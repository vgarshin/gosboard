import {useEffect, useRef} from 'react';
export function useDialog(onClose) {
 const ref=useRef(null);
 useEffect(()=>{
  const previous=document.activeElement;ref.current?.querySelector('button')?.focus();
  function keyboard(e){
   const dialogs=document.querySelectorAll('[role="dialog"][aria-modal="true"]');
   if(dialogs[dialogs.length-1]!==ref.current)return;
   if(e.key==='Escape'){e.preventDefault();e.stopPropagation();onClose();return;}
   if(e.key!=='Tab')return;
   const nodes=[...ref.current.querySelectorAll('button,input,select,textarea,a[href]')].filter(node=>!node.disabled&&node.offsetParent!==null);
   const first=nodes[0],last=nodes[nodes.length-1];
   if(e.shiftKey&&(document.activeElement===first||!nodes.includes(document.activeElement))){e.preventDefault();last?.focus();}
   else if(!e.shiftKey&&(document.activeElement===last||!nodes.includes(document.activeElement))){e.preventDefault();first?.focus();}
  }
  document.addEventListener('keydown',keyboard,true);
  return()=>{document.removeEventListener('keydown',keyboard,true);if(previous?.isConnected)previous.focus();};
 },[]);
 return ref;
}
