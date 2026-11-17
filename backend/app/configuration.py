"""Administrator-owned catalog and user-owned per-chat selection."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


ReasoningMode = Literal['none','low','medium','high']
ReasoningChoice = Literal['default','disabled','enabled','none','low','medium','high']


def canonical_reasoning(value):
    return {'disabled':'none','enabled':'medium'}.get(value,value)


def allowed_reasoning(model):
    # None preserves legacy on/off capability; an explicit list is authoritative.
    return model.reasoning_efforts if model.reasoning_efforts is not None else ['none','medium'] if model.reasoning_supported else []


def check_reasoning_modes(modes):
    if modes is not None and len(modes)!=len(set(modes)):
        raise ValueError('Режимы рассуждений не должны повторяться')
    return modes


class ModelOption(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1, max_length=100)
    reasoning_supported: bool = False
    reasoning_efforts: list[ReasoningMode] | None = Field(default=None,max_length=4)

    _unique_reasoning = field_validator('reasoning_efforts')(check_reasoning_modes)
    model: str = Field(pattern=r'^[a-zA-Z0-9_./-]{1,150}$')


class PromptOption(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(min_length=1, max_length=6000)


class SourceOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    communication_mode: Literal['text', 'voice', 'live'] | None = None
    live_pause: Literal['normal', 'long'] = 'normal'
    review_before_send: bool = False
    streaming_enabled: bool = False
    auto_speech: bool = False
    rag_enabled: bool = False
    web_search_enabled: bool = False
    knowledge_base_id: str | None = None

    @field_validator('knowledge_base_id')
    @classmethod
    def valid_base_id(cls, value):
        return str(UUID(value)) if value is not None else None

    @model_validator(mode='after')
    def base_required(self):
        if self.communication_mode in {'voice', 'live'}:
            self.streaming_enabled = True
            self.auto_speech = True
        elif self.communication_mode == 'text':
            self.auto_speech = False
            if 'streaming_enabled' not in self.model_fields_set:
                self.streaming_enabled = True
        if self.rag_enabled and self.knowledge_base_id is None:
            raise ValueError('Выберите базу знаний для RAG')
        return self


class ChatOptions(SourceOptions):
    history_messages: int = Field(default=20, ge=0, le=100, strict=True)

    @field_validator('history_messages')
    @classmethod
    def whole_turn_count(cls, value):
        if value % 2:
            raise ValueError('Укажите чётное число сообщений от 0 до 100: вопрос и ответ сохраняются вместе')
        return value

    model_id: str = Field(default='default', max_length=60)
    prompt_id: str = Field(default='default', max_length=60)
    voice: Literal['marina', 'filipp'] | None = None
    reasoning_effort: ReasoningChoice = 'default'


class AppConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=150, pattern=r'^[a-zA-Z0-9_./-]+$')
    model_label: str = Field(min_length=1, max_length=100)
    temperature: float = Field(ge=0, le=1)
    max_tokens: int = Field(ge=1, le=2000)
    context_chars: int = Field(ge=4000, le=40000)
    voice: str = Field(min_length=1, max_length=50)
    tts_model: str = Field(default='general', pattern='^general$')
    tts_speed: float = Field(ge=.5, le=2)
    system_prompt: str = Field(min_length=1, max_length=6000)
    demo_prompt: str = Field(min_length=1, max_length=4000)
    reasoning_supported: bool = False
    reasoning_efforts: list[ReasoningMode] | None = Field(default=None,max_length=4)

    _unique_reasoning = field_validator('reasoning_efforts')(check_reasoning_modes)
    reasoning_effort: ReasoningChoice = 'default'
    models: list[ModelOption] = Field(default_factory=list, max_length=20)
    prompts: list[PromptOption] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def check_catalog(self):
        if self.reasoning_effort != 'default' and canonical_reasoning(self.reasoning_effort) not in allowed_reasoning(self):
            raise ValueError('Reasoning mode requires an explicitly capable model')
        for entries in (self.models, self.prompts):
            ids = [item.id for item in entries]
            if 'default' in ids or len(ids) != len(set(ids)):
                raise ValueError('Catalog IDs must be unique and must not use the reserved default ID')
        for text in [self.system_prompt, *(item.system_prompt for item in self.prompts)]:
            if len(text) + 4000 > self.context_chars:
                raise ValueError('context_chars must fit each system prompt plus one input')
        return self

    def catalog(self):
        return {
            'models': [{'id': 'default', 'label': self.model_label, 'reasoning_efforts': allowed_reasoning(self)},
                       *[{'id': item.id, 'label': item.label, 'reasoning_efforts': allowed_reasoning(item)} for item in self.models]],
            'prompts': [{'id': 'default', 'label': 'Обычный диалог'},
                        *[{'id': item.id, 'label': item.label} for item in self.prompts]],
        }

    def resolve(self, options: ChatOptions):
        models = {item.id: item for item in self.models}
        prompts = {item.id: item for item in self.prompts}
        if options.model_id != 'default' and options.model_id not in models:
            raise ValueError('Выбранная модель недоступна. Выберите модель из списка')
        if options.prompt_id != 'default' and options.prompt_id not in prompts:
            raise ValueError('Выбранная инструкция недоступна. Выберите инструкцию из списка')
        changes = {}
        if options.model_id != 'default':
            model = models[options.model_id]
            changes.update(model=model.model, model_label=model.label, reasoning_supported=model.reasoning_supported, reasoning_efforts=model.reasoning_efforts, reasoning_effort='default')
        if options.prompt_id != 'default':
            changes['system_prompt'] = prompts[options.prompt_id].system_prompt
        if options.voice:
            changes['voice'] = options.voice
        if options.reasoning_effort != 'default':
            selected=self.model_copy(update=changes)
            if canonical_reasoning(options.reasoning_effort) not in allowed_reasoning(selected):
                raise ValueError('Эта модель не поддерживает настройку уровня рассуждений')
            changes['reasoning_effort'] = options.reasoning_effort
        return self.model_copy(update=changes)

    def snapshot(self):
        return self.model_dump(exclude={'models', 'prompts'})
