import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {BookOpen, Users, Star, Settings, CircleHelp, Download, ArrowUp, AudioLines, Check, ChevronRight, Copy, Headphones, LogOut, Menu, MessageSquare, Mic, Plus, ShieldCheck, Square, Trash2, X} from 'lucide-react';
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
import {SpacesPanel} from './SpacesPanel';
import {SourceViewer, SourceModal} from './SourceViewer';
import './style.css';
import './controls.css';

function App() {
  const [bootstrap, setBootstrap] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [user, setUser] = useState(null);
  const [spaces, setSpaces] = useState([]);
  const [space, setSpace] = useState(null);
  const [spaceManagement, setSpaceManagement] = useState(false);
  const [archived, setArchived] = useState(null);
  const [archiveMessages, setArchiveMessages] = useState([]);
  const spaceRef = useRef(null);
  const workspaceState = useRef(new Map());
  const defaults = useRef(DEFAULT_CHAT_SETTINGS);
  const savedDraft = useRef('');
  const speechRequest = useRef(null);
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
    if (spaceRef.current) headers['X-Space-ID'] = spaceRef.current;
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
      if (me.status === 'active') {
        const [settings, available] = await Promise.all([api('/config'), api('/spaces')]);
        setConfig(settings); setSpaces(available);
      }
    } catch (e) { setError(e.message); }
    finally { setInitializing(false); }
  }
  useEffect(() => { initialize(); return () => { audio.current?.pause(); streamPlayer.current?.stop(); streamController.current?.abort(); liveSetup.current?.abort(); sttController.current?.abort(); conversation.current.pause('',true); }; }, []);
  useEffect(() => {
    savedDraft.current = draft;
  }, [draft]);
  useEffect(() => {
    if (user?.status !== 'active') return;
    const timer = setInterval(async () => {
      try {
        const available = await api('/spaces');
        setSpaces(available);
        if (space && spaceRef.current === space.id) {
          const latest = available.find(item => item.id === space.id);
          if (!latest) {
            leaveSpace();
            setError('Доступ к пространству изменился');
          } else if (latest.name !== space.name || latest.description !== space.description) {
            setSpace(latest);
          }
        }
      } catch {
        if (space && spaceRef.current === space.id) leaveSpace();
        setError('Не удалось проверить доступ. Обновите страницу.');
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [user?.id, space, active, chatOptions]);

  function leaveSpace() {
    const previous = spaceRef.current;
    if (previous) workspaceState.current.set(previous, {id: activeRef.current, draft: savedDraft.current, options: chatOptions});
    conversation.current.pause('', true);
    liveSetup.current?.abort();
    sttController.current?.abort();
    streamController.current?.abort();
    for (const rid of [sttRequest.current, streamRequest.current, speechRequest.current, pendingRequest.current?.id]) {
      if (rid) api(`/requests/${rid}/cancel`, {method:'POST'}).catch(() => {});
    }
    stopAudio();
    for (const ref of [sttRequest, streamRequest, speechRequest, pendingRequest]) ref.current = null;
    spaceRef.current = null; activeRef.current = null;
    setSourceChunk(null); setSpace(null); setActive(null); setMessages([]); setChats([]); setRecording(false);
    setDraft(''); setStreamDraft(null); setStreamSound({playing:false, needsGesture:false}); setBusy(''); setMenu(false); setExportOpen(false);
    setConfirmDelete(false); setError('');
  }

  async function enterSpace(item) {
    spaceRef.current = item.id;
    setBusy('Открываем пространство'); setError('');
    try {
      const [list, options] = await Promise.all([api('/chats'), api('/space-defaults')]);
      if (spaceRef.current !== item.id) return;
      defaults.current = options;
      const saved = workspaceState.current.get(item.id);
      const selected = list.find(chat => chat.id === saved?.id) || (!saved && list[0]);
      const items = selected ? await api(`/chats/${selected.id}/messages`) : [];
      if (spaceRef.current !== item.id) return;
      activeRef.current = selected?.id ?? null;
      setActive(activeRef.current); setChats(list); setMessages(items); setSpace(item);
      setChatOptions(selected?.settings || saved?.options || options);
      setDraft(saved?.draft || '');
    } catch (e) {
      if (spaceRef.current === item.id) {spaceRef.current = null; setError(e.message);}
    } finally { if (!spaceRef.current || spaceRef.current === item.id) setBusy(''); }
  }

  async function openHistory() {
    try {setArchived(await api('/chats/archived')); setArchiveMessages([]); setManualOpen(false);}
    catch (e) {setError(e.message);}
  }
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
    const token = conversation.current.epoch;
    try {
      const result = await api(`/chats/${id}/messages`);
      if (conversation.current.valid(token) && activeRef.current === id) setMessages(result);
    } finally {if (conversation.current.valid(token)) setBusy('');}
  }
  async function changeSettings(mode) {
    if (recording || (busy && !conversation.current.enabled)) return;
    if (conversation.current.enabled) pauseLive('',true);
    const token = conversation.current.epoch;
    if (!active) {
      setChatOptions({...defaults.current, communication_mode: mode,
        streaming_enabled: mode !== 'text' || defaults.current.streaming_enabled, auto_speech: mode !== 'text'});
      return;
    }
    setBusy('Сохраняем режим'); setError('');
    try {
      const updated = await api(`/chats/${active}/mode`, {method:'PATCH', body:JSON.stringify({communication_mode:mode})});
      if (!conversation.current.valid(token)) return;
      setChatOptions(updated.settings);
      setChats(list => list.map(chat => chat.id === active ? updated : chat));
    } catch (e) {if (conversation.current.valid(token)) setError(e.message);}
    finally {if (conversation.current.valid(token)) setBusy('');}
  }
  async function newChat() {
    if (busy || recording) return;
    if(conversation.current.enabled) pauseLive('',true);
    const token = conversation.current.epoch;
    setBusy('Создаём разговор');
    try {
      const chat = await api('/chats', {method: 'POST', body:JSON.stringify({})});
      if (!conversation.current.valid(token)) return;
      setChats(list => [chat, ...list]); await select(chat.id, chat);
    } catch (e) {if (spaceRef.current === space.id) setError(e.message);}
    finally {if (spaceRef.current === space.id) setBusy('');}
  }
  async function send(event, regenerate = false, submission = null) {
    event?.preventDefault();
    if (!submission && (busy || recording || conversation.current.active)) return;
    const text = (submission?.text ?? draft).trim();
    if (!text) return;
    let options = submission?.options || chatOptions;
    const token = submission?.token ?? conversation.current.epoch;
    const current = () => conversation.current.valid(token);
    const live = !!submission?.live;
    if(!current())return false;
    if(live)conversation.current.transition('generating',token);
    setError(''); setBusy('Модель готовит ответ');
    if (!submission?.preparedAudio) { stopAudio(); if (options.streaming_enabled && options.auto_speech) prepareSpeech(); }
    let textCompleted = false;
    try {
      let id = active;
      if (!id) {
        const chat = await api('/chats', {method: 'POST', body: JSON.stringify({communication_mode: communicationMode(options)})});
        if(!current())return false;
        id = chat.id; setActive(id); activeRef.current = id;
        options = chat.settings; setChatOptions(options);
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
          headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf.current, 'X-Space-ID':space.id}, body,
          signal:streamController.current.signal});
        await readReplyStream(response, event => {
          if(!current())return;
          if (event.event === 'status') setBusy(event.text);
          if (event.event === 'delta') {
            setBusy('Формирую ответ');
            setStreamDraft(previous => ({...previous, text:previous.text + event.text, provisional:event.provisional}));
          }
          if (event.event === 'answer') setStreamDraft(previous => ({...previous, text:event.text, id:event.message_id, provisional:false, metadata:event.metadata, finalized:true}));
          if (event.event === 'text_done') { textCompleted = true; setDraft(''); setBusy('Готовлю озвучивание'); }
          if (event.event === 'audio') streamPlayer.current?.enqueue(event.url);
          if (event.event === 'audio_error') {
            setError(event.detail);
            if(live)pauseLive(event.detail);
          }
        });
      } else {
        streamController.current = new AbortController();
        await api(`/chats/${id}/messages`, {method:'POST', body, signal:streamController.current.signal});
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
    if (conversation.current.enabled) {
      pauseLive('Ответ остановлен. Проверьте историю перед повтором.');
      return;
    }
    const rid = pendingRequest.current?.id;
    if (!rid) return;
    const token = conversation.current.epoch;
    const request = streamController.current;
    stopStreamSound();
    try {
      const result = await api('/requests/' + rid + '/cancel', {method:'POST'});
      if (!result.cancel_requested && conversation.current.valid(token)) request?.abort();
    } catch (e) {if (conversation.current.valid(token)) setError(e.message);}
  }
  function prepareSpeech() {
    const token = conversation.current.epoch;
    try { streamPlayer.current = new StreamAudioPlayer(state=>{
      if (!conversation.current.valid(token)) return;
      setStreamSound(state);
      if(conversation.current.active && state.needsGesture)pauseLive('Браузер заблокировал звук. Нажмите «Продолжить», чтобы включить его.');
      else if(conversation.current.active && state.playing)conversation.current.transition('speaking');
    }, message => {
      if (!conversation.current.valid(token)) return;
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
    const token = conversation.current.epoch;
    try {
      const stop=await startCapture(() => stopRecording(false, true));
      if(await conversation.current.attach(stop,token))setRecording(true);
    } catch (e) {if (conversation.current.valid(token)) {stopStreamSound(); setError(e.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере.' : e.message);}}
    finally {if (conversation.current.valid(token)) setBusy('');}
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
    setStreamSound({playing:false, needsGesture:false});
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
    const token = conversation.current.epoch;
    const rid = crypto.randomUUID();
    speechRequest.current = rid;
    try {
      const result = await api(`/chats/${active}/messages/${message.id}/speech`, {method: 'POST', body: JSON.stringify({request_id: rid})});
      if (!conversation.current.valid(token)) return;
      const player = new Audio(result.url);
      audio.current = player; setPlaying(message.id);
      player.onended = () => {if (audio.current === player) setPlaying(null);};
      player.onerror = () => {
        if (audio.current !== player) return;
        setError('Аудио недоступно. Запросите озвучивание снова.');
        setPlaying(null);
      };
      await player.play();
    } catch (e) {if (conversation.current.valid(token)) {setError(e.message); setPlaying(null);}}
    finally {
      if (speechRequest.current === rid) speechRequest.current = null;
      if (conversation.current.valid(token)) setBusy('');
    }
  }
  async function copy(message) {
    try {await navigator.clipboard.writeText(message.text); setCopied(message.id); setTimeout(() => setCopied(null), 1800);}
    catch { setError('Не удалось скопировать текст.'); }
  }
  async function removeChat() {
    if(conversation.current.enabled)pauseLive('',true);
    const token = conversation.current.epoch;
    try {
      await api(`/chats/${active}`, {method: 'DELETE'});
      if (!conversation.current.valid(token)) return;
      stopAudio(); setMessages([]); setActive(null); activeRef.current = null; setChatOptions(defaults.current); setConfirmDelete(false);
      const list = await api('/chats');
      if (conversation.current.valid(token)) setChats(list);
    } catch (e) {if (conversation.current.valid(token)) setError(e.message);}
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
    leaveSpace(); workspaceState.current.clear();
    try { await api('/auth/logout', {method: 'POST'}); location.reload(); }
    catch (e) { setError(e.message); }
  }

  if (!bootstrap || initializing) return <div className="loading">{error || 'Подключаем AI Talks…'}</div>;
  if (!user) return <div className="login-shell"><div className="login-card"><Brand/><span className="eyebrow">ПРОСТРАНСТВО ДЛЯ ДИАЛОГА</span><h1>Начните<br/>с разговора.</h1><p>Ваши мысли. Возможности ИИ.<br/>Текстом или голосом — как вам удобнее.</p>{(error || location.search.includes('auth_error')) && <div role="alert" className="error">{error || 'Вход не выполнен. Проверьте доступ у администратора.'}</div>}{bootstrap.demo ? <form onSubmit={loginDemo}><button className="primary">Открыть локальное демо <ChevronRight size={18}/></button><small>Тестовый ответ и беззвучный аудиофайл. Платные API отключены.</small></form> : <a className="primary" href={bootstrap.login_url}>Войти с Яндекс ID <ChevronRight size={18}/></a>}<div className="login-note"><ShieldCheck size={15}/> Доступ после подтверждения администратором</div></div><div className="login-art"><div className="orb orb-one"/><div className="orb orb-two"/><div className="art-caption">Место, где мысль<br/>обретает голос.<span>AI TALKS / 01</span></div></div></div>;

  const management = <>
    {spaceManagement && <SpacesPanel api={api} admin={user.admin} onClose={() => {
      setSpaceManagement(false); api('/spaces').then(setSpaces).catch(e => setError(e.message));
    }}/>}
    {configurationOpen && user.admin && <ConfigurationPanel api={api} config={config} admin onSaved={refreshCatalog} onClose={() => setConfigurationOpen(false)}/>}
    {managementOpen && user.editor && <ManagementPanel api={api} onSource={setSourceChunk} admin={user.admin} onClose={() => {setManagementOpen(false); api('/spaces').then(setSpaces).catch(e => setError(e.message));}}/>}
    {sourceChunk && <SourceModal chunkId={sourceChunk} onClose={() => setSourceChunk(null)}/>}
    {knowledgeOpen && <KnowledgeManager editor onSource={setSourceChunk} api={api} admin={user.admin} initialBases={[]} refresh={refreshCatalog} onClose={() => setKnowledgeOpen(false)}/>}
    {manualOpen && <UserManual onClose={() => setManualOpen(false)} onHistory={!space && user.status === 'active' ? openHistory : undefined}/>}
  </>;

  if (!space) return <>
    <header className="workspace-header"><Brand/><div className="header-right">
      {user.admin && <HeaderAction icon={Star} label="Управление" onClick={() => setSpaceManagement(true)}/>}
      {user.editor && <HeaderAction icon={BookOpen} label="Базы знаний" onClick={() => setKnowledgeOpen(true)}/>}
      {user.editor && <HeaderAction icon={Users} label="Пользователи" onClick={() => setManagementOpen(true)}/>}
      {user.admin && <HeaderAction icon={Settings} label="Настройки" onClick={() => setConfigurationOpen(true)}/>}
      <HeaderAction icon={CircleHelp} label="Руководство пользователя" onClick={() => setManualOpen(true)}/>
      <HeaderAction icon={LogOut} label="Выйти" onClick={logout}/>
    </div></header>
    <div className="workspace-picker">
    <h1>{user.status === 'pending' ? 'Заявка на доступ' : 'Ваши пространства'}</h1>
    {user.status === 'pending' ? <p>Администратор должен подтвердить ваш доступ. После подтверждения обновите страницу.</p> :
      spaces.length ? <div className="workspace-cards">{spaces.map(item => <button type="button" key={item.id} disabled={!!busy} onClick={() => enterSpace(item)}>
        <strong>{item.name}</strong><span>{item.description}</span>
      </button>)}</div> : <p>Вы пока не добавлены ни в одно пространство.</p>}
    {busy && <p role="status">{busy}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {archived && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="Архивная история">
      <h2>Архивная история</h2>
      {archived.length ? archived.map(chat => <button key={chat.id} onClick={async () => {
        try {setArchiveMessages(await api(`/chats/${chat.id}/messages`));} catch (e) {setError(e.message);}
      }}>{chat.space_name} · {chat.title}</button>) : <p>Архивных разговоров пока нет</p>}
      {archiveMessages.map(message => <p key={message.id}><strong>{message.role === 'assistant' ? 'AI Talks' : 'Вы'} </strong>{message.text}</p>)}
      <div className="modal-actions"><button onClick={() => setArchived(null)}>Закрыть</button></div>
    </section></div>}
    {management}
    </div>
  </>;

  const title = chats.find(chat => chat.id === active)?.title || 'Новый разговор';
  return <div className="app-shell">
    {menu && <button className="scrim" aria-label="Закрыть меню" onClick={() => setMenu(false)}/>}
    <aside className={'sidebar ' + (menu ? 'open' : '')}>
      <Brand onHome={leaveSpace}/><button className="new-chat" onClick={newChat} disabled={!!busy || recording}><Plus size={18}/> Новый разговор</button>
      <div className="section-label">ВАШИ РАЗГОВОРЫ <span>{chats.length.toString().padStart(2, '0')}</span></div>
      <nav aria-label="История разговоров">{chats.length ? chats.map(chat => <button key={chat.id} disabled={!!busy || recording} className={'chat-link ' + (active === chat.id ? 'selected' : '')} onClick={() => select(chat.id, chat).catch(e => setError(e.message))}><MessageSquare size={16}/><span>{chat.title}</span>{active === chat.id && <i/>}</button>) : <p className="empty-history">Здесь появятся ваши разговоры</p>}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={16}/><span>Сообщения доступны вам и администраторам</span></div><div className="profile"><div className="avatar">{user.name.slice(0, 1).toUpperCase()}</div><div><strong>{user.name}</strong><small>{user.role==='admin' ? 'Администратор' : user.role==='editor' ? 'Редактор' : 'Пользователь'}</small></div></div></div>
    </aside>
    <main>
      <header><div className="header-left"><button className="icon-button mobile-menu" onClick={() => setMenu(true)} aria-label="Открыть меню"><Menu size={20}/></button><strong>{space.name}</strong></div><div className="header-right"><HeaderAction icon={CircleHelp} label="Руководство пользователя" onClick={() => setManualOpen(true)}/><HeaderAction icon={LogOut} label="Выйти из пространства" onClick={leaveSpace}/></div></header>
      {user.demo && <div className="demo-banner">Локальное демо · ответы и распознавание тестовые · озвучивание без звука</div>}
      <section className="conversation" aria-label="Разговор">
        {messages.length === 0 && !streamDraft ? <div className="welcome"><div className="welcome-icon"><AudioLines size={28}/></div><span className="eyebrow">AI TALKS</span><h1>О чём поговорим?</h1><p>Задайте вопрос или начните с идеи.<br/>Можно просто сказать её вслух.</p><button className="prompt-card" disabled={!config || !!busy || recording} onClick={() => setDraft(config.demo_prompt)}><span className="prompt-card-icon">I</span><div><span className="eyebrow">ПОПРОБУЙТЕ ДЛЯ НАЧАЛА</span><strong>Государев совет при Петре I</strong><p>Четыре взгляда на образовательную реформу</p></div><ChevronRight size={19}/></button><span className="welcome-hint"><Mic size={13}/> Запишите мысль → проверьте текст → отправьте</span></div> : <div className="message-list"><div className="conversation-title"><span>{title}</span><div className="conversation-tools"><button className="icon-button" disabled={!!busy || recording} onClick={()=>setExportOpen(true)} title="Скачать разговор" aria-label="Скачать разговор"><Download size={15}/></button><button className="icon-button" disabled={!!busy || recording} onClick={() => setConfirmDelete(true)} title="Удалить разговор" aria-label="Удалить разговор"><Trash2 size={15}/></button></div></div>{messages.map(message => <article key={message.id} className={'message ' + message.role}><div className="message-avatar">{message.role === 'assistant' ? <AudioLines size={18}/> : user.name.slice(0, 1)}</div><div className="message-body"><div className="message-label">{message.role === 'assistant' ? 'AI Talks' : 'Вы'}{message.role === 'assistant' && <span>{message.metadata.demo ? 'ДЕМО' : ''}</span>}</div><div className="message-text"><OwnerReply message={message} chatId={active} spaceId={space.id} onSource={setSourceChunk}/></div>{message.role === 'assistant' && <div className="message-actions"><button onClick={() => speak(message)} disabled={!!busy || recording}>{playing === message.id ? <Square size={14}/> : <Headphones size={14}/>} {playing === message.id ? 'Остановить' : 'Озвучить'}</button><button onClick={() => copy(message)}>{copied === message.id ? <Check size={14}/> : <Copy size={14}/>} {copied === message.id ? 'Скопировано' : 'Копировать'}</button>{Number.isFinite(message.metadata.elapsed_ms) && <span>{(message.metadata.elapsed_ms / 1000).toFixed(1)} с{message.metadata.cached ? ' · из кеша' : ''}</span>}</div>}</div></article>)}<div ref={scroll}/></div>}
        {streamDraft && <div className="message-list stream-preview" aria-label="Потоковый ответ"><article className="message user"><div className="message-avatar">{user.name.slice(0,1)}</div><div className="message-body"><div className="message-label">Вы</div><div className="message-text">{streamDraft.question}</div></div></article><article className="message assistant"><div className="message-avatar"><AudioLines size={18}/></div><div className="message-body"><div className="message-label">AI Talks <span>{streamDraft.finalized ? 'Ответ готов' : 'Ответ формируется'}</span></div><div className="message-text">{streamDraft.finalized ? <OwnerReply message={streamDraft} chatId={active} spaceId={space.id} onSource={setSourceChunk}/> : (streamDraft.text || '…')}</div></div></article><div ref={scroll}/></div>}
      </section>
      <div className="composer-area">{communicationMode(chatOptions)==='live'&&<LiveDialogControls state={liveState} disabled={!!busy||recording} onStart={startLive} onPause={()=>pauseLive()} onStop={()=>pauseLive('',true)}/>} {error && <div role="alert" className="error"><span>{error}</span><button className="icon-button" aria-label="Закрыть ошибку" onClick={() => setError('')}><X size={16}/></button></div>}{busy && <div role="status" className="status"><span className="pulse"/>{busy}…{(streamDraft || busy === 'Модель готовит ответ') && <button className="cancel-link" onClick={cancelGeneration}>Остановить ответ</button>}</div>}{(streamSound.playing || (streamDraft && streamPlayer.current)) && <div className="stream-audio-controls">{streamSound.needsGesture && <button onClick={() => streamPlayer.current?.resume()}>Включить звук</button>}<button onClick={()=>{if(conversation.current.enabled)pauseLive('Озвучивание остановлено.');else stopStreamSound();}}><Square size={13}/> Остановить звук</button></div>}{recording && <div role="status" className="recording-status"><span className="pulse"/>Запись · {seconds} с / 25 с. {communicationMode(chatOptions) === 'voice' && !chatOptions.review_before_send ? 'Остановите запись, чтобы отправить вопрос.' : 'Остановите запись, чтобы проверить текст.'}</div>}<form className={'composer ' + (recording ? 'is-recording' : '')} onSubmit={send}><div className="composer-input-row"><textarea aria-label="Сообщение" placeholder="Напишите сообщение…" value={draft} onChange={e => {setDraft(e.target.value); pendingRequest.current = null;}} maxLength={4000} disabled={!!busy || recording || liveState.active} rows={2} onKeyDown={e => {if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); send(e);}}}/><ChatSettings value={chatOptions} modeDisabled={recording || (!!busy&&!liveState.enabled)} onChange={changeSettings}/></div><div className="composer-bottom"><span>{recording ? 'Микрофон включён' : 'Текст или голос'}<span className="char-count">{draft.length ? ` · ${draft.length}/4000` : ''}</span></span><div>{recording && <button type="button" className="cancel-recording" onClick={() => stopRecording(true)} aria-label="Отменить запись"><X size={16}/><span>Отменить</span></button>}{communicationMode(chatOptions)!=='live'&&<button type="button" className={'mic-button ' + (recording ? 'active' : '')} disabled={!!busy} onClick={record} aria-label={recording ? 'Закончить запись' : 'Записать голос'} title={recording ? 'Закончить запись' : 'Записать голос'}>{recording ? <Square size={18}/> : <Mic size={19}/>}</button>}{communicationMode(chatOptions) === 'text' && <button className="send-button" type="submit" disabled={!draft.trim() || !!busy || recording || liveState.active} aria-label="Отправить сообщение"><ArrowUp size={20}/></button>}
      {communicationMode(chatOptions) !== 'text' && draft.trim() && !liveState.active && <button type="submit" className="voice-send" disabled={!!busy || recording}>Отправить текст</button>}
      </div></div></form><div className="composer-caption"><span>ИИ может ошибаться. Проверяйте важную информацию.</span><span className="keyboard-hint">Enter ↵ отправить</span></div></div>
      <footer><span>AI TALKS</span><span>Голос и мысль в одном пространстве</span><span>AI TALKS</span></footer>
    </main>
    {management}
    {exportOpen && active && <ExportChat chatId={active} spaceId={space.id} onClose={()=>setExportOpen(false)}/>}
    {confirmDelete && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="Удаление разговора"><h2>Удалить разговор?</h2><p>Сообщения и связанные кешированные данные будут удалены.</p><div className="modal-actions"><button onClick={() => setConfirmDelete(false)}>Отмена</button><button className="primary" onClick={removeChat}>Удалить</button></div></section></div>}

  </div>;
}
function CitationButton({source, onClick, children}) {
  return <button type="button" className="citation-marker chat-citation"
    title={source.title} disabled={!source.available} onClick={() => onClick(source)}>
    {children}
  </button>;
}

function OwnerReply({message, chatId, spaceId, onSource}) {
  const citations = message.metadata?.citations || [];
  function open(source) {
    onSource({
      id: source.id,
      type: source.type,
      preview: '/api/chats/' + chatId + '/messages/' + message.id + '/citations/' + source.id,
      spaceId,
    });
  }
  return <>
    <ChatText text={message.text} citations={citations} open={open}/>
    {!!citations.length && <details className="citation-list">
      <summary>Источники · {citations.length}</summary>
      {citations.map(source => <div key={source.id}>
        <CitationButton source={source} onClick={open}>
          [{source.id}] {source.title}{source.page ? ' · стр. ' + source.page : ''}
        </CitationButton>
      </div>)}
    </details>}
  </>;
}

function ChatText({text, citations, open}) {
  const links = /(?:(?:\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s<>\[\]()]+)|(\[[DW]\d+\]))/gi;
  const contents = [];
  let start = 0;
  for (const match of text.matchAll(links)) {
    const [whole, label, markdownUrl, plainUrl, marker] = match;
    if (marker) {
      const source = citations.find(item => item.id === marker.slice(1, -1));
      contents.push(text.slice(start, match.index));
      contents.push(source ? <CitationButton key={match.index} source={source} onClick={open}>{marker}</CitationButton> : marker);
      start = match.index + marker.length;
      continue;
    }
    const url = markdownUrl || plainUrl.replace(/[.,:;!?]+$/, '');
    contents.push(text.slice(start, match.index));
    contents.push(<a className="chat-url" key={match.index} href={url} target="_blank" rel="noopener noreferrer">{label || url}</a>);
    if (plainUrl) contents.push(plainUrl.slice(url.length));
    start = match.index + whole.length;
  }
  contents.push(text.slice(start));
  return contents;
}

function HeaderAction({icon: Icon, label, onClick}) {
  return <button type="button" className="icon-button" title={label} aria-label={label} onClick={onClick}><Icon size={19}/></button>;
}

function Brand({onHome}) {
  const logo = <><span className="brand-symbol"><AudioLines size={22}/></span><span>ai talks<span className="brand-dot">.</span></span></>;
  if (!onHome) return <div className="brand">{logo}</div>;
  return <a className="brand" aria-label="AI Talks · Ваши пространства" href="/" onClick={event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onHome();
  }}>{logo}</a>;
}
const sourceRoute = location.pathname.match(/^\/(sources|web-sources)\/([0-9a-f]{64})$/);
createRoot(document.getElementById('root')).render(sourceRoute ? <SourceViewer chunkId={sourceRoute[2]} kind={sourceRoute[1]==='web-sources'?'web':'rag'}/> : <App/>);
