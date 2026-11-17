// Session ownership and epochs prevent delayed permission/transcription results restarting a stopped mode.
export class ConversationController {
  constructor(onState, {sessionMs=15*60*1000, cooldownMs=400}={}) {
    this.onState=onState; this.sessionMs=sessionMs; this.cooldownMs=cooldownMs;
    this.epoch=0; this.active=false; this.enabled=false; this.phase='off'; this.capture=null;
    this.reason=''; this.timer=null; this.cooldown=null;
  }
  snapshot() { return {active:this.active, enabled:this.enabled, phase:this.phase, reason:this.reason}; }
  publish() { this.onState(this.snapshot()); }
  valid(token) { return token===this.epoch; }
  transition(phase, token=this.epoch) {
    if (!this.valid(token) || !this.active) return false;
    this.phase=phase; this.reason=''; this.publish(); return true;
  }
  start() {
    this.invalidate(); this.active=true; this.enabled=true; this.phase='connecting'; this.reason='';
    const token=this.epoch;
    this.timer=setTimeout(()=>this.onPause?.('Сеанс длится 15 минут. Нажмите «Продолжить», если хотите общаться дальше.'),this.sessionMs);
    this.publish(); return token;
  }
  takeCapture() { const stop=this.capture; this.capture=null; return stop; }
  async attach(stop, token, live=false) {
    if (!this.valid(token) || (live && !this.active)) { await stop(); return false; }
    this.capture=stop; return true;
  }
  invalidate() {
    this.epoch++; clearTimeout(this.timer); clearTimeout(this.cooldown);
    this.timer=null; this.cooldown=null;
    const stop=this.takeCapture();
    if (stop) Promise.resolve(stop()).catch(()=>{});
  }
  pause(reason='Пауза', off=false) {
    this.invalidate(); this.active=false; this.enabled=!off; this.phase=off?'off':'paused'; this.reason=off?'':reason; this.publish();
  }
  review(reason, token) {
    if (!this.valid(token)) return;
    clearTimeout(this.timer); clearTimeout(this.cooldown); this.active=false;
    this.enabled=true; this.phase='paused'; this.reason=reason; this.publish();
  }
  next(token) {
    if (!this.valid(token) || !this.active) return;
    this.transition('cooldown',token);
    this.cooldown=setTimeout(()=>{
      if(this.valid(token)&&this.active)this.onListen?.(token);
    },this.cooldownMs);
  }
}

export class UtteranceDetector {
  constructor({silenceMs=1000, onSpeech=()=>{}, onEnd=()=>{}, onLimit=()=>{}}={}) {
    this.silenceFrames=silenceMs/20; this.onSpeech=onSpeech; this.onEnd=onEnd; this.onLimit=onLimit;
    this.pre=[]; this.votes=[]; this.frames=[]; this.started=false; this.ended=false; this.quiet=0; this.noise=.0005;
  }
  push(frame, voice) {
    if(this.ended)return;
    let power=0;for(const sample of frame)power+=(sample/32768)**2;
    const rms=Math.sqrt(power/frame.length);
    if(!voice)this.noise=Math.min(.02,.98*this.noise+.02*rms);
    const speech=voice&&rms>Math.max(.0025,this.noise*2);
    if(!this.started){
      this.pre.push(frame);if(this.pre.length>13)this.pre.shift(); // 260 ms pre-roll.
      this.votes.push(speech);if(this.votes.length>15)this.votes.shift();
      if(this.votes.filter(Boolean).length<10)return; // At least 200 ms accepted speech.
      this.started=true;this.frames=this.pre;this.pre=[];this.onSpeech();
    }else this.frames.push(frame);
    this.quiet=speech?0:this.quiet+1;
    if(this.frames.length>=1200){this.ended=true;this.onLimit(this.pcm());}
    else if(this.quiet>=this.silenceFrames){this.ended=true;this.onEnd(this.pcm());}
  }
  pcm() {
    const pcm=new Int16Array(this.frames.length*320);
    this.frames.forEach((frame,i)=>pcm.set(frame,i*320));return pcm.buffer;
  }
  clear() { this.ended=true;this.pre=[];this.frames=[];this.votes=[]; }
}
