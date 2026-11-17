// POST streaming preserves the same session and CSRF protection as classic chat.
export async function readReplyStream(response, onEvent) {
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(typeof problem.detail === 'string' ? problem.detail : `Ошибка запроса (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', completed = false;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      if (buffer.length > 100000) throw new Error('Некорректный поток ответа');
      let end;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.event === 'error') throw Object.assign(new Error(event.detail), {definitive:true});
        onEvent(event);
        if (event.event === 'done') completed = true;
      }
    }
    if (!completed) throw new Error('Соединение прервано. Проверьте историю перед повтором.');
  } catch (error) {
    if (!error.definitive) error.retryable = true;
    throw error;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export class StreamAudioPlayer {
  constructor(onState, onError) {
    this.context = new (window.AudioContext || window.webkitAudioContext)();
    this.onState = onState; this.onError = onError;
    this.queue = []; this.nodes = new Set(); this.controller = new AbortController();
    this.nextTime = 0; this.stopped = false; this.running = false;
    this.context.onstatechange = () => this.report();
    this.resume();
  }
  resume() { this.context.resume().catch(() => this.report()); }
  report() {
    if (!this.stopped) this.onState({playing: this.nodes.size > 0 || this.queue.length > 0,
      needsGesture: this.context.state === 'suspended' && (this.nodes.size > 0 || this.queue.length > 0)});
  }
  enqueue(url) {
    if (this.stopped) return;
    this.queue.push(url); this.report();
    if (!this.running) this.pump();
  }
  async pump() {
    this.running = true;
    try {
      while (this.queue.length && !this.stopped) {
        // Keep only a short playback lead, rather than decoding a whole long answer.
        while (!this.stopped && this.nextTime - this.context.currentTime > 5) {
          await new Promise(resolve => setTimeout(resolve, 100));
        }
        if (this.stopped) break;
        const response = await fetch(this.queue.shift(), {signal: this.controller.signal});
        if (!response.ok) throw new Error('Аудио недоступно. Текстовый ответ сохранён.');
        const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
        if (this.stopped) break;
        const node = this.context.createBufferSource(); node.buffer = buffer; node.connect(this.context.destination);
        const start = Math.max(this.context.currentTime + .03, this.nextTime);
        this.nextTime = start + buffer.duration; this.nodes.add(node);
        node.onended = () => { this.nodes.delete(node); node.disconnect(); this.report(); };
        node.start(start); this.report();
      }
    } catch (error) {
      if (!this.stopped) { this.onError(error.message); this.stop(); }
    } finally { this.running = false; }
  }
  async waitForIdle(isCurrent=()=>true) {
    while(this.running || this.queue.length || this.nodes.size) {
      if(!isCurrent() || this.stopped) throw new Error('Озвучивание остановлено');
      if(this.context.state==='suspended') throw new Error('Браузер заблокировал звук. Продолжите диалог нажатием кнопки.');
      await new Promise(resolve=>setTimeout(resolve,80));
    }
    if(this.stopped || !isCurrent()) throw new Error('Озвучивание остановлено');
  }
  stop() {
    this.stopped = true; this.queue = []; this.controller.abort();
    for (const node of this.nodes) { node.onended = null; try {node.stop();} catch {} }
    this.nodes.clear(); this.context.close().catch(() => {});
    this.onState({playing: false, needsGesture: false});
  }
}
