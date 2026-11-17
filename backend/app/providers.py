import asyncio
import base64
import io
import json
import hashlib
import math
import re
import wave

import httpx
from fastapi import HTTPException
from .configuration import allowed_reasoning, canonical_reasoning
from .web_search import SEARCH_URL, parse_search_response


LLM_URL = 'https://ai.api.cloud.yandex.net/v1/chat/completions'
LLM_PROTOCOL = 'yandex-chat-completions-v1'
STT_URL = 'https://stt.api.cloud.yandex.net/speech/v1/stt:recognize'
TTS_URL = 'https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis'

DEMO_ANSWER = '''Дворянин. Новые школы дают знания для службы: математика, навигация и инженерное дело помогают сделать карьеру. Но учёба требует времени и расходов. Я поддерживаю реформу, если обучение связано с полезной службой.

Боярин. Замена привычного воспитания новыми науками тревожит меня: положение рода уже не гарантирует влияния. Однако государству нужны умелые служилые люди. Я принимаю полезные науки с осторожностью, сохраняя веру и обычай.

Крестьянин. Моей семье прежде всего нужны рабочие руки. Расходы на школы и новые повинности могут ухудшить нашу жизнь, а доступ к учению для нас ограничен. Без облегчения тягот я реформу не поддерживаю.

Иностранец. Подготовленные переводчики, мастера и моряки облегчат торговлю и сотрудничество. Я поддерживаю школы, хотя понимаю опасения людей, для которых перемены означают новые обязанности.

Итог: дворянин — за практическое обучение; боярин — за с оговорками; крестьянин — против без облегчения тягот; иностранец — за. Это демонстрационный текст для проверки интерфейса, а не ответ подключённой модели.'''


class Providers:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, method, url, **kwargs):
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, ValueError):
            # Never return provider response bodies: they may contain prompt data or secrets.
            raise HTTPException(502, 'Сервис Яндекса недоступен или отклонил запрос. Повторите позже') from None

    def headers(self, speech=False):
        key = self.settings.speech_key if speech else self.settings.api_key
        return {'Authorization': f'Api-Key {key}'}

    async def complete(self, messages, config):
        return await self._complete(messages, config)

    async def complete_stream(self, messages, config, on_delta):
        if self.settings.demo:
            text, meta = await self.complete(messages, config)
            for start in range(0, len(text), 40):
                await on_delta(text[start:start + 40])
                await asyncio.sleep(.01)
            return text, meta
        return await self._complete(messages, config, on_delta)

    async def _complete(self, messages, config, on_delta=None):
        if self.settings.demo:
            await asyncio.sleep(.2)
            if '[RETRIEVAL_QUERY_SCHEMA_V1]' in messages[0]['text']:
                return json.dumps({'query':json.loads(messages[-1]['text'])['question']},ensure_ascii=False), {'demo':True}
            if '[RAG_RESPONSE_SCHEMA_V1]' in messages[0]['text']:
                source_data = messages[0]['text'].split('SOURCE_DATA=', 1)[1]
                sources = json.loads(source_data)
                chosen = [next((source for source in sources if source['id'].startswith(kind)),None) for kind in ('D','W')]
                chosen = [source for source in chosen if source]
                text = '\n\n'.join(source['excerpt'][:650]+' ['+source['id']+']' for source in chosen)
                return json.dumps({'supported':True,'answer':text,'used_sources':[source['id'] for source in chosen]},ensure_ascii=False), {'demo': True}
            return DEMO_ANSWER, {'demo': True}
        payload = {
            'model': f'gpt://{self.settings.folder}/{config.model}',
            'stream': False, 'store': False,
            'temperature': config.temperature, 'max_completion_tokens': config.max_tokens,
            'messages': [{'role': message['role'], 'content': message['text']} for message in messages],
        }
        if any(marker in messages[0]['text'] for marker in ('[RAG_RESPONSE_SCHEMA_V1]','[RETRIEVAL_QUERY_SCHEMA_V1]')):
            payload['response_format'] = {'type': 'json_object'}
        if config.reasoning_effort != 'default':
            effort=canonical_reasoning(config.reasoning_effort)
            if effort not in allowed_reasoning(config):
                raise HTTPException(422, 'Модель не поддерживает настройку рассуждений')
            payload['reasoning_effort'] = effort
        headers = {**self.headers(), 'OpenAI-Project': self.settings.folder}
        if on_delta is not None:
            payload['stream'] = True
            return await self._stream_completion(payload, headers, on_delta)
        response = await self.request('POST', LLM_URL, headers=headers, json=payload)
        try:
            result = response.json()
            choice = result['choices'][0]
            message = choice['message']
            text = message['content']
            # Do not cache truncated answers, tool requests or refusal payloads as successful chat replies.
            if choice.get('finish_reason') != 'stop' or message.get('refusal') or message.get('tool_calls'):
                raise ValueError()
            if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                raise ValueError()
            return text, {'provider_usage': result.get('usage', {}), 'provider_api': LLM_PROTOCOL, 'provider_model': result.get('model'), 'provider_response_format': payload.get('response_format', {'type':'text'})['type'], 'provider_reasoning_effort': payload.get('reasoning_effort','default')}
        except (KeyError, IndexError, TypeError, ValueError):
            raise HTTPException(502, 'Модель вернула некорректный или незавершённый ответ') from None

    async def _stream_completion(self, payload, headers, on_delta):
        text, finished, done, usage, model = '', False, False, {}, None
        try:
            async with self.client.stream('POST', LLM_URL, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if len(line) > 65536:
                        raise ValueError()
                    if not line or line.startswith(':'):
                        continue
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        done = True
                        break
                    item = json.loads(data)
                    if item.get('error'):
                        raise ValueError()
                    model = item.get('model') or model
                    usage = item.get('usage') or usage
                    choices = item.get('choices', [])
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get('delta', {})
                    if delta.get('refusal') or delta.get('tool_calls') or delta.get('function_call'):
                        raise ValueError()
                    part = delta.get('content')
                    if part is not None:
                        if not isinstance(part, str) or finished:
                            raise ValueError()
                        text += part
                        if len(text) > 20000:
                            raise ValueError()
                        if part:
                            await on_delta(part)
                    reason = choice.get('finish_reason')
                    if reason is not None:
                        if reason != 'stop':
                            raise ValueError()
                        finished = True
            if not done or not finished or not text.strip():
                raise ValueError()
            return text, {'provider_usage': usage, 'provider_api': LLM_PROTOCOL,
                          'provider_model': model, 'provider_streaming': True,
                          'provider_response_format': payload.get('response_format', {'type':'text'})['type'],
                          'provider_reasoning_effort': payload.get('reasoning_effort','default')}
        except httpx.HTTPError:
            raise HTTPException(502, 'Поток Яндекса прерван. Проверьте историю перед повтором') from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise HTTPException(502, 'Модель вернула некорректный или незавершённый поток') from None

    async def embed(self, text, kind, settings):
        if self.settings.demo:
            values = [0.0] * 256
            for token in re.findall(r'\w{3,}', text.casefold()):
                position = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], 'little') % 256
                values[position] += 1
            if not any(values):
                values[0] = 1
            return values, 'demo-hash-v1'
        model = settings.embedding_doc_model if kind == 'doc' else settings.embedding_query_model
        response = await self.request('POST', 'https://ai.api.cloud.yandex.net/foundationModels/v1/textEmbedding',
                                      headers=self.headers(), json={'modelUri': f'emb://{settings.folder}/{model}', 'text': text})
        try:
            data = response.json()
            vector = [float(value) for value in data['embedding']]
            version = data['modelVersion']
            if not 16 <= len(vector) <= 2048 or not all(math.isfinite(value) for value in vector) or not any(vector):
                raise ValueError()
            if not isinstance(version, str) or not version:
                raise ValueError()
            return vector, version
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, 'Модель эмбеддингов вернула некорректный ответ') from None

    async def search(self,query):
        if self.settings.demo:
            return [{'title':'Образование при Петре I','url':'https://example.com/peter-education',
                     'excerpt':'В 1701 году была учреждена школа математических и навигацких наук. Образование готовило специалистов для государственной службы, армии и флота.'},
                    {'title':'Учебные предметы','url':'https://example.org/peter-schools',
                     'excerpt':'В школах преподавали математику, навигацию и иностранные языки. Это тестовый поисковый фрагмент для локальной проверки интерфейса.'}]
        try:
            async with self.client.stream('POST',SEARCH_URL,headers={'Authorization':'Api-Key '+self.settings.search_key},
                json={'folderId':self.settings.search_folder,'query':{'searchType':'SEARCH_TYPE_RU','queryText':query},
                      'groupSpec':{'groupMode':'GROUP_MODE_FLAT','groupsOnPage':'5','docsInGroup':'1'},'responseFormat':'FORMAT_XML'},timeout=12) as response:
                response.raise_for_status()
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>3*1024*1024:raise ValueError()
            return parse_search_response(json.loads(body))
        except (httpx.HTTPError,ValueError):
            raise HTTPException(502,'Поиск в интернете недоступен. Попробуйте позже или выключите его для разговора') from None

    async def transcribe(self, pcm):
        if self.settings.demo:
            return 'Расскажи об образовательной реформе Петра Первого от лица четырёх сословий.'
        response = await self.request('POST', STT_URL, headers=self.headers(True), content=pcm,
                                      params={'lang': 'ru-RU', 'format': 'lpcm', 'sampleRateHertz': '16000'})
        try:
            data = response.json()
            text = data.get('result', '')
            if 'error_code' in data or not isinstance(text, str):
                raise ValueError()
            if not text.strip():
                raise HTTPException(422, 'Речь не распознана. Попробуйте ещё раз или введите текст')
            return text
        except (ValueError, TypeError):
            raise HTTPException(502, 'Сервис распознавания вернул некорректный ответ') from None

    async def synthesize(self, text, config):
        if self.settings.demo:
            # A short silent WAV tests the audio transport; never impersonate real synthesis.
            stream = io.BytesIO()
            with wave.open(stream, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b'\x00\x00' * 8000)
            return stream.getvalue()
        # Bound utterances explicitly instead of enabling automatic unbounded splitting.
        parts = split_text(text)
        frames = []
        spec = None
        for part in parts:
            payload = {'text': part, 'model': config.tts_model,
                       'hints': [{'voice': config.voice}, {'speed': config.tts_speed}],
                       'outputAudioSpec': {'containerAudio': {'containerAudioType': 'WAV'}}}
            response = await self.request('POST', TTS_URL, headers=self.headers(True), json=payload)
            try:
                chunks = []
                for line in response.text.splitlines():
                    if line.strip():
                        item = json.loads(line)
                        if item.get('error'):
                            raise ValueError()
                        audio = item.get('result', {}).get('audioChunk', {}).get('data')
                        if audio:
                            chunks.append(base64.b64decode(audio, validate=True))
                raw = b''.join(chunks)
                with wave.open(io.BytesIO(raw), 'rb') as wav:
                    current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
                    if spec and spec != current:
                        raise ValueError()
                    spec = current
                    frames.append(wav.readframes(wav.getnframes()))
            except (ValueError, TypeError, KeyError, wave.Error, EOFError):
                raise HTTPException(502, 'Сервис синтеза вернул некорректное аудио') from None
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(spec[0])
            wav.setsampwidth(spec[1])
            wav.setframerate(spec[2])
            wav.writeframes(b''.join(frames))
        return output.getvalue()


def split_text(text, limit=240):
    """Bound utterance size for predictable v3 synthesis billing and compatibility."""
    parts = []
    remaining = text.strip()
    while remaining:
        end = min(limit, len(remaining))
        if end < len(remaining):
            split = max(remaining.rfind('. ', 0, end), remaining.rfind('! ', 0, end), remaining.rfind('? ', 0, end))
            if split >= end // 2:
                end = split + 1
            elif remaining.rfind(' ', 0, end) > 0:
                end = remaining.rfind(' ', 0, end)
        parts.append(remaining[:end].strip())
        remaining = remaining[end:].strip()
    return parts
