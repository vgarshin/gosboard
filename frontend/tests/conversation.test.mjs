import test from 'node:test';
import assert from 'node:assert/strict';
import {ConversationController,UtteranceDetector} from '../src/conversation.js';
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const speech=()=>new Int16Array(320).fill(2200);
const quiet=()=>new Int16Array(320);

test('short noise is ignored; accepted speech retains pre-roll and finalizes once',()=>{
 let started=0,ends=[];const d=new UtteranceDetector({onSpeech:()=>started++,onEnd:pcm=>ends.push(pcm)});
 for(let i=0;i<50;i++)d.push(quiet(),false);
 for(let i=0;i<4;i++)d.push(speech(),true);
 for(let i=0;i<60;i++)d.push(quiet(),false);
 assert.equal(started,0);assert.equal(ends.length,0);
 for(let i=0;i<20;i++)d.push(speech(),true);
 for(let i=0;i<49;i++)d.push(quiet(),false);assert.equal(ends.length,0);
 d.push(quiet(),false);assert.equal(ends.length,1);assert.equal(started,1);
 assert.equal(new Int16Array(ends[0]).length,73*320);
 for(let i=0;i<80;i++)d.push(speech(),true);assert.equal(ends.length,1);
});
test('long pause tolerates thinking gap and renewed speech cancels silence candidate',()=>{
 let count=0;const d=new UtteranceDetector({silenceMs:1600,onEnd:()=>count++});
 for(let i=0;i<20;i++)d.push(speech(),true);
 for(let i=0;i<60;i++)d.push(quiet(),false);assert.equal(count,0);
 for(let i=0;i<15;i++)d.push(speech(),true);
 for(let i=0;i<79;i++)d.push(quiet(),false);assert.equal(count,0);
 d.push(quiet(),false);assert.equal(count,1);
});
test('duration cap produces review payload with bounded memory, never normal submit',()=>{
 let limits=[],ends=0;const d=new UtteranceDetector({onLimit:pcm=>limits.push(pcm),onEnd:()=>ends++});
 for(let i=0;i<2000;i++)d.push(speech(),true);
 assert.equal(limits.length,1);assert.equal(ends,0);assert.equal(limits[0].byteLength,1200*320*2);
 d.clear();assert.equal(d.frames.length,0);
});
test('pause invalidates late permissions and queued restarts and releases capture',async()=>{
 const states=[];const c=new ConversationController(s=>states.push(s),{cooldownMs:5,sessionMs:1000});
 let stopped=0,listened=0;c.onListen=()=>listened++;
 const token=c.start();await c.attach(async()=>stopped++,token,true);c.next(token);c.pause('user pause');
 await wait(15);assert.equal(stopped,1);assert.equal(listened,0);assert.equal(c.valid(token),false);
 assert.equal(await c.attach(async()=>stopped++,token,true),false);assert.equal(stopped,2);
 assert.equal(states.at(-1).phase,'paused');c.pause('',true);
});
test('shared controller also owns manual capture without enabling live microphone',async()=>{
 const c=new ConversationController(()=>{});let stopped=0;
 assert.equal(await c.attach(async()=>stopped++,c.epoch),true);assert.equal(c.active,false);
 c.invalidate();await wait(0);assert.equal(stopped,1);assert.equal(c.capture,null);
});
test('review holds transcript without automatic restart; session limit pauses once',async()=>{
 const c=new ConversationController(()=>{},{sessionMs:10,cooldownMs:5});let pauses=0;
 c.onPause=reason=>{pauses++;c.pause(reason);};const token=c.start();c.review('duration',token);
 await wait(20);assert.equal(pauses,0);assert.equal(c.valid(token),true);assert.equal(c.active,false);
 c.start();await wait(25);assert.equal(pauses,1);assert.equal(c.active,false);c.pause('',true);
});
