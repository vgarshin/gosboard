// Bounded 20-ms/16-kHz PCM frames. Downsampling averages each input interval.
class LiveCapture extends AudioWorkletProcessor {
  constructor(){super();this.frame=new Int16Array(320);this.offset=0;this.phase=0;this.sum=0;this.count=0;this.alive=true;this.port.onmessage=event=>{if(event.data?.stop)this.alive=false;};}
  process(inputs){
    if(!this.alive)return false;
    const data=inputs[0]?.[0];if(!data)return true;
    for(const value of data){
      this.sum+=value;this.count++;this.phase+=16000;
      if(this.phase>=sampleRate){
        const sample=Math.max(-1,Math.min(1,this.sum/this.count));
        this.frame[this.offset++]=sample<0?sample*32768:sample*32767;
        this.phase-=sampleRate;this.sum=0;this.count=0;
        if(this.offset===320){this.port.postMessage(this.frame,[this.frame.buffer]);this.frame=new Int16Array(320);this.offset=0;}
      }
    }
    return true;
  }
}
registerProcessor('live-capture',LiveCapture);
