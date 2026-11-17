"""Versioned non-secret runtime configuration with optimistic concurrency."""
import json
import time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .configuration import AppConfig, ChatOptions


class ConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)
    config: AppConfig


class LiveConfiguration:
    def __init__(self, store, bootstrap):
        self.store = store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS live_config_versions (
                    revision INTEGER PRIMARY KEY, config TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS live_config_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM live_config_state WHERE id=1').fetchone():
                initial = AppConfig.model_validate_json(Path(bootstrap).read_text())
                db.execute('INSERT INTO live_config_versions VALUES (1,?,?,?)',
                           (initial.model_dump_json(), 'bootstrap', time.time()))
                db.execute('INSERT INTO live_config_state VALUES (1,1)')

    def read(self):
        with self.store.connection() as db:
            row = db.execute('''SELECT v.* FROM live_config_versions v JOIN live_config_state s
                                ON v.revision=s.revision WHERE s.id=1''').fetchone()
        return {'revision': row['revision'], 'config': json.loads(row['config'])}

    def current(self):
        return AppConfig.model_validate(self.read()['config'])

    def versions(self):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute('SELECT revision,actor,created FROM live_config_versions ORDER BY revision DESC LIMIT 100')]

    def version(self, revision):
        with self.store.connection() as db:
            row = db.execute('SELECT revision,config FROM live_config_versions WHERE revision=?',(revision,)).fetchone()
        if not row:
            raise HTTPException(404, 'Версия не найдена')
        return {'revision':row['revision'], 'config':json.loads(row['config'])}

    def save(self, update, actor):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = db.execute('SELECT revision FROM live_config_state WHERE id=1').fetchone()[0]
            if revision != update.expected_revision:
                raise HTTPException(409, 'Конфигурация уже изменена. Загрузите актуальную версию перед сохранением')
            candidate = update.config.model_copy(update={'version': f'live-{revision + 1}'})
            # Do not strand existing conversations by removing their selected catalog entries/capabilities.
            for row in db.execute('SELECT settings FROM chat_settings'):
                try:
                    candidate.resolve(ChatOptions.model_validate_json(row['settings']))
                except ValueError:
                    raise HTTPException(409, 'Существующий разговор использует удаляемую модель/инструкцию или режим рассуждений. Сначала измените настройки разговора') from None
            db.execute('INSERT INTO live_config_versions VALUES (?,?,?,?)',
                       (revision + 1, candidate.model_dump_json(), actor, time.time()))
            db.execute('UPDATE live_config_state SET revision=? WHERE id=1', (revision + 1,))
            db.execute("DELETE FROM cache WHERE kind='llm'")
            # Full content stays in restricted configuration revisions, not the audit listing.
            db.execute('INSERT INTO audit_events(actor,action,target,details,created) VALUES (?,?,?,?,?)',
                       (actor, 'configuration.save', str(revision + 1), json.dumps({'previous_revision': revision}), time.time()))
        return self.read()

    def prompts(self):
        live = self.read()
        config = AppConfig.model_validate(live['config'])
        with self.store.connection() as db:
            counts = {}
            for row in db.execute('SELECT settings FROM chat_settings'):
                pid = ChatOptions.model_validate_json(row['settings']).prompt_id
                counts[pid] = counts.get(pid, 0) + 1
        items = [{'id':'default','label':'Обычный диалог','system_prompt':config.system_prompt,'builtin':True},
                 *[{**prompt.model_dump(),'builtin':False} for prompt in config.prompts]]
        return {'revision':live['revision'],'prompts':[{**item,'used_by_chats':counts.get(item['id'],0)} for item in items]}

    def change_prompt(self, expected_revision, actor, pid=None, label=None, text=None, delete=False):
        from uuid import uuid4
        from pydantic import ValidationError
        live = self.read()
        if live['revision'] != expected_revision:
            raise HTTPException(409, 'Список промптов уже изменён. Обновите список перед сохранением')
        config = live['config']
        if delete:
            if pid=='default':
                raise HTTPException(409, 'Основной промпт нельзя удалить. Можно изменить его текст')
            if not any(item['id']==pid for item in config['prompts']):
                raise HTTPException(404, 'Промпт не найден')
            config['prompts'] = [item for item in config['prompts'] if item['id']!=pid]
        elif pid=='default':
            if label != 'Обычный диалог':
                raise HTTPException(422, 'Название основного промпта не изменяется')
            config['system_prompt'] = text
        elif pid:
            if not any(item['id']==pid for item in config['prompts']):
                raise HTTPException(404, 'Промпт не найден')
            config['prompts'] = [{**item,'label':label,'system_prompt':text} if item['id']==pid else item for item in config['prompts']]
        else:
            pid = 'prompt-' + uuid4().hex
            config['prompts'].append({'id':pid,'label':label,'system_prompt':text})
        try:
            candidate = AppConfig.model_validate(config)
        except ValidationError:
            raise HTTPException(422, 'Проверьте длину промпта, размер контекста и лимит в 30 промптов') from None
        self.save(ConfigurationUpdate(expected_revision=expected_revision,config=candidate),actor)
        return {**self.prompts(),'prompt_id':pid}
