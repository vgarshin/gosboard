"""Bounded reply delivery; no incomplete text is persisted as conversation history."""
import asyncio
import json
import re


def provisional_answer(raw):
    """Read only a top-level JSON answer string, including a safe incomplete prefix."""
    decoder = json.JSONDecoder()
    pos = 0
    try:
        pos = len(raw) - len(raw.lstrip())
        if raw[pos] != '{':
            return ''
        pos += 1
        while True:
            while raw[pos].isspace() or raw[pos] == ',':
                pos += 1
            key, end = decoder.raw_decode(raw, pos)
            pos = end
            while raw[pos].isspace():
                pos += 1
            if raw[pos] != ':':
                return ''
            pos += 1
            while raw[pos].isspace():
                pos += 1
            if key != 'answer':
                _, pos = decoder.raw_decode(raw, pos)
                continue
            if raw[pos] != '"':
                return ''
            pos += 1
            result = ''
            while pos < len(raw):
                char = raw[pos]
                if char == '"':
                    return result
                if char == '\\':
                    end = pos + (6 if raw[pos + 1] == 'u' else 2)
                    # Decode escapes together, so surrogate pairs cannot leak to UTF-8 output.
                    if raw[pos:pos + 2] == '\\u' and len(raw) >= end:
                        value = int(raw[pos + 2:end], 16)
                        if 0xD800 <= value <= 0xDBFF:
                            end += 6
                    if end > len(raw):
                        return result
                    decoded = json.loads('"' + raw[pos:end] + '"')
                    if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
                        return result
                    result += decoded
                    pos = end
                elif ord(char) < 32:
                    return result
                else:
                    result += char
                    pos += 1
            return result
    except (ValueError, IndexError, TypeError):
        return locals().get('result', '')


class ReplyStream:
    def __init__(self, synthesize):
        self.events = asyncio.Queue(maxsize=32)
        self.phrases = asyncio.Queue(maxsize=80)
        self.synthesize = synthesize
        self.audio_task = None
        self.task = None
        self.muted = False
        self.speech_config = None
        self.auto_speech = False
        self.pending = ''
        self.spoken_chars = 0
        self.segment_count = 0

    async def emit(self, event, data):
        await self.events.put({'event': event, **data})

    def feed(self, text, final=False):
        if self.muted or not self.auto_speech:
            return
        room = max(0, 6000 - self.spoken_chars)
        text = text[:room]
        self.spoken_chars += len(text)
        self.pending += text
        while self.pending:
            minimum = 80 if not self.segment_count else 160
            boundary = next((m.end() for m in re.finditer(r'[.!?](?:\s+|$)|\n\n', self.pending)
                             if minimum <= m.end() <= 240), None)
            if boundary is None and len(self.pending) >= 240:
                boundary = self.pending.rfind(' ', 0, 240)
                boundary = boundary if boundary > 0 else 240
            if boundary is None:
                if not final:
                    break
                boundary = len(self.pending)
            phrase, self.pending = self.pending[:boundary].strip(), self.pending[boundary:].lstrip()
            if phrase:
                self.phrases.put_nowait(phrase)
                self.segment_count += 1

    async def audio_worker(self):
        try:
            async with asyncio.timeout(180):
                await self._audio_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.muted = True
            await self.emit('audio_error', {'detail': 'Озвучивание остановлено: сервис недоступен или достигнут лимит. Текстовый ответ продолжается.'})

    async def _audio_loop(self):
        while True:
            phrase = await self.phrases.get()
            if phrase is None or self.muted:
                return
            result = await self.synthesize(phrase, self.speech_config)
            await self.emit('audio', result)

    def stop_audio(self):
        self.muted = True
        if self.audio_task:
            self.audio_task.cancel()
