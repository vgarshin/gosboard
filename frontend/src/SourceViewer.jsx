import React, {useEffect, useState, useRef} from 'react';
import {ArrowLeft, FileText, X} from 'lucide-react';

export function SourceViewer({chunkId, embedded = false, kind = 'rag', initialSource = null}) {
  const [source, setSource] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setSource(null); setError('');
    if(initialSource){setSource(initialSource);return ()=>{live=false;};}
    fetch(`/api/${kind==='web'?'web-sources':'sources'}/${encodeURIComponent(chunkId)}`).then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || 'Источник недоступен');
      if (live) setSource(body);
    }).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [chunkId, kind, initialSource]);
  return <div className={embedded ? "source-page source-embedded" : "source-page"}>{!embedded && <a href="/" className="source-back"><ArrowLeft size={16}/>К разговорам</a>}{error ? <div className="error" role="alert">{error}</div> : source ? <article><span className="eyebrow">{kind==='web'?(source.content_kind==='page_excerpt'?'ИНТЕРНЕТ · ТЕКСТ СТРАНИЦЫ':'ИНТЕРНЕТ · ПОИСКОВЫЙ ФРАГМЕНТ'):`ИСТОЧНИК · ${source.base_name}`}</span><h1><FileText size={25}/>{source.title}</h1>
    {kind==='web'?<><p className="source-location">Найдено: {new Date(source.fetched_at*1000).toLocaleString()}</p><p className="source-query">Запрос: {source.query}</p>{source.content_kind==='page_excerpt'?<><p className="source-location">Страница прочитана: {new Date(source.page_fetched_at*1000).toLocaleString()}{source.page_cached?' · из кеша':''}</p><p className="source-note">Показан выбранный фрагмент текста страницы, использованный для ответа.</p></>:<p className="source-note">{source.page_status&&source.page_status!=='not_requested'?'Текст страницы недоступен; использован фрагмент поисковой выдачи.':'Показан фрагмент поисковой выдачи. Полная страница не загружалась.'}</p>}</>:<p className="source-location">{source.page ? `Страница ${source.page}` : 'Без нумерации страниц'} · фрагмент {chunkId.slice(0, 8)}</p>}
    <mark className="source-excerpt">{kind==='web'?source.excerpt:source.text}</mark>
    {kind==='web'?<><a className="primary" href={source.url} target="_blank" rel="noopener noreferrer">Открыть страницу</a><p className="source-url">{source.url}</p></>:<a className="primary" download href={source.document_url}>Скачать оригинал{source.page ? ` · цитата на стр. ${source.page}` : ''}</a>}
  </article> : <p>Открываем источник…</p>}</div>;
}

export function MessageText({message, onSource}) {
  const sources = new Map((message.metadata.citations || []).map(source => [source.id, source]));
  return message.text.split(/(\[[DW]\d+\])/g).map((part, i) => {
    const source = sources.get(part.slice(1, -1));
    return source ? <a className="citation-marker" key={i} href={source.url} title={`${source.type==='web'?'Интернет · ':''}${source.title}${source.page ? ', стр. ' + source.page : ''}`} target={source.type==='web'?'_blank':undefined} rel="noopener noreferrer" onClick={event => openSource(event, source, onSource)}>{part}</a> : <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

export function CitationList({sources, onSource}) {
  if (!sources?.length) return null;
  return <details className="citation-list"><summary>Источники · {sources.length}</summary>{sources.map(source => <div key={source.id}><a href={source.url} onClick={event => openSource(event, source, onSource)}>[{source.id}] {source.type==='web'?(source.content_kind==='page_excerpt'?'Интернет · страница · ':'Интернет · поисковый фрагмент · '):''}{source.title}{source.page ? ` · стр. ${source.page}` : ''}</a><p>{source.excerpt.slice(0, 200)}{source.excerpt.length > 200 ? '…' : ''}</p></div>)}</details>;
}


function openSource(event, source, onSource) {
  if (onSource && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    event.preventDefault(); onSource(source.type==='web'?{id:source.source_id,type:'web'}:source.chunk_id);
  }
}

export function SourceModal({chunkId, onClose}) {
  const reference = typeof chunkId==='string'?{id:chunkId,type:'rag'}:chunkId;
  const dialog = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.querySelector('button')?.focus();
    function keyboard(event) {
      if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); onClose();}
      if (event.key !== 'Tab') return;
      const elements = [...dialog.current.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]')].filter(element => !element.disabled);
      if (!elements.length) {event.preventDefault(); return;}
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialog.current.contains(document.activeElement))) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && (document.activeElement === last || !dialog.current.contains(document.activeElement))) {event.preventDefault(); first.focus();}
    }
    document.addEventListener('keydown', keyboard, true);
    return () => {document.removeEventListener('keydown', keyboard, true); if (previous?.isConnected) previous.focus();};
  }, [chunkId]);
  return <div className="modal-backdrop source-backdrop" onClick={event => {if (event.target === event.currentTarget) onClose();}}>
    <section ref={dialog} className="modal source-modal" role="dialog" aria-modal="true" aria-label="Источник ответа">
      <button type="button" className="icon-button modal-close" aria-label="Закрыть источник" onClick={onClose}><X size={20}/></button>
      <SourceViewer chunkId={reference.id} kind={reference.type} initialSource={reference.source} embedded/>
    </section>
  </div>;
}
