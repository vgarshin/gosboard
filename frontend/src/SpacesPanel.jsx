import {Toggle} from './Toggle';
import React, {useEffect, useState} from 'react';
import {TriangleAlert, X} from 'lucide-react';
import {PanelTabs} from './PanelTabs';
import {useDialog} from './useDialog';

const roleLabels = {user:'Пользователь', editor:'Редактор', admin:'Администратор'};
const modes = {text:'Текстовый', voice:'Голосовой', live:'Живой диалог'};

export function SpacesPanel({api, admin, onClose}) {
  const dialog = useDialog(onClose);
  return <div className="modal-backdrop">
    <section ref={dialog} className="modal configuration-modal" role="dialog" aria-modal="true" aria-label="Управление пространствами">
      <button type="button" className="icon-button modal-close" aria-label="Закрыть управление пространствами" onClick={onClose}><X size={18}/></button>
      <h2>Управление пространствами</h2>
      <SpaceManagement api={api} admin={admin}/>
    </section>
  </div>;
}

export function SpaceManagement({api, admin = false}) {
  const [tab, setTab] = useState('existing');
  const [spaces, setSpaces] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [sid, setSid] = useState('');
  const [email, setEmail] = useState('');
  const [policy, setPolicy] = useState(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [spaceName, setSpaceName] = useState('');
  const [spaceDescription, setSpaceDescription] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const space = spaces.find(item => item.id === sid);
  const account = accounts.find(item => item.email === email);

  async function load() {
    const [items, users] = await Promise.all([
      api('/management/spaces'), api('/management/space-accounts'),
    ]);
    setSpaces(items);
    setAccounts(users);
  }

  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    setPolicy(null);
    setConfirmation('');
    setSpaceName(space?.name || '');
    setSpaceDescription(space?.description || '');
    if (!space || space.archived) return;
    let current = true;
    api(`/management/spaces/${sid}/policy`).then(data => {if (current) setPolicy(data);})
      .catch(e => {if (current) setError(e.message);});
    return () => {current = false;};
  }, [sid, space?.revision, space?.archived]);

  async function run(work) {
    setBusy(true); setError('');
    try {await work(); await load();}
    catch (e) {setError(e.message);}
    finally {setBusy(false);}
  }

  function assign(assigned, management) {
    run(() => api(`/management/spaces/${sid}/assignments`, {
      method:'PUT', body:JSON.stringify({email, assigned, management}),
    }));
  }

  function field(key, value) {
    setPolicy(previous => ({...previous, policy:{...previous.policy, [key]:value}}));
  }

  function setting(key, value) {
    const next = {...policy.policy.settings, [key]:value};
    if (key === 'model_id') next.reasoning_effort = 'default';
    field('settings', next);
  }

  const settings = policy?.policy.settings;
  const prompts = policy?.policy.prompts || [];
  const selectedPrompt = prompts.find(item => item.id === policy.policy.prompt_id);
  const model = policy?.models.find(item => item.id === settings?.model_id);

  return <PanelTabs label="Пространства" tabs={admin ? [['existing','Существующие пространства'],['new','Новое пространство']] : [['existing','Назначенные пространства']]} active={tab} onChange={setTab}>
      {error && <p className="error" role="alert">{error}</p>}
      {tab === 'new' && <form className="management-form" onSubmit={event => {
        event.preventDefault();
        run(async () => {
          const created = await api('/management/spaces', {method:'POST', body:JSON.stringify({name,description})});
          setSid(created.id); setName(''); setDescription(''); setTab('existing');
        });
      }}>
        <label>Название нового пространства<input value={name} onChange={e => setName(e.target.value)} required maxLength={120}/></label>
        <label>Описание<textarea value={description} onChange={e => setDescription(e.target.value)} maxLength={2000}/></label>
        <button disabled={busy}>Создать пространство</button>
      </form>}
      {tab === 'existing' && <>
      <label className="space-picker">Пространство<select value={sid} disabled={busy} onChange={e => setSid(e.target.value)}>
        <option value="">Выберите пространство</option>
        {spaces.map(item => <option key={item.id} value={item.id}>{item.name}{item.archived ? ' · архив' : ''}</option>)}
      </select></label>
      {space && !space.archived && <>
        <form className="management-form" onSubmit={event => {
          event.preventDefault();
          run(() => api(`/management/spaces/${sid}?revision=${space.revision}`, {
            method:'PUT', body:JSON.stringify({name:spaceName, description:spaceDescription}),
          }));
        }}>
          <label>Название<input value={spaceName} required maxLength={120} onChange={e => setSpaceName(e.target.value)}/></label>
          <label>Описание<textarea value={spaceDescription} maxLength={2000} onChange={e => setSpaceDescription(e.target.value)}/></label>
          <button disabled={busy || (spaceName === space.name && spaceDescription === space.description)}>Сохранить название и описание</button>
        </form>
        <div className="management-form">
          <label>Аккаунт<select value={email} onChange={e => setEmail(e.target.value)} disabled={busy}>
            <option value="">Выберите аккаунт</option>
            {accounts.map(item => <option key={item.email} value={item.email}>{item.email} · {roleLabels[item.role]}</option>)}
          </select></label>
          {account && <>
            <label><Toggle checked={account.memberships.includes(sid)} disabled={busy} onChange={e => assign(e.target.checked, false)}/> Доступ к разговорам</label>
            {admin && account.role === 'editor' && account.status === 'active' && <label>
              <Toggle checked={account.managed.includes(sid)} disabled={busy} onChange={e => assign(e.target.checked, true)}/> Управление пространством
            </label>}
          </>}
        </div>
        {settings && <form className="management-form" onSubmit={event => {
          event.preventDefault();
          run(() => api(`/management/spaces/${sid}/policy`, {
            method:'PUT', body:JSON.stringify({revision:space.revision, policy:policy.policy}),
          }));
        }}>
          <h3>Настройки новых разговоров</h3>
          <label>Модель<select value={settings.model_id} onChange={e => setting('model_id', e.target.value)}>
            {policy.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select></label>
          {!!model?.reasoning_efforts?.length && <label>Режим рассуждений<select value={settings.reasoning_effort} onChange={e => setting('reasoning_effort', e.target.value)}>
            <option value="default">По умолчанию</option>
            {model.reasoning_efforts.map(mode => <option key={mode} value={mode}>{mode}</option>)}
          </select></label>}
          <label>Инструкция пространства<select value={policy.policy.prompt_id} onChange={e => field('prompt_id', e.target.value)}>
            <option value="default">Основная инструкция</option>
            {prompts.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select></label>
          {selectedPrompt && <label>Название инструкции<input value={selectedPrompt.label} required maxLength={100} onChange={e =>
            field('prompts', prompts.map(item => item.id === selectedPrompt.id ? {...item,label:e.target.value} : item))}/></label>}
          <label>Текст инструкции<textarea rows={5} required maxLength={6000} value={selectedPrompt?.system_prompt ?? policy.policy.prompt ?? ''}
            onChange={e => selectedPrompt ? field('prompts', prompts.map(item => item.id === selectedPrompt.id ?
              {...item,system_prompt:e.target.value} : item)) : field('prompt', e.target.value)}/></label>
          <div className="form-actions">
            <button type="button" disabled={busy || prompts.length >= 30} onClick={() => {
              const item = {id:crypto.randomUUID(), label:'Новая инструкция', system_prompt:policy.policy.prompt || 'Отвечайте на вопрос пользователя.'};
              setPolicy(previous => ({...previous,policy:{...previous.policy,prompts:[...prompts,item],prompt_id:item.id}}));
            }}>Добавить инструкцию</button>
            {selectedPrompt && <button type="button" disabled={busy} onClick={() =>
              setPolicy(previous => ({...previous,policy:{...previous.policy,prompt_id:'default',prompts:prompts.filter(item => item.id !== selectedPrompt.id)}}))
            }>Убрать инструкцию</button>}
          </div>
          <label>Голос<select value={settings.voice || ''} onChange={e => setting('voice', e.target.value || null)}>
            <option value="">По умолчанию</option><option value="marina">Марина</option><option value="filipp">Филипп</option>
          </select></label>
          <label>Начальный режим<select value={settings.communication_mode || 'text'} onChange={e => setting('communication_mode', e.target.value)}>
            {Object.entries(modes).map(([key,label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
          <label>Пауза в живом диалоге<select value={settings.live_pause} onChange={e => setting('live_pause', e.target.value)}>
            <option value="normal">1 секунда</option><option value="long">1,6 секунды</option>
          </select></label>
          <label><Toggle checked={settings.review_before_send} onChange={e => setting('review_before_send', e.target.checked)}/> Проверять распознанный текст в голосовом режиме</label>
          <label><Toggle checked={policy.policy.classic_text} onChange={e => field('classic_text', e.target.checked)}/> Показывать текстовый ответ целиком</label>
          <label><Toggle checked={settings.web_search_enabled} disabled={!policy.web_enabled && !settings.web_search_enabled} onChange={e => setting('web_search_enabled', e.target.checked)}/> Использовать Интернет</label>
          {admin && <label>Сообщений в контексте<input type="number" min={0} max={100} step={2} value={settings.history_messages} onChange={e => setting('history_messages', Number(e.target.value))}/></label>}
          {!!policy.bases.length && <fieldset><legend>Подключённые базы знаний</legend>
            <label><Toggle checked={!!policy.policy.rag_enabled}
              onChange={e => field('rag_enabled', e.target.checked)}/> Искать по базам знаний</label>
            {policy.bases.map(base => <label key={base.id}><Toggle checked={policy.policy.base_ids.includes(base.id)}
              onChange={e => field('base_ids', e.target.checked ? [...policy.policy.base_ids, base.id] : policy.policy.base_ids.filter(id => id !== base.id))}/> {base.name}</label>)}
          </fieldset>}
          <button className="primary" disabled={busy}>Сохранить настройки</button>
        </form>}
        {admin && <form className="management-form archive-space" onSubmit={event => {
          event.preventDefault();
          run(() => api(`/management/spaces/${sid}/archive`, {method:'POST', body:JSON.stringify({name:confirmation, revision:space.revision})}));
        }}>
          <h3><TriangleAlert size={20} aria-hidden="true"/>Удаление в архив</h3>
          <p>Разговоров: {space.chats}. Участников: {space.members}. Без активных пространств останутся: {space.affected_members}.</p>
          <label>Введите название «{space.name}»<input value={confirmation} onChange={e => setConfirmation(e.target.value)}/></label>
          <button disabled={busy || confirmation !== space.name}>Архивировать</button>
        </form>}
      </>}
      </>}
  </PanelTabs>;
}
