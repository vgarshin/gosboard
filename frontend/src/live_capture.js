import build, {VADMode, VADEvent} from '@ozymandiasthegreat/vad/lib/vad.js';
import wasmUrl from '@ozymandiasthegreat/vad/dist/libfvad.wasm?url';
import {UtteranceDetector} from './conversation';

let factory;
const workletContexts=new WeakSet();
async function detectorClass(){
  if(!factory)factory=fetch(wasmUrl).then(r=>{if(!r.ok)throw Error('Не удалось загрузить определитель речи');return r.arrayBuffer();}).then(build).catch(e=>{factory=null;throw e;});
  return factory;
}
export async function startLiveCapture({silenceMs,onSpeech,onEnd,onLimit,onIdle,onError,isCurrent=()=>true,signal,audioContext}){
  if(!navigator.mediaDevices?.getUserMedia)throw Error('Для микрофона нужен HTTPS или localhost.');
  const VAD=await detectorClass();
  const vad=new VAD(VADMode.AGGRESSIVE,16000);
  let stream,context,source,node,mute,stopped=false,destroyed=false,closing,idle;
  const ownsContext=!audioContext;
  const contextState=()=>{if(!stopped&&context.state!=='running')onError(Error('Аудиозахват приостановлен браузером'));};
  const detector=new UtteranceDetector({silenceMs,onSpeech:()=>{clearTimeout(idle);onSpeech();},onEnd,onLimit});
  async function stop(){
    stopped=true;clearTimeout(idle);detector.clear();
    signal?.removeEventListener('abort',abort);
    context?.removeEventListener('statechange',contextState);
    if(node){node.port.onmessage=null;node.port.postMessage({stop:true});node.disconnect();}
    source?.disconnect();mute?.disconnect();
    stream?.getTracks().forEach(track=>{track.onended=null;track.onmute=null;track.stop();});
    if(!destroyed){vad.destroy();destroyed=true;}
    if(ownsContext&&context&&context.state!=='closed'&&!closing)closing=context.close().catch(()=>{});
    if(closing)await closing;
  }
  const abort=()=>{stop().catch(()=>{});};
  signal?.addEventListener('abort',abort,{once:true});
  try{
    if(signal?.aborted||stopped||!isCurrent())throw new DOMException('Stopped','AbortError');
    stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
    if(signal?.aborted||stopped||!isCurrent())throw new DOMException('Stopped','AbortError');
    context=audioContext||new AudioContext();
    if(!workletContexts.has(context)){await context.audioWorklet.addModule('/live-pcm-worklet.js');workletContexts.add(context);}
    if(signal?.aborted||stopped||!isCurrent())throw new DOMException('Stopped','AbortError');
    let resumeTimer;
    try{await Promise.race([context.resume(),new Promise((_,reject)=>{resumeTimer=setTimeout(()=>reject(Error('Браузер не включил аудио. Нажмите «Продолжить».')),3000);})]);}
    finally{clearTimeout(resumeTimer);}
    if(signal?.aborted||stopped||!isCurrent())throw new DOMException('Stopped','AbortError');
    context.addEventListener('statechange',contextState);
    node=new AudioWorkletNode(context,'live-capture');source=context.createMediaStreamSource(stream);mute=context.createGain();mute.gain.value=0;
    node.port.onmessage=({data})=>{
      if(stopped||detector.ended)return;
      try{const result=vad.processFrame(data);if(result===VADEvent.ERROR)throw Error('Ошибка определения речи');detector.push(data,result===VADEvent.VOICE);}
      catch(e){onError(e);}
    };
    stream.getTracks().forEach(track=>{track.onended=()=>onError(Error('Микрофон отключён'));track.onmute=()=>onError(Error('Микрофон недоступен'));});
    source.connect(node).connect(mute).connect(context.destination);
    idle=setTimeout(onIdle,60000);
    return stop;
  }catch(e){await stop();throw e;}
}
