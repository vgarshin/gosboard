# Live dialog — initial hands-free delivery

Updated 2026-11-15: the third mode «Живой диалог» is implemented alongside Text, button-controlled Voice and secondary classic text delivery. It starts only after a user click, detects an utterance locally, sends bounded PCM to existing STT, uses the existing streaming answer/audio pipeline, waits for playback completion, then resumes listening after 400 ms. Source-based speech retains full citation validation. The microphone is physically stopped during recognition/generation/playback. WebRTC libfvad 2.0.7 (pinned, 15.33 kB WASM) loads from the same origin only on live activation, with 20-ms frames, adaptive energy gating, 200-ms speech acceptance and 260-ms pre-roll. Pause tolerance is per-chat normal 1000 ms / long 1600 ms. No cloud VAD, new recognition SDK or silence polling calls. Owner confirmed the initial live dialog works and requested spoken interruption next. Documentation/manual update comes first; interruption is not implemented yet. Streaming STT remains a later optimization, not a prerequisite.

## Experience

A user enables **Живой диалог / Live dialog** for the current chat. That user gesture requests microphone permission and initializes/resumes the playback AudioContext. Do not enable on page load or automatically restore active recording after reload. Show listening, recognizing, thinking and speaking states, plus a persistent stop/pause control. The normal transcript and citations stay visible. Existing per-chat model, voice, RAG and Internet settings apply unchanged.

Implemented first release: alternating turns. Listen automatically, submit the completed utterance, generate a reply, read it aloud, then listen again. Suspend capture during generation/playback and briefly after playback so the assistant does not transcribe its own loudspeaker output. Speaking over the assistant will not interrupt it in this release; a visible stop control remains available. Automatic spoken interruption is a later increment.

## Detecting a completed question

Use browser-local WebRTC voice activity detection with adaptive energy gating, not silence amplitude alone. A separate AudioWorklet emits 16-kHz PCM frames. The pinned libfvad package has no transitive dependencies or model downloads; ship upstream license, authors and patent notice in public/licenses/libfvad.txt. The upstream wrapper is archived; review its small fixed API and keep version/integrity pinned. Caddy permits wasm-unsafe-eval narrowly for compilation, without enabling JavaScript unsafe-eval or remote script origins.

Current local thresholds below are heuristics, not guarantees that a person has finished speaking:

1. Keep 260 ms of PCM pre-roll in memory to preserve the beginning of words. Ignore isolated clicks/brief noise; require approximately 200 ms of speech to start a turn.
2. While speech continues, extend the turn. After approximately 1,000 ms without speech, mark a candidate end. If speech resumes before submission, cancel that candidate and continue the same turn. Offer a slower-pause setting around 1,600 ms for hesitant speech.
3. Freeze the utterance and run the existing bounded STT request. Ignore an empty result; otherwise persist and submit the recognized text once, then run the usual LLM/source pipeline and automatic TTS. Display the recognized text so mistakes remain visible.
4. Preserve the current 24-second recording boundary. At the limit, pause and offer the transcript for review rather than silently submitting a cut-off question. Set a turn ID to prevent retries or reconnects producing duplicate messages/paid requests.

A pause cannot prove that a person has finished thinking. Initial release accepts this tradeoff, exposes pause sensitivity, and allows stopping/correcting a mistaken turn. Do not add an extra LLM call to classify every pause.

## Delivery increments

### A. Automatic turns using current APIs — implemented

A shared ConversationController owns capture, session epochs, timers and live transitions; stale permission/STT completions cannot restart or submit after pause/stop/mode changes. State machine:

`off → listening → capturing → recognizing → generating → speaking → listening`

Any active state can go to `paused` or `off`. Keep only one active turn per chat. Reuse bounded PCM STT, current text generation and cached TTS; await playback completion before resuming listening. Handle failed/blocked playback by pausing and offering a user action to resume. Recognition still waits for the completed utterance; ordinary LLM text and phrase TTS stream. Grounded audio waits for full citation validation. The browser resumes listening only after all queued playback ends, not merely after receiving the last text event.

### B. Streaming recognition and faster responses — deferred after interruption

Move browser PCM through an authenticated, bounded backend WebSocket to SpeechKit v3 gRPC. Credentials remain server-side; preserve user/session authorization, concurrency and budget reservations. Use partial text only for display. Dispatch the LLM once the complete utterance is finalized by EOU; an individual final fragment is not necessarily the whole question. Start with the default EOU classifier and tune `max_pause_between_words_hint_ms`; the high-sensitivity mode trades precision for speed. Reconcile local VAD and provider EOU through a single turn coordinator, avoiding two competing submit triggers.

Then evaluate streaming LLM text and sentence-sized TTS playback, maintaining sentence order, cancellation and cache keys. Measure end-of-speech → transcript, first text and first audible response separately before choosing latency targets.

### C. Spoken interruption — next implementation priority

Owner feedback on 2026-11-15: live dialog works; spoken interruption is needed. First update project documentation and the in-app manual. This update authorizes documenting the next increment; it does not deliver microphone monitoring during playback.

Preserve Text, button-controlled Voice, secondary classic delivery and the current alternating-turn live behavior. Plan an explicit live-only interruption option with an alternating-turn fallback; final defaults and thresholds must be established during implementation/device testing. Do not silently turn on playback-time microphone monitoring for existing sessions. The UI must truthfully show when input is active.

Required behavior and implementation boundaries:

1. During an explicitly enabled interruptible live session, run local speech detection while the assistant speaks, with browser echo cancellation and playback-reference/echo rejection. Volume or VAD positivity alone is insufficient: assistant speech from speakers must not submit a new question. A headset trial and a real loudspeaker trial are required.
2. On confirmed user speech, immediately stop current/scheduled playback, suppress stale audio events and cancel outstanding synthesis. Cancel ongoing answer generation if it is still running. Preserve pre-roll so the interruption's first words are retained; finish the new utterance using the selected normal/long pause tolerance.
3. Keep one authoritative turn coordinator. Epoch/cancellation ownership must prevent two submissions, overlapping playback, stale transcription replies and automatic resumption of the cancelled turn. No continuous cloud recognition or paid calls merely to monitor silence/playback.
4. Define and expose interruption/history semantics: preserve already saved complete answers, distinguish interrupted playback from interrupted generation, and never feed an unfinished assistant answer into context as an ordinary complete reply. Retain/identify the cancelled question where necessary for follow-ups; avoid silently losing it or duplicating it on retry. Implemented history/export behavior must be documented before release.
5. Keep citation checks unchanged: no unvalidated RAG/Internet speech. Already processed provider work may remain charged; stopping a request cannot guarantee invoice cancellation. Existing cache, budget, ownership and role restrictions apply.
6. Pause/Stop, hidden/offline/session changes, permission loss and failed/blocked playback must release the input detector and microphone. If interruption is off or unsupported, retain the currently working alternating-turn flow and visible button controls.

Acceptance: user speech during early/late playback and during still-running generation; first-word preservation; coughs/clicks/background speech; assistant echo at different speaker volumes; rapid repeat interruptions; delayed STT/TTS/network events; source-enabled replies; budget exhaustion; hidden tabs; mode changes; text/voice/classic regressions. Prove no extra submitted turns from assistant playback and account for cancelled work. Automated fixtures are necessary but do not replace room/device rehearsal.

Streaming SpeechKit recognition is optional later work. It must not be made a dependency of the interruption release solely for convenience.

## Lifecycle, privacy and cost

- Mode starts only through user action and stops on logout, access revocation, chat switch or page exit. Pause when the tab is hidden; require explicit resume. Release microphone tracks when paused/off; do not leave a hidden recorder running.
- Fixed initial limits: idle pause after 60 seconds without accepted speech and active-session ceiling of 15 minutes. Both require explicit resume; only silence tolerance is user-configurable in this release. Silence alone must not create STT/LLM/TTS calls in increment A. Apply existing provider limits, cancellations, local caches and spending caps; remote caches remain TODO.
- Keep raw recording in bounded memory until STT finishes; retain text history and the existing short-lived voice-cache policy. No permanent session recording. Reuse existing source ownership and role restrictions.
- Treat provider errors, lost network, budget exhaustion or permission changes as a paused session with a clear reason; never loop retries or automatically resubmit a question.

## Acceptance before enabling

Test quiet speech, room noise, coughs, long pauses, first-word preservation, 24-second boundary, empty STT and repeated retries. Verify automatic playback on desktop/mobile with the initial gesture, loudspeaker feedback, cancellation, denied permissions, backgrounding, logout/revocation and spending limits. Simulate 5–10 concurrent sessions with mock providers first; then run a separately bounded real microphone/speaker rehearsal. Track false starts, premature endings, end-to-audio latency and cache hits. This operational rehearsal is separate from the deferred answer-quality review.

## References

- [SpeechKit end-of-utterance detection](https://aistudio.yandex.ru/en/docs/speechkit/stt/eou)
- [SpeechKit v3 streaming API](https://aistudio.yandex.ru/ru/docs/speechkit/stt-v3/api-ref/grpc/Recognizer/recognizeStreaming)
- [MDN autoplay and Web Audio](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay)

## Validation and limits of evidence

192 backend tests and 8 Node controller/detector tests pass. Real libfvad tests use the public py-webrtcvad speech fixture plus silence/click/steady-noise controls. Isolated browser checks pass two automatic turns, no microphone during assistant playback, pause tolerance persistence, stop/pause/mode-switch resource release, delayed permissions, hidden tabs, denied permission, empty STT, idle expiry, stale transcripts, budget error without retries and 24-second review. Earlier voice, text/classic and streaming checks pass. Existing mocked 5/10-user backend streaming tests remain green; listening performs local work only. Physical room/device echo and false-turn rates still require an actual venue rehearsal; simulated inputs do not establish them.

References for the selected detector: https://github.com/OzymandiasTheGreat/libfvad-wasm and https://github.com/wiseman/py-webrtcvad (test fixture/license).

Live capture reuses the playback AudioContext unlocked by the initial user gesture, avoiding a new autoplay unlock requirement between turns. Capture owns and releases only its input nodes/tracks; Pause/Stop closes the shared playback context too. Startup cancellation releases acquired tracks even if AudioWorklet loading/resume is delayed. Explicit worklet shutdown prevents idle input processing while the shared context plays an answer.

Production live-dialog verification passed two automatic turns with a temporary regular user and real Yandex STT, LLM and TTS. The browser captured/resampled synthesized Russian speech as microphone input; recognition was not stubbed. Verified local lazy VAD under the deployed CSP, microphone off for playback, no overlapping turns, automatic re-listening, Pause release, persisted mode/pause settings without auto-start, updated manual, prior modes and mobile layout. Existing conservative reservations totalled 90 RUB for this bounded verification; limits were unchanged. Synthetic chat/user/session were removed; usage ledger remains accounted. Physical room/device echo and noise rehearsal is still pending.
