"""Public upstream speech fixture, generated fake MediaStream; no production hooks."""
import base64
from pathlib import Path


def install_microphone(page, pcm=None, sample_rate=8000):
    data=base64.b64encode(pcm if pcm is not None else (Path(__file__).parent/'fixtures/webrtc-speech-8khz.raw').read_bytes()).decode()
    page.add_init_script("""(() => {
      const rate="""+str(sample_rate)+""";
      const samples=Uint8Array.from(atob('"""+data+"""'),c=>c.charCodeAt(0));
      const view=new DataView(samples.buffer);const speech=new Float32Array(samples.length/2);
      for(let i=0;i<speech.length;i++)speech[i]=view.getInt16(i*2,true)/32768;
      window.liveTest={input:'speech',opens:0,active:0,stops:0,audioStarts:0,overlaps:0,delay:0,denied:false};
      const originalStart=AudioBufferSourceNode.prototype.start;
      AudioBufferSourceNode.prototype.start=function(...args){
        if(!this.fixtureInput){liveTest.audioStarts++;if(liveTest.active)liveTest.overlaps++;}
        return originalStart.apply(this,args);
      };
      navigator.mediaDevices.getUserMedia=async()=>{
        const input=liveTest.input;
        if(liveTest.delay)await new Promise(resolve=>setTimeout(resolve,liveTest.delay));
        if(liveTest.denied)throw new DOMException('Denied','NotAllowedError');
        const context=new AudioContext();await context.resume();
        const destination=context.createMediaStreamDestination();const node=context.createBufferSource();node.fixtureInput=true;
        const repeat=input==='continuous';
        const buffer=context.createBuffer(1,repeat?speech.length:speech.length+rate*3,rate);
        if(input!=='silence')buffer.getChannelData(0).set(speech,repeat?0:Math.round(rate*.4));
        node.buffer=buffer;node.loop=repeat;node.connect(destination);node.start(context.currentTime+.25);
        liveTest.opens++;liveTest.active++;
        let ended=false;
        for(const track of destination.stream.getTracks()){
          const stop=track.stop.bind(track);
          track.stop=()=>{if(!ended){ended=true;liveTest.active--;liveTest.stops++;try{node.stop();}catch{}context.close().catch(()=>{});}stop();};
        }
        return destination.stream;
      };
    })();""")
