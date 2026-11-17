import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import build,{VADMode} from '@ozymandiasthegreat/vad/lib/vad.js';
import {UtteranceDetector} from '../src/conversation.js';
const VAD=await build(await readFile(new URL('../node_modules/@ozymandiasthegreat/vad/dist/libfvad.wasm',import.meta.url)));
function process(frames){
 const vad=new VAD(VADMode.AGGRESSIVE,16000);let ends=0,starts=0;
 const d=new UtteranceDetector({onEnd:()=>ends++,onSpeech:()=>starts++});
 for(const frame of frames)d.push(frame,vad.processFrame(frame)===1);
 vad.destroy();return {ends,starts};
}
const quiet=()=>new Int16Array(320);
test('real WebRTC engine accepts upstream speech and ends after silence',async()=>{
 const raw=await readFile(new URL('../../tests/fixtures/webrtc-speech-8khz.raw',import.meta.url));
 const samples=[];for(let i=0;i<raw.length;i+=2){const v=raw.readInt16LE(i);samples.push(v,v);}
 const frames=Array.from({length:20},quiet);
 for(let i=0;i+320<=samples.length;i+=320)frames.push(new Int16Array(samples.slice(i,i+320)));
 frames.push(...Array.from({length:90},quiet));
 assert.deepEqual(process(frames),{starts:1,ends:1});
});
test('silence, a brief click and steady background noise do not submit',()=>{
 assert.deepEqual(process(Array.from({length:500},quiet)),{starts:0,ends:0});
 const click=Array.from({length:100},quiet);click[20][20]=28000;click[20][21]=-28000;
 assert.deepEqual(process(click),{starts:0,ends:0});
 let seed=44;const noise=Array.from({length:250},()=>Int16Array.from({length:320},()=>{seed=(1664525*seed+1013904223)>>>0;return (seed/2**32-.5)*2000;}));
 assert.deepEqual(process(noise),{starts:0,ends:0});
});
