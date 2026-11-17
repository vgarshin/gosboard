# Operational rehearsal — 2026-11-14

## Automated result

Five- and ten-user concurrent scenarios passed against an isolated local database and mock providers; no production accounts/content or paid calls were used. Each simulated LLM request sleeps 35 ms. Results are functional/concurrency evidence, not cloud-provider or VM capacity measurements.

| Simulated users | Maximum active LLM calls | Message requests, including failure/cancel | Successful | Provider failure | Cancelled |
| --- | --- | --- | --- | --- | --- |
| 5 | 2 | 8 | 6 | 1 | 1 |
| 10 | 2 | 13 | 11 | 1 | 1 |

Observed message HTTP mean/p95 in one local run: 71.5/131.6 ms for five users and 118.0/234.8 ms for ten. These include intentionally failed/cancelled requests and must not be used as real response-time targets.

Verified: all initial turns finish, histories remain private, a provider failure saves no partial turn and the next turn recovers, explicit cancellation leaves no partial turn, no provider call remains active, repeated identical STT is served from each user's own cache, repeated TTS is cached, and no pending reservation remains. Existing source/cancellation tests also cover combined RAG/web and cancellation while searching or waiting for model capacity. Cache metrics count actual decision lookups, not every cache probe.

Run: `PYTHONPATH=backend pytest -q -s tests/test_operations.py` from the application directory with dependencies and antiword installed.

## Real venue acceptance — pending

1. With the administrator, choose a temporary rehearsal budget in the Budget tab and note existing usage. Do not automatically raise it or clear usage. Use a small set of short utterances/answers to bound TTS allowance.
2. Start with two users, then five, then ten using their own allowed accounts and actual venue devices/network. Exercise text, push-to-talk, playback and the four source modes. Keep other users' messages private.
3. Observe Monitoring: active requests, HTTP/API outcomes, cache hits, mean and p95. Capture end-to-end perceived response/playback times on devices as well; HTTP metrics exclude download and browser playback. Record device/browser, source mode and rough room noise, without copying private messages.
4. Play TTS on the real speaker and test microphone recognition, noise and volume. Live dialog is not enabled; push-to-talk remains the recording control.
5. Cancel a slow turn and confirm no partial answer is saved, controls recover and the next turn works. Verify a controlled offline/client-network interruption; inspect whether the last request completed in history before retrying. Never assume cancellation or network failure refunds provider costs.
6. Offline fallback is operational: pause the demonstration and use a prepared local presentation or previously exported non-private material. The app has no offline inference or guaranteed offline history mode. Do not present cached/generated content as a fresh live answer.
7. Record outcomes and restore any temporary budget limit deliberately through the admin UI. Real-device/provider rehearsal remains pending until performed.

Answer-quality review remains in backlog. This rehearsal is about operations, concurrency and audio handling.
