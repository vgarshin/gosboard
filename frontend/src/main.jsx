import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {CircleHelp, Download, ArrowUp, AudioLines, BookOpen, Settings, Users, Check, ChevronRight, Copy, Headphones, LogOut, Menu, MessageSquare, Mic, Plus, ShieldCheck, Square, Trash2, X} from 'lucide-react';
import {ExportChat} from './ExportChat';
import {UserManual} from './UserManual';
import {startCapture} from './audio';
import {ConversationController} from './conversation';
import {LiveDialogControls} from './LiveDialogControls';
import {readReplyStream, StreamAudioPlayer} from './streaming';
import {ChatSettings, DEFAULT_CHAT_SETTINGS, communicationMode} from './ChatSettings';
import {ConfigurationPanel} from './ConfigurationPanel';
import {ManagementPanel} from './ManagementPanel';
import {KnowledgeManager} from './KnowledgeManager';
import {SourceViewer, SourceModal, MessageText, CitationList} from './SourceViewer';
import './style.css';

function App() {
  const [bootstrap, setBootstrap] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [user, setUser] = useState(null);
  const [config, setConfig] = useState(null);
  const [chatOptions, setChatOptions] = useState(DEFAULT_CHAT_SETTINGS);
  const [chats, setChats] = useState([]);
  const [active, setActive] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [recording, setRecording] = useState(false);
  const [playing, setPlaying] = useState(null);
  const [copied, setCopied] = useState(null);
  const [menu, setMenu] = useState(false);
  const [configurationOpen, setConfigurationOpen] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [sourceChunk, setSourceChunk] = useState(null);
  const [manualOpen,setManualOpen] = useState(false);
  const [exportOpen,setExportOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [liveState,setLiveState] = useState({enabled:false,active:false,phase:'off',reason:''});
  const conversation = useRef(null);
  if(!conversation.current) conversation.current = new ConversationController(setLiveState);
  const liveSetup = useRef(null);
  const sttController = useRef(null);
  const sttRequest = useRef(null);
  const audio = useRef(null);
  const scroll = useRef(null);
  const activeRef = useRef(null);
  const pendingRequest = useRef(null);
  const csrf = useRef('');
  const streamPlayer = useRef(null);
  const streamController = useRef(null);
  const streamRequest = useRef(null);
  const [streamDraft, setStreamDraft] = useState(null);
  const [streamSound, setStreamSound] = useState({playing:false, needsGesture:false});

  async function api(path, options = {}) {
    const headers = {...options.headers};
    if (options.body && typeof options.body === 'string') headers['Content-Type'] = 'application/json';
    if (options.method && options.method !== 'GET') {
      headers['X-CSRF-Token'] = csrf.current;
    }
    const response = await fetch('/api' + path, {...options, headers});
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(typeof problem.detail === 'string' ? problem.detail : Array.isArray(problem.detail) ? problem.detail.map(error => `${error.loc.slice(1).join('.')}: ${error.msg}`).join('; ') : `Ошибка запроса (${response.status})`);
    }
    return response.json();
  }

  async function refreshCatalog() {
    const settings = await api('/config'); setConfig(settings);
  }
  async function initialize() {
    try {
      const boot = await api('/bootstrap'); setBootstrap(boot);
      const response = await fetch('/api/me');
      if (response.status === 401) return;
      if (!response.ok) throw new Error('Доступ закрыт. Обратитесь к администратору.');
      const me = await response.json();
      csrf.current = me.csrf; setUser(me);
      const [settings, list] = await Promise.all([api('/config'), api('/chats')]);
      setConfig(settings); setChats(list);
      if (list.length) await select(list[0].id, list[0]);
    } catch (e) { setError(e.message); }
    finally { setInitializing(false); }
  }
  useEffect(() => { initialize(); return () => { audio.current?.pause(); streamPlayer.current?.stop(); streamController.current?.abort(); liveSetup.current?.abort(); sttController.current?.abort(); conversation.current.pause('',true); }; }, []);
  useEffect(() => {
    if (!user) return;
    const timer = setInterval(() => refreshCatalog().catch(() => {}), 10000);
    return () => clearInterval(timer);
  }, [user?.id, config?.capabilities?.rag]);
  useEffect(() => {
    if (!user || busy || recording) return;
    const timer = setInterval(async () => {
      try {
        const list = await api('/chats'); setChats(list);
        const selected = list.find(chat => chat.id === active);
        if (selected) setChatOptions(selected.settings);
      } catch (e) { setError(e.message); }
    }, 10000);
    return () => clearInterval(timer);
  }, [user?.id, active, busy, recording]);
  useEffect(() => { scroll.current?.scrollIntoView({behavior: 'smooth'}); }, [messages, busy, streamDraft]);
  useEffect(() => {
    if (!recording) return;
    setSeconds(0);
    const interval = setInterval(() => setSeconds(v => v + 1), 1000);
    return () => clearInterval(interval);
  }, [recording]);

  async function select(id, selectedChat) {
    if(conversation.current.enabled) pauseLive('',true);
    else conversation.current.invalidate();
    stopAudio(); setError(''); setMenu(false); setConfirmDelete(false);
    setBusy('Открываем разговор');
    activeRef.current = id; setActive(id); setDraft('');
    setChatOptions(selectedChat?.settings || DEFAULT_CHAT_SETTINGS);
    try {
      const result = await api(`/chats/${id}/messages`);
      if (activeRef.current === id) setMessages(result);
    } finally { setBusy(''); }
  }
  async function changeSettings(key, value) {
    if ((busy || recording) && !(key==='communication_mode' && conversation.current.enabled)) return;
    if(key==='communication_mode' && conversation.current.enabled) pauseLive('',true);
    const mode = key === 'communication_mode' ? value : communicationMode(chatOptions);
    const next = {...chatOptions, [key]: value, communication_mode:mode, auto_speech:mode !== 'text'};
    if (key === 'communication_mode' || mode !== 'text') next.streaming_enabled = true;
    if (key === 'model_id') next.reasoning_effort = 'default';
    if (key === 'knowledge_base_id' && !config.knowledge_bases?.find(base => base.id === value)?.ready) next.rag_enabled = false;
    if (key === 'rag_enabled' && !value && !config.knowledge_bases?.some(base => base.id === next.knowledge_base_id)) next.knowledge_base_id = null;
    if (!active) { setChatOptions(next); pendingRequest.current = null; return; }
    const previous = chatOptions;
    setChatOptions(next);
    setBusy('Сохраняем настройки'); setError('');
    try {
      const updated = await api(`/chats/${active}/sources`, {method: 'PATCH', body: JSON.stringify({rag_enabled:next.rag_enabled, web_search_enabled:next.web_search_enabled, knowledge_base_id:next.knowledge_base_id, streaming_enabled:next.streaming_enabled, auto_speech:next.auto_speech, communication_mode:next.communication_mode, review_before_send:next.review_before_send, live_pause:next.live_pause})});
      setChatOptions(updated.settings);
      pendingRequest.current = null;
      setChats(list => list.map(chat => chat.id === active ? updated : chat));
    } catch (e) { setChatOptions(previous); setError(e.message); }
    finally { setBusy(''); }
  }
  async function newChat() {
    if (busy || recording) return;
    if(conversation.current.enabled) pauseLive('',true);
    setBusy('Создаём разговор');
    try {
      const chat = await api('/chats', {method: 'POST', body:JSON.stringify(DEFAULT_CHAT_SETTINGS)});
      setChats(list => [chat, ...list]); await select(chat.id, chat);
    } catch (e) { setError(e.message); }
    finally { setBusy(''); }
  }
  async function send(event, regenerate = false, submission = null) {
    event?.preventDefault();
    if (!submission && (busy || recording || conversation.current.active)) return;
    const text = (submission?.text ?? draft).trim();
    if (!text) return;
    const options = submission?.options || chatOptions;
    const token = submission?.token ?? conversation.current.epoch;
    const current = () => conversation.current.valid(token);
    const live = !!submission?.live;
    if(!current())return false;
    if(live)conversation.current.transition('generating',token);
    setError(''); setBusy(options.web_search_enabled?'Ищем источники и готовим ответ':'Модель готовит ответ');
    if (!submission?.preparedAudio) { stopAudio(); if (options.streaming_enabled && options.auto_speech) prepareSpeech(); }
    let textCompleted = false;
    try {
      let id = active;
      if (!id) {
        const chat = await api('/chats', {method: 'POST', body: JSON.stringify(options)});
        if(!current())return false;
        id = chat.id; setActive(id); activeRef.current = id;
        setChats(list => [chat, ...list]);
      }
      // Reuse the request ID after transport failure; explicit new text gets a new ID.
      const fingerprint = JSON.stringify([id, text, regenerate, options]);
      const requestId = pendingRequest.current?.fingerprint === fingerprint ? pendingRequest.current.id : crypto.randomUUID();
      pendingRequest.current = {fingerprint, id: requestId};
      const body = JSON.stringify({text, request_id: requestId, regenerate});
      if (options.streaming_enabled) {
        streamRequest.current = requestId;
        streamController.current = new AbortController();
        setStreamDraft({question:text, text:'', provisional:false});
        const response = await fetch(`/api/chats/${id}/messages/stream`, {method:'POST',
          headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf.current}, body,
          signal:streamController.current.signal});
        await readReplyStream(response, event => {
          if(!current())return;
          if (event.event === 'status') setBusy(event.text);
          if (event.event === 'delta') {
            setBusy(event.provisional ? 'Формирую ответ · проверка ссылок впереди' : 'Формирую ответ');
            setStreamDraft(previous => ({...previous, text:previous.text + event.text, provisional:event.provisional}));
          }
          if (event.event === 'answer') setStreamDraft(previous => ({...previous, text:event.text, provisional:false, metadata:event.metadata, finalized:true}));
          if (event.event === 'text_done') { textCompleted = true; setDraft(''); setBusy('Готовлю озвучивание'); }
          if (event.event === 'audio') streamPlayer.current?.enqueue(event.url);
          if (event.event === 'audio_error') {
            setError(event.detail);
            if(live)pauseLive(event.detail);
          }
        });
      } else {
        await api(`/chats/${id}/messages`, {method:'POST', body});
      }
      if(!current())return false;
      pendingRequest.current = null;
      setDraft('');
      const [items, list] = await Promise.all([api(`/chats/${id}/messages`), api('/chats')]);
      if(!current())return false;
      setMessages(items); setChats(list);
      if(live){
        const player=streamPlayer.current;
        if(!player)throw new Error('Звук недоступен. Нажмите «Продолжить», чтобы возобновить диалог.');
        await player.waitForIdle(current);
        if(current())conversation.current.next(token);
      }
      return true;
    } catch (e) {
      if(!current())return false;
      if (!e.retryable && !e.message.startsWith('Failed to fetch')) pendingRequest.current = null;
      stopStreamSound();
      setError((e.name === 'AbortError' ? 'Ответ остановлен.' : e.message) + (textCompleted ? ' Текстовый ответ сохранён.' : ' Текст сохранён в поле ввода. При обрыве связи проверьте историю.'));
      if(live)pauseLive(e.message);
      if (activeRef.current && current()) {
        try { const saved=await api(`/chats/${activeRef.current}/messages`);if(current())setMessages(saved); } catch {}
      }
      return false;
    } finally { if(current()){setBusy(''); setStreamDraft(null); streamController.current = null; streamRequest.current = null;} }
  }
  async function cancelGeneration() {
    if(conversation.current.enabled){pauseLive('Ответ остановлен. Проверьте историю перед повтором.');return;}
    if (!pendingRequest.current) return;
    stopStreamSound();
    try {
      const result = await api(`/requests/${pendingRequest.current.id}/cancel`, {method: 'POST'});
      if (!result.cancel_requested) streamController.current?.abort();
    }
    catch (e) { setError(e.message); }
  }
  function prepareSpeech() {
    try { streamPlayer.current = new StreamAudioPlayer(state=>{
      setStreamSound(state);
      if(conversation.current.active && state.needsGesture)pauseLive('Браузер заблокировал звук. Нажмите «Продолжить», чтобы включить его.');
      else if(conversation.current.active && state.playing)conversation.current.transition('speaking');
    }, message => {
      setError(message);
      if(conversation.current.enabled)pauseLive(message);
      else stopStreamSound();
    }); } catch { setError('Автоматическое озвучивание недоступно в этом браузере.'); }
  }
  async function stopRecording(discard = false, atLimit = false) {
    if (!conversation.current.capture) return;
    const stop = conversation.current.takeCapture();
    setRecording(false); setBusy(discard ? 'Отменяем запись' : 'Распознаём речь');
    const options = {...chatOptions};
    const token=conversation.current.epoch;
    const abort=new AbortController();sttController.current=abort;
    const rid=crypto.randomUUID();sttRequest.current=rid;
    let submitted = false;
    try {
      const pcm = await stop();
      if (discard || !conversation.current.valid(token)) { stopStreamSound(); return; }
      const result = await api('/stt', {method: 'POST', headers: {'Content-Type': 'application/octet-stream', 'X-Request-ID': rid}, body: pcm, signal:abort.signal});
      if(!conversation.current.valid(token))return;
      if (!result.text?.trim()) { setError('Речь не распознана. Запишите вопрос ещё раз.'); stopStreamSound(); return; }
      const text = (draft ? draft + '\n' : '') + result.text.trim();
      setDraft(text);
      if (text.length > 4000) { setError('Сообщение длиннее 4000 символов. Сократите текст перед отправкой.'); stopStreamSound(); return; }
      if (communicationMode(options) === 'voice' && !options.review_before_send && !atLimit) {
        submitted = true;
        await send(null, false, {text, options, preparedAudio:true, token});
      } else {
        stopStreamSound();
        if (atLimit) setError('Достигнут предел записи. Проверьте распознанный текст и отправьте вручную.');
      }
    } catch (e) { if(conversation.current.valid(token)){stopStreamSound(); setError(e.message);} }
    finally { if(sttController.current===abort){sttController.current=null;sttRequest.current=null;}if(!submitted&&conversation.current.valid(token))setBusy(''); }
  }
  async function record() {
    if (recording) return stopRecording();
    if (busy) return;
    setError(''); setBusy('Подключаем микрофон'); stopAudio();
    // Unlock playback during the microphone click, before the async transcription.
    if (communicationMode(chatOptions) === 'voice' && !chatOptions.review_before_send) prepareSpeech();
    try {
      const token=conversation.current.epoch;
      const stop=await startCapture(() => stopRecording(false, true));
      if(await conversation.current.attach(stop,token))setRecording(true);
    } catch (e) { stopStreamSound(); setError(e.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере.' : e.message); }
    finally { setBusy(''); }
  }
  function pauseLive(reason='Пауза',off=false) {
    const wasLive=conversation.current.enabled;
    conversation.current.pause(reason,off);
    liveSetup.current?.abort();
    if(!wasLive)return;
    const token=conversation.current.epoch,id=activeRef.current;
    sttController.current?.abort();
    for(const rid of [sttRequest.current,streamRequest.current])if(rid)api(`/requests/${rid}/cancel`,{method:'POST'}).catch(()=>{});
    streamController.current?.abort();stopAudio();setBusy('');setRecording(false);setStreamDraft(null);
    if(id)api(`/chats/${id}/messages`).then(items=>{if(conversation.current.valid(token)&&activeRef.current===id)setMessages(items);}).catch(()=>{});
  }
  async function listenLive(token) {
    const controller=conversation.current;
    if(!controller.valid(token)||!controller.active)return;
    const setup=new AbortController();liveSetup.current=setup;
    try {
      const {startLiveCapture}=await import('./live_capture');
      if(!controller.valid(token)||!controller.active)return;
      controller.transition('connecting',token);
      const stop=await startLiveCapture({
        signal:setup.signal, audioContext:streamPlayer.current?.context,
        silenceMs:chatOptions.live_pause==='long'?1600:1000,
        isCurrent:()=>controller.valid(token)&&controller.active,
        onSpeech:()=>controller.transition('capturing',token),
        onEnd:pcm=>handleLiveQuestion(pcm,false,token),
        onLimit:pcm=>handleLiveQuestion(pcm,true,token),
        onIdle:()=>{if(controller.valid(token))pauseLive('Минуту не было речи. Нажмите «Продолжить», когда будете готовы.');},
        onError:error=>{if(controller.valid(token))pauseLive(error.message);},
      });
      if(await controller.attach(stop,token,true))controller.transition('listening',token);
    }catch(error){if(controller.valid(token)){setError(error.name==='NotAllowedError'?'Разрешите доступ к микрофону в браузере.':error.message);pauseLive('Не удалось включить микрофон. Проверьте разрешения и нажмите «Продолжить».');}}
    finally{if(liveSetup.current===setup)liveSetup.current=null;}
  }
  async function startLive() {
    if(busy||recording||conversation.current.active)return;
    if(draft.trim()){setError('Отправьте или очистите черновик перед началом живого диалога.');return;}
    stopAudio();setError('');
    const token=conversation.current.start();
    prepareSpeech();
    if(!streamPlayer.current){pauseLive('Автоматическое озвучивание недоступно в этом браузере.');return;}
    await listenLive(token);
  }
  async function handleLiveQuestion(pcm,atLimit,token) {
    const controller=conversation.current;
    if(!controller.valid(token)||!controller.active)return;
    // Stop and release all microphone tracks before any remote operation or playback.
    const stop=controller.takeCapture();if(stop)await stop();
    if(!controller.valid(token))return;
    const reviewReason='Достигнут предел записи. Проверьте текст и отправьте вручную; затем продолжите диалог.';
    if(atLimit)controller.review(reviewReason,token);else controller.transition('recognizing',token);
    setBusy('Распознаём речь');
    const abort=new AbortController();sttController.current=abort;
    const rid=crypto.randomUUID();sttRequest.current=rid;
    try{
      const result=await api('/stt',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Request-ID':rid},body:pcm,signal:abort.signal});
      if(!controller.valid(token))return;
      const text=result.text?.trim();
      if(!text){pauseLive('Речь не распознана. Нажмите «Продолжить», чтобы попробовать снова.');return;}
      setDraft(text);
      if(atLimit){stopStreamSound();setError(reviewReason);setBusy('');return;}
      if(text.length>4000){pauseLive('Сообщение длиннее 4000 символов. Сократите текст перед отправкой.');return;}
      await send(null,false,{text,options:chatOptions,preparedAudio:true,live:true,token});
    }catch(error){if(controller.valid(token)){setError(error.message);pauseLive('Не удалось обработать вопрос. Проверьте текст и соединение перед повтором.');}}
    finally{if(sttController.current===abort){sttController.current=null;sttRequest.current=null;}if(controller.valid(token)&&!controller.active)setBusy('');}
  }
  conversation.current.onListen=listenLive;
  conversation.current.onPause=reason=>pauseLive(reason);
  useEffect(()=>{
    const hidden=()=>{if(document.hidden&&conversation.current.active)pauseLive('Вкладка скрыта. Нажмите «Продолжить», чтобы снова включить микрофон.');};
    const offline=()=>{if(conversation.current.active)pauseLive('Нет соединения. Продолжите диалог после восстановления связи.');};
    const leaving=()=>{if(conversation.current.enabled)pauseLive('',true);};
    document.addEventListener('visibilitychange',hidden);window.addEventListener('offline',offline);window.addEventListener('pagehide',leaving);
    return()=>{document.removeEventListener('visibilitychange',hidden);window.removeEventListener('offline',offline);window.removeEventListener('pagehide',leaving);};
  },[]);
  useEffect(()=>{
    if(conversation.current.enabled&&communicationMode(chatOptions)!=='live')pauseLive('',true);
  },[chatOptions.communication_mode,chatOptions.streaming_enabled,chatOptions.auto_speech]);
  useEffect(()=>{
    if(!liveState.active)return;
    const token=conversation.current.epoch;
    const timer=setInterval(()=>api('/me').catch(()=>{if(conversation.current.valid(token)&&conversation.current.active)pauseLive('Сессия недоступна. Проверьте вход и разрешение на доступ.');}),10000);
    return()=>clearInterval(timer);
  },[liveState.active]);
  function stopStreamSound() {
    streamPlayer.current?.stop(); streamPlayer.current = null;
    if (streamRequest.current) api(`/requests/${streamRequest.current}/audio/stop`, {method:'POST'}).catch(() => {});
  }
  function stopAudio() {
    stopStreamSound();
    audio.current?.pause(); audio.current = null; setPlaying(null);
  }
  async function speak(message) {
    if(conversation.current.enabled)pauseLive('Прослушивание прошлого ответа. Нажмите «Продолжить» после воспроизведения.');
    if (playing === message.id) return stopAudio();
    if (busy || recording) return;
    stopAudio(); setError(''); setBusy('Готовим озвучивание');
    try {
      const result = await api(`/chats/${active}/messages/${message.id}/speech`, {method: 'POST', body: JSON.stringify({request_id: crypto.randomUUID()})});
      const player = new Audio(result.url);
      audio.current = player; setPlaying(message.id);
      player.onended = () => setPlaying(null);
      player.onerror = () => { setError('Аудио недоступно. Запросите озвучивание снова.'); setPlaying(null); };
      await player.play();
    } catch (e) { setError(e.message); setPlaying(null); }
    finally { setBusy(''); }
  }
  async function copy(message) {
    try { const references = (message.metadata.citations || []).map(source => `[${source.id}] ${source.title} — ${new URL(source.url,location.origin).href}`).join('\n'); await navigator.clipboard.writeText(message.text + (references ? '\n\nИсточники:\n' + references : '')); setCopied(message.id); setTimeout(() => setCopied(null), 1800); }
    catch { setError('Не удалось скопировать текст.'); }
  }
  async function removeChat() {
    if(conversation.current.enabled)pauseLive('',true);
    try {
      await api(`/chats/${active}`, {method: 'DELETE'});
      stopAudio(); setMessages([]); setActive(null); activeRef.current = null; setChatOptions(DEFAULT_CHAT_SETTINGS); setConfirmDelete(false);
      setChats(await api('/chats'));
    } catch (e) { setError(e.message); }
  }
  async function loginDemo(event) {
    event.preventDefault();
    setError('');
    try {
      const response = await fetch('/api/auth/demo', {method: 'POST', redirect: 'manual'});
      if (response.type === 'opaqueredirect' || response.ok) location.assign('/');
      else {
        const problem = await response.json();
        throw new Error(problem.detail || 'Не удалось открыть демо');
      }
    } catch (e) { setError(e.message); }
  }
  async function logout() {
    if(conversation.current.enabled)pauseLive('',true);
    else conversation.current.invalidate();
    sttController.current?.abort();
    try { await api('/auth/logout', {method: 'POST'}); location.reload(); }
    catch (e) { setError(e.message); }
  }

  if (!bootstrap || initializing) return <div className="loading">{error || 'Подключаем AI Talks…'}</div>;
  if (!user) return <div className="login-shell"><div className="login-card"><Brand/><span className="eyebrow">ПРОСТРАНСТВО ДЛЯ ДИАЛОГА</span><h1>Начните<br/>с разговора.</h1><p>Ваши мысли. Возможности ИИ.<br/>Текстом или голосом — как вам удобнее.</p>{(error || location.search.includes('auth_error')) && <div role="alert" className="error">{error || 'Вход не выполнен. Проверьте доступ у администратора.'}</div>}{bootstrap.demo ? <form onSubmit={loginDemo}><button className="primary">Открыть локальное демо <ChevronRight size={18}/></button><small>Тестовый ответ и беззвучный аудиофайл. Платные API отключены.</small></form> : <a className="primary" href={bootstrap.login_url}>Войти с Яндекс ID <ChevronRight size={18}/></a>}<div className="login-note"><ShieldCheck size={15}/> Доступ по приглашению</div></div><div className="login-art"><div className="orb orb-one"/><div className="orb orb-two"/><div className="art-caption">Место, где мысль<br/>обретает голос.<span>AI TALKS / 01</span></div></div></div>;

  const title = chats.find(chat => chat.id === active)?.title || 'Новый разговор';
  return <div className="app-shell">
    {menu && <button className="scrim" aria-label="Закрыть меню" onClick={() => setMenu(false)}/>}
    <aside className={'sidebar ' + (menu ? 'open' : '')}>
      <Brand/><button className="new-chat" onClick={newChat} disabled={!!busy || recording}><Plus size={18}/> Новый разговор</button>
      <div className="section-label">ВАШИ РАЗГОВОРЫ <span>{chats.length.toString().padStart(2, '0')}</span></div>
      <nav aria-label="История разговоров">{chats.length ? chats.map(chat => <button key={chat.id} disabled={!!busy || recording} className={'chat-link ' + (active === chat.id ? 'selected' : '')} onClick={() => select(chat.id, chat).catch(e => setError(e.message))}><MessageSquare size={16}/><span>{chat.title}</span>{active === chat.id && <i/>}</button>) : <p className="empty-history">Здесь появятся ваши разговоры</p>}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={16}/><span>Сообщения доступны вам и администраторам</span></div><div className="profile"><div className="avatar">{user.name.slice(0, 1).toUpperCase()}</div><div><strong>{user.name}</strong><small>{user.role==='admin' ? 'Администратор' : user.role==='editor' ? 'Редактор' : 'Пользователь'}</small></div><button className="icon-button" onClick={logout} disabled={!!busy || recording} title="Выйти" aria-label="Выйти"><LogOut size={16}/></button></div></div>
    </aside>
    <main>
      <header><div className="header-left"><button className="icon-button mobile-menu" onClick={() => setMenu(true)} aria-label="Открыть меню"><Menu size={20}/></button><span>Рабочее пространство</span><ChevronRight size={14}/><strong>Диалог</strong></div><div className="header-right">{config?.capabilities?.rag && <button className="icon-button" onClick={() => setKnowledgeOpen(true)} title="Базы знаний" aria-label="Базы знаний"><BookOpen size={18}/></button>}{user.admin && <button className="icon-button" aria-label="Пользователи" title="Пользователи" onClick={() => setManagementOpen(true)}><Users size={18}/></button>}{user.editor && <button className="icon-button" aria-label="Конфигурация" title="Конфигурация" onClick={() => setConfigurationOpen(true)}><Settings size={18}/></button>}<button type="button" className="icon-button" aria-label="Руководство пользователя" title="Руководство пользователя" onClick={() => setManualOpen(true)}><CircleHelp size={19}/></button></div></header>
      {user.demo && <div className="demo-banner">Локальное демо · ответы и распознавание тестовые · озвучивание без звука</div>}
      <section className="conversation" aria-label="Разговор">
        {messages.length === 0 && !streamDraft ? <div className="welcome"><div className="welcome-icon"><AudioLines size={28}/></div><span className="eyebrow">AI TALKS</span><h1>О чём поговорим?</h1><p>Задайте вопрос или начните с идеи.<br/>Можно просто сказать её вслух.</p><button className="prompt-card" disabled={!config || !!busy || recording} onClick={() => setDraft(config.demo_prompt)}><span className="prompt-card-icon">I</span><div><span className="eyebrow">ПОПРОБУЙТЕ ДЛЯ НАЧАЛА</span><strong>Государев совет при Петре I</strong><p>Четыре взгляда на образовательную реформу</p></div><ChevronRight size={19}/></button><span className="welcome-hint"><Mic size={13}/> Запишите мысль → проверьте текст → отправьте</span></div> : <div className="message-list"><div className="conversation-title"><span>{title}</span><button className="icon-button" disabled={!!busy || recording} onClick={()=>setExportOpen(true)} title="Скачать разговор" aria-label="Скачать разговор"><Download size={15}/></button><button className="icon-button" disabled={!!busy || recording} onClick={() => setConfirmDelete(true)} title="Удалить разговор" aria-label="Удалить разговор"><Trash2 size={15}/></button></div>{messages.map(message => <article key={message.id} className={'message ' + message.role}><div className="message-avatar">{message.role === 'assistant' ? <AudioLines size={18}/> : user.name.slice(0, 1)}</div><div className="message-body"><div className="message-label">{message.role === 'assistant' ? 'AI Talks' : 'Вы'}{message.role === 'assistant' && <span>{message.metadata.demo ? 'ДЕМО' : (message.metadata.model_label || message.metadata.model || config?.model_label)}</span>}</div><div className="message-text"><MessageText message={message} onSource={setSourceChunk}/></div><CitationList sources={message.metadata.citations} onSource={setSourceChunk}/>{message.role === 'assistant' && <div className="message-actions">{message.metadata.retrieval?.web?.page_fetch?.attempted>0&&<span>Прочитано страниц: {message.metadata.retrieval.web.page_fetch.loaded}</span>}{message.metadata.context&&<span title="Количество предыдущих сообщений, включённых в запрос к модели">История: {message.metadata.context.history_messages_included} сообщений</span>}<button onClick={() => speak(message)} disabled={!!busy || recording}>{playing === message.id ? <Square size={14}/> : <Headphones size={14}/>} {playing === message.id ? 'Остановить' : 'Озвучить'}</button><button onClick={() => copy(message)}>{copied === message.id ? <Check size={14}/> : <Copy size={14}/>} {copied === message.id ? 'Скопировано' : 'Копировать'}</button><span>{(message.metadata.elapsed_ms / 1000).toFixed(1)} с{message.metadata.cached ? ' · из кеша' : ''}</span></div>}</div></article>)}<div ref={scroll}/></div>}
        {streamDraft && <div className="message-list stream-preview" aria-label="Потоковый ответ"><article className="message user"><div className="message-avatar">{user.name.slice(0,1)}</div><div className="message-body"><div className="message-label">Вы</div><div className="message-text">{streamDraft.question}</div></div></article><article className="message assistant"><div className="message-avatar"><AudioLines size={18}/></div><div className="message-body"><div className="message-label">AI Talks <span>{streamDraft.provisional ? 'Предварительный текст · ссылки ещё не проверены' : streamDraft.finalized ? 'Ответ готов' : 'Ответ формируется'}</span></div><div className="message-text">{streamDraft.finalized ? <MessageText message={{text:streamDraft.text, metadata:streamDraft.metadata}} onSource={setSourceChunk}/> : (streamDraft.text || '…')}</div>{streamDraft.finalized && <CitationList sources={streamDraft.metadata?.citations} onSource={setSourceChunk}/>}</div></article><div ref={scroll}/></div>}
      </section>
      <div className="composer-area">{communicationMode(chatOptions)==='live'&&<LiveDialogControls state={liveState} disabled={!!busy||recording} onStart={startLive} onPause={()=>pauseLive()} onStop={()=>pauseLive('',true)}/>} {error && <div role="alert" className="error"><span>{error}</span><button className="icon-button" aria-label="Закрыть ошибку" onClick={() => setError('')}><X size={16}/></button></div>}{busy && <div role="status" className="status"><span className="pulse"/>{busy}…{(streamDraft || ['Модель готовит ответ','Ищем источники и готовим ответ'].includes(busy)) && <button className="cancel-link" onClick={cancelGeneration}>Остановить ответ</button>}</div>}{(streamSound.playing || (streamDraft && streamPlayer.current)) && <div className="stream-audio-controls">{streamSound.needsGesture && <button onClick={() => streamPlayer.current?.resume()}>Включить звук</button>}<button onClick={()=>{if(conversation.current.enabled)pauseLive('Озвучивание остановлено.');else stopStreamSound();}}><Square size={13}/> Остановить звук</button></div>}{recording && <div role="status" className="recording-status"><span className="pulse"/>Запись · {seconds} с / 25 с. {communicationMode(chatOptions) === 'voice' && !chatOptions.review_before_send ? 'Остановите запись, чтобы отправить вопрос.' : 'Остановите запись, чтобы проверить текст.'}</div>}<form className={'composer ' + (recording ? 'is-recording' : '')} onSubmit={send}><textarea aria-label="Сообщение" placeholder="Напишите сообщение…" value={draft} onChange={e => {setDraft(e.target.value); pendingRequest.current = null;}} maxLength={4000} disabled={!!busy || recording || liveState.active} rows={2} onKeyDown={e => {if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); send(e);}}}/><ChatSettings config={config} value={chatOptions} disabled={!!busy || recording || liveState.active} modeDisabled={recording || (!!busy&&!liveState.enabled)} onChange={changeSettings}/><div className="composer-bottom"><span>{recording ? 'Микрофон включён' : 'Текст или голос'}<span className="char-count">{draft.length ? ` · ${draft.length}/4000` : ''}</span></span><div>{recording && <button type="button" className="cancel-recording" onClick={() => stopRecording(true)} aria-label="Отменить запись"><X size={16}/><span>Отменить</span></button>}{communicationMode(chatOptions)!=='live'&&<button type="button" className={'mic-button ' + (recording ? 'active' : '')} disabled={!!busy} onClick={record} aria-label={recording ? 'Закончить запись' : 'Записать голос'} title={recording ? 'Закончить запись' : 'Записать голос'}>{recording ? <Square size={18}/> : <Mic size={19}/>}</button>}<button className="send-button" type="submit" disabled={!draft.trim() || !!busy || recording || liveState.active} aria-label="Отправить сообщение"><ArrowUp size={20}/></button></div></div></form><div className="composer-caption"><span>ИИ может ошибаться. Проверяйте важную информацию.</span><span className="keyboard-hint">Enter ↵ отправить</span></div></div>
      <footer><span>AI TALKS</span><span>Голос и мысль в одном пространстве</span><span>AI TALKS</span></footer>
    </main>
    {configurationOpen && <ConfigurationPanel api={api} config={config} admin={user.admin} currentChatId={active} onSaved={async () => {await refreshCatalog(); const list=await api('/chats'); setChats(list); const current=list.find(chat => chat.id===active); if(current)setChatOptions(current.settings);}} onClose={() => setConfigurationOpen(false)}/>}
    {managementOpen && <ManagementPanel api={api} onSource={setSourceChunk} config={config} admin={user.admin} onClose={() => {setManagementOpen(false); api('/chats').then(list => {setChats(list); const selected=list.find(chat => chat.id===active); if(selected)setChatOptions(selected.settings);}).catch(e => setError(e.message));}}/>}
    {sourceChunk && <SourceModal chunkId={sourceChunk} onClose={() => setSourceChunk(null)}/>}
    {knowledgeOpen && <KnowledgeManager editor={user.editor} onSource={setSourceChunk} api={api} admin={user.admin} initialBases={config.knowledge_bases} refresh={refreshCatalog} onClose={() => {setKnowledgeOpen(false); refreshCatalog().catch(e => setError(e.message));}}/>}
    {manualOpen && <UserManual onClose={() => setManualOpen(false)}/>}
    {exportOpen && active && <ExportChat chatId={active} onClose={()=>setExportOpen(false)}/>}
    {confirmDelete && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="Удаление разговора"><h2>Удалить разговор?</h2><p>Сообщения и связанные кешированные данные будут удалены.</p><div className="modal-actions"><button onClick={() => setConfirmDelete(false)}>Отмена</button><button className="primary" onClick={removeChat}>Удалить</button></div></section></div>}

  </div>;
}
function Brand() { return <div className="brand"><span className="brand-symbol"><AudioLines size={22}/></span><span>ai talks<span className="brand-dot">.</span></span></div>; }
const sourceRoute = location.pathname.match(/^\/(sources|web-sources)\/([0-9a-f]{64})$/);
createRoot(document.getElementById('root')).render(sourceRoute ? <SourceViewer chunkId={sourceRoute[2]} kind={sourceRoute[1]==='web-sources'?'web':'rag'}/> : <App/>);
