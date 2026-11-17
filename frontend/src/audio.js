// Capture mono PCM in-browser; no audio files are written to the server's disk.
export async function startCapture(onLimit) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Для микрофона нужен HTTPS или localhost.');
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
  const context = new AudioContext();
  const blocks = [];
  try {
    await context.audioWorklet.addModule('/pcm-worklet.js');
    await context.resume();
    const node = new AudioWorkletNode(context, 'capture');
    const source = context.createMediaStreamSource(stream);
    const mute = context.createGain();
    mute.gain.value = 0;
    node.port.onmessage = ({data}) => blocks.push(data);
    source.connect(node).connect(mute).connect(context.destination);
    let stopped = false;
    const timer = setTimeout(onLimit, 24000);
    return async () => {
      if (stopped) return null;
      stopped = true;
      clearTimeout(timer);
      node.port.onmessage = null;
      source.disconnect(); node.disconnect(); mute.disconnect();
      stream.getTracks().forEach(track => track.stop());
      const rate = context.sampleRate;
      await context.close();
      const count = blocks.reduce((n, block) => n + block.length, 0);
      const samples = new Float32Array(count);
      let offset = 0;
      for (const block of blocks) { samples.set(block, offset); offset += block.length; }
      const size = Math.min(400000, Math.floor(count * 16000 / rate));
      const pcm = new ArrayBuffer(size * 2);
      const view = new DataView(pcm);
      // Average each source interval when downsampling to 16 kHz.
      for (let i = 0; i < size; i++) {
        const start = Math.floor(i * rate / 16000);
        const end = Math.max(start + 1, Math.floor((i + 1) * rate / 16000));
        let sum = 0;
        for (let j = start; j < Math.min(end, samples.length); j++) sum += samples[j];
        const value = Math.max(-1, Math.min(1, sum / (end - start)));
        view.setInt16(i * 2, value < 0 ? value * 32768 : value * 32767, true);
      }
      return pcm;
    };
  } catch (error) {
    stream.getTracks().forEach(track => track.stop());
    await context.close();
    throw error;
  }
}
