# Implementation status: three modes including hands-free live dialog

Documentation reorganized for the standalone repository: 2026-11-17. Feature verification below records the existing delivery; no application deployment is part of this repository preparation.

## Next priority confirmed by the owner

Owner confirms the current live dialog works. Spoken interruption is the next implementation priority; this delivery updates project documentation and the app manual first. It is not implemented yet. Current live dialog still disables the microphone while answering. Use Pause/Stop sound, resolve any retained draft, then Resume to speak a clarification. The upcoming feature must reject loudspeaker echo, cancel stale work safely, preserve first words/history semantics and keep every existing mode. Streaming STT is a later optimization, not a prerequisite. See ../plans/LIVE_DIALOG_PLAN.md section C.

## Administrative chat review

Owner-authorized change on 2026-11-15: enabled admins can read all users' messages through a paginated owner-filtered read-only viewer. Message reads are audited without body text. Editors retain only foreign title/settings access, and normal owner-scoped mutation/export/audio routes remain unchanged. The privacy notice discloses administrator message access.

## Current delivery

Internet search can enrich up to two result URLs with bounded public HTML/plain-text passages. Public-only DNS-pinned connections, checked redirects, limited bytes/time/concurrency, private page caches and explicit snippet fallback preserve a small footprint. Source popups distinguish page excerpts from search snippets and show page-fetch dates; saved excerpts/URLs remain in private chat history and exports. Existing combined RAG/web, history-aware queries, budget controls and model policies remain in place. Streaming text and phrase audio are available through the three per-chat communication modes, with independent sound cancellation, grounded-answer validation before speech, local phrase caches and existing budget controls. New UI-created text chats stream by default; classic whole-answer delivery remains a secondary text setting. Spoken interruption, streaming recognition, GigaChat and remote caching remain later work; compaction and broad quality review are backlog.

## Live dialog

Third per-chat mode «Живой диалог» starts only on an explicit click. Local WebRTC VAD with normal/long silence tolerance ends questions; existing STT, streaming LLM/RAG/Internet and automatic TTS answer them. Microphone tracks stop during remote operations and playback and reopen only after audio drain/cooldown. Pause/Stop and session epochs prevent stale operations restarting capture. Silence makes no provider calls. Duration-limit questions require review; errors, idle, hidden tab and session ceiling pause with explicit resume. Text, button-controlled voice and classic delivery remain available. Streaming STT and spoken interruption remain future work; real room/device rehearsal is pending.

192 backend tests, 8 controller/VAD tests and isolated browser lifecycle/automatic-turn/failure/regression checks pass. Lazy detector assets are 15.33 kB WASM plus about 5 kB JS, served locally. Full user manual updated. See ../plans/LIVE_DIALOG_PLAN.md for thresholds and limits of test evidence.

## Built-in user manual

All signed-in roles have a question-mark icon at the upper right. It opens a Russian manual over the current chat, covering chat/history, Internet/RAG and source links, text/voice controls and limits, role-dependent settings and troubleshooting. The manual uses an indexed editable HTML source under guides/ with a reproducible sync script. Keyboard and mobile browser checks pass; no provider calls are made by help. The manual now also covers the implemented initial hands-free live dialog and its limits.

## Communication modes

The prominent delivery switches are replaced by a per-chat Text / Voice selector. Voice sends recognized text exactly once after an explicit recording stop and plays the reply automatically; optional review and cancel are available. Empty/failed recognition and duration-limit recordings are never auto-submitted. New UI-created text chats stream by default; classic delivery is a secondary text-mode setting. Legacy preferences are preserved and normalized on changes. The third live mode now provides silence detection and automatic re-listening; spoken interruption remains future work.

190 backend tests pass; isolated desktop/mobile browser checks cover auto-send/audio, draft preservation, cancel without STT/LLM, optional review and persistence, empty/failed recognition, duration-limit review, legacy settings and classic delivery.

## Streaming verification

188 backend tests pass, including 23 streaming tests for incremental UTF-8 provider output, truncation rejection, cache/idempotency, provisional sourced answers, all RAG/Internet combinations, early speech, independent mute, cancellation, role revocation, conservative budget states and mocked 5/10-user concurrency. Frontend production build, live HTTPS provider/source checks and production desktop/mobile browser playback/persistence checks pass; browser instrumentation confirms audio starts while text is still arriving. See operations/RUNBOOK.md and the Russian guide in guides/.

## Implemented

- React chat interface with conversation selection, persistent private text history, exact demo prompt, basic responsive layout, and administrator configuration/usage view.
- Python/FastAPI backend with SQLite WAL, Yandex OAuth authorization code + PKCE, primary-email allowlist, opaque revocable sessions, CSRF/origin checks, and chat/audio ownership enforcement.
- YandexGPT adapter, push-to-talk browser PCM capture with editable SpeechKit transcription, bounded SpeechKit v3 TTS synthesis, and explicit play/stop/replay.
- User-scoped local response/STT/TTS caches, 24-hour generated-audio expiration, cache size cap, request deduplication, generation cancellation, bounded provider concurrency/timeouts, and conservative global budget reservations.
- Docker Compose, Caddy HTTPS configuration, runtime dependency lock, external environment template, non-secret admin configuration, online SQLite backup helper, and operating runbook.

## VM preparation

- Selected host: `aitalks-test-01` from the owner's SSH configuration.
- Observed capacity: 2 vCPU, about 4 GB RAM, 40 GB root disk.
- Docker Engine/Compose installed; application source is at `/opt/aitalks/app`.
- Production is live at https://aitalks.tech; both containers are healthy. The previous loopback demo is stopped, with its volumes preserved. www.aitalks.tech redirects to the canonical address.
- Owner-supplied credentials/allowlists were transferred directly to `/etc/aitalks/production.env` (0600), without printing values. Current provider tariffs were reviewed, the review flag enabled, and configured spending caps preserved. The local environment file also has mode 0600.

## Verification

- The initial 19 focused backend tests passed: history persistence, context bounds, user isolation, auth/CSRF/allowlist/admin rules, OAuth/PKCE, idempotency, caches/expiry, request size limits, budget reservations/restart, cancellation, backup contents, and mocked provider contracts.
- React production build, Compose validation, and Caddy configuration validation passed.
- Local development browser smoke test passed: demo login, prompt, generation, TTS transport, reload/history, chat creation, microphone capture/STT, admin view, and mobile navigation.
- Python runtime dependency audit and npm audit reported no known vulnerabilities at the time of checking.
- Final browser smoke test against the VM Docker/Caddy deployment passed all nine workflow checks with no browser or CSP errors. This caught and resolved the demo form-Origin issue and a draft-clearing race during chat creation/history loading; origin and CSP protections remain enabled.

## Production activation checks

- HTTPS returns 200 with a valid Let’s Encrypt certificate; HTTP redirects to HTTPS. The www certificate also validates and redirects to the canonical domain.
- Public login page and Yandex OAuth/PKCE redirect checked in a fresh browser. The owner subsequently confirmed the live site, STT, and TTS work well; this is user-reported functional validation, separate from automated checks.
- Demo mode is off; anonymous history access returns 401.
- Three small live checks passed: YandexGPT response, valid SpeechKit WAV synthesis, and recognition of that generated audio. Observed single-run provider times: LLM 0.51 s, TTS 0.12 s, STT 0.36 s. These are smoke checks, not latency benchmarks or microphone/venue trials. Conservative reservations were recorded through the application budget ledger.

## Remaining before event use

1. Extend validation to multi-user allowed-email access, history isolation, and representative event conversations.
2. Rehearse real microphone capture, conversation history, audio playback/cache reuse, and cancellation in the authenticated production UI.
3. Select the voice and confirm real costs, representative latency, output duration, and event mechanics. Credential-free demo fixtures remain available only in explicitly enabled local demo mode.
4. Monitor daily local/S3 backup runs and retention. Live upload/download/checksum verification and isolated actual-snapshot restore rehearsals have passed.
5. Prepare an approved downloadable event recording and rehearse offline playback with actual venue equipment.

Stage 1 settings and stage 2A shared RAG are delivered. Optional web search, combined-source citations, Cloud.ru and remote caching remain planned. Stage 0 buffers complete model and audio responses; streaming latency targets are not yet achieved or claimed.

## First stage 1 increment delivered

- Per-chat model and instruction selection is deployed, including YandexGPT and YandexGPT Lite plus the council preset. Existing model, voice, and prompt defaults on the VM were preserved.
- Selections persist across reload/restart. Immutable effective configuration snapshots and source-control flags are recorded for each new answer; later settings changes do not rewrite old answers. Existing chats retain default behavior. New tables preserve compatibility with the original chat table.
- At this initial stage 1 delivery, RAG/web controls and the global-base selector were visible but unavailable. The API rejects attempts to enable unimplemented retrieval, so no hidden retrieval calls or charges occur.
- 31 automated tests pass, including migration, settings isolation/persistence, generation/cache separation, immutable snapshots, backup integrity, restored history/write access, and fake-S3 download verification/rotation safeguards. The browser smoke test covers persisted selections and disabled source switches alongside the original chat/voice flow.
- YandexGPT Lite passed a live short-response check within the existing application budget ledger. Public HTTPS and service health checks pass.
- A real production snapshot passed an isolated network-disabled restore rehearsal, including existing history, a synthetic saved turn, two-user isolation, and per-chat settings. The source database was untouched. A predeployment archive also has a checksum-verified temporary off-VM copy outside the synced project.
- Daily local backup/restore verification is enabled by systemd at 03:15 UTC plus up to five minutes jitter, retaining seven archives per category. SDK, service/timer, and protected external backup configuration are installed.
- Automated off-VM S3 delivery is enabled for `aitalks-data`. The real systemd job passed upload/download SHA-256 verification and isolated restore checks, reporting `off_vm: true`. The daily timer remains active. See BACKUPS.md.

Remaining stage 1 work includes broader interface refinement and measured 5–10-user capacity tests. Stage 2A RAG was subsequently delivered below; web retrieval and stage 3 integrations remain pending.

The daily job also passed after an unchanged backend image was retagged by a frontend build. Restore image selection now verifies matching application/dependency fingerprints before accepting a replacement image index.

Application and backup credentials are consolidated in the existing local `.env`, deployed as `/etc/aitalks/production.env`. The backup service now reads that shared file; Compose injects only required variables (including S3 access for stage 2A originals), and the backup service unsets application credentials. Separate backup credential files/templates are retired after verified migration.

## Live S3 activation

The owner supplied S3 credentials through the shared `.env`; they were transferred without printing values. `BACKUP_LOCAL_ONLY=false` is saved in both the editing source and deployed environment. Upload and download to the selected bucket passed with matching SHA-256. The storage response capitalized custom metadata as `Sha256`; the checker now normalizes metadata keys, with all eight focused backup tests passing. The scheduled service reports success and the timer remains active. This initial backup activation required no application-container S3 credential injection or public bucket policy change; stage 2A subsequently added backend S3 access for private documents.

## Stage 2A: shared RAG and citations delivered

- Deployed on `aitalks-test-01` at https://aitalks.tech with existing histories, model/voice configuration, and spending limits preserved. Caddy still manages HTTPS certificates.
- FAISS IndexFlatIP on local SSD; versioned serialized index, chunks, embedding vectors and catalog are stored atomically in SQLite. No additional database service. LangChain page-aware recursive splitting uses 1,200 characters / 180 overlap. Paired Yandex document/query embeddings record actual versions; model drift prompts explicit reindexing.
- Admin-only shared-base creation, PDF/TXT uploads (10 MB), durable background indexing, progress/errors, retry/reindex, and deletion. Every allowed user can select any ready global base. Personal chat histories remain isolated.
- Persistent per-chat RAG switch and base selection. Turning RAG off makes no retrieval/embedding calls. Web search remains disabled. Answers validate supplied citation IDs and link to authenticated highlighted chunks and PDF page originals; no public S3 links. No-evidence answers use a fixed insufficient-information message. Source-ID validation does not prove factual entailment; linked passages remain reviewable.
- Private originals in `aitalks-data/aitalks/documents/`, bounded local originals cache, persisted document embeddings, one-hour per-user query cache, and source-version-sensitive answer cache. Deletion tombstones access immediately and queues S3 cleanup; snapshot retention still applies.
- Seeded **Государев совет — материалы игры** with the provided 19-page boyar book (36 indexed chunks). A live Yandex answer produced three valid citations on pages 7, 7 and 3 in approximately 2.8 seconds; temporary test chat/session were removed. Original document SHA-256 matched.
- 51 automated tests pass, including RAG permissions/shared reads, persistence, deduplication, source deletion, bad citations, query-model drift, corrupt-index detection, document failures/retry, page references, and isolated FAISS restore validation. Runtime dependency audit found no known vulnerabilities. Frontend production build passed.
- Local browser checks passed with no JavaScript errors: upload/indexing, selected base, cited answer, highlighted source, authenticated original, persisted RAG settings, RAG off, and mobile layout. Existing chat/voice/browser checks passed as well.
- Authenticated production browser checks also passed over public HTTPS: citation/new-tab viewer, original fetch after local cache eviction, persisted RAG settings, and refusal of an unrelated stock-price question, with no JavaScript errors. Synthetic chats and temporary sessions were removed.
- A fresh production snapshot passed isolated network-disabled restore checks, including FAISS checksum/dimension/count validation, existing history, a synthetic saved turn and user isolation. S3 upload/download checksum verification passed; daily timer remains active. Source originals are durable separately and must not receive the backup-prefix expiration policy.

Current format limits: text PDFs and UTF-8 TXT; scans need external OCR. No DOCX/OCR processing, web search, hybrid keyword retrieval, reranker, streaming upgrade, or 5–10-user capacity claim yet. Next: representative book-grounding evaluation and then the optional web adapter plus combined-source contract.


## Owner feedback delivery — 2026-11-14

Owner confirmed RAG, source links and uploads work. Added bounded DOCX paragraph/table text extraction and legacy DOC extraction through antiword in the isolated parser. PDF/TXT remain supported. Word sources use chunk references without invented page numbers. Original formats are retained in private S3.

Source links now open a modal over the current chat, including links in the shared-base browser; Escape/backdrop/close return to the chat, keyboard focus is trapped/restored, and direct copied source URLs remain available. Originals download from the popup. RAG/web switches now use sliding oval controls, and the base selector and switches sit inside the message composer below the draft. Internet remains disabled pending its adapter.

57 backend tests and production frontend build pass. Browser checks cover popup-without-new-tab, exact source/original, Escape/focus return, persisted settings, composer controls, mobile layout, and existing voice/chat workflow. New tests cover real binary DOC, DOCX paragraph/table extraction and invalid/oversized/XML-entity documents before embedding.

The updated development plan queues three roles, editor model/voice/reasoning configuration, admin user management and admin RAG chunk/top-K controls. Confirmed: editors can see other users' chat titles/settings only; messages stay private. Effort means provider-supported reasoning effort. Those role/admin features are planned, not enabled in this delivery; the existing admin/user access rules remain in force.


Production verification for this delivery passed: DOC and DOCX uploaded/indexed with live Yandex embeddings, exact source text and byte-identical originals; temporary test base/session removed. Public HTTPS browser verified popup citations without a new tab, Escape/focus restoration, mobile popup, authenticated download, and persistent RAG settings with no JavaScript errors. Both services are healthy. Postdeployment local/S3 backup and isolated FAISS/history restore checks passed.


## Roles, editorial controls and RAG tuning — 2026-11-14

Implemented persisted user/editor/admin roles with one-time bootstrap migration, admin account access/role management, immediate session revocation, in-flight request cancellation, last-signed-in-admin protection and audit records. Editors manage shared bases and change model/voice/reasoning settings using a chat title/settings-only endpoint; message/audio/delete ownership remains enforced for every role. Existing histories, selections, admins and content are retained.

Added admin per-base chunk size/overlap and separate limits for distinct source files and chunks. Chunk changes explicitly confirm paid reindexing; defaults preserve existing indexes. Model/voice cache separation and native reasoning mode capability checks are implemented; current catalog models leave reasoning unavailable until exact provider support is verified. Management UI and role browser checks pass, including mobile layout. Web search remains disabled pending its adapter.


Role delivery verification: 63 backend tests pass, including last-signed-in-admin protection, metadata-only editor access, source-file/chunk limits, real rechunk/reindex behavior, reasoning payload mapping, voice use and in-flight revocation. Existing chat/voice and RAG-popup browser rehearsals pass. Live HTTPS management UI passed on desktop/mobile with no JavaScript errors; temporary production fixtures verified all three roles, model/voice policy, cross-user message denial, admin tuning and session revocation. Existing accounts/chats/content were unchanged by these checks. Postdeployment S3 upload/download checksum and isolated restore passed with `access_roles: preserved`, four FAISS indexes, existing history and a saved synthetic turn. Synthetic accounts, chats, sessions and test base were cleaned up.


## Navigation cleanup and live configuration — 2026-11-14

Implemented explicit Add user creation, duplicate rejection and row-level access sliders with error rollback. Header navigation is book/content, admin users, and editor/admin configuration gear; model badge and duplicate shield/read-only screen removed. Editorial controls have moved out of the chat into configuration; admin RAG policy has moved out of the book/content browser.

Added validated runtime configuration revisions in SQLite, importing the deployed bootstrap file only once. Editors/admins edit per-chat model/instruction/voice/reasoning and global defaults/catalog/generation settings without restart. Save conflicts reject stale revisions; removal of in-use catalog entries is blocked. Historical versions load as drafts for explicit re-save. Running and historical responses retain their captured configuration. Restore checks verify active revisions, roles and indexes. Credentials and operational spending limits remain in the protected environment.

69 backend tests pass; browser checks cover the revised user/role/access flow, live global/preset changes without restart, reload persistence, version restoration, mobile configuration, and existing chat/voice/source-popup workflows. Search-API credential instructions and reserved unified-env fields are prepared; web search remains disabled.


Production UI verification passed over HTTPS: explicit Add user, row access switches, removed header badge and chat selectors, editor settings in configuration, live global save and stale-save rejection, admin RAG policy in its new location, and mobile layout with no JavaScript errors. Effective production defaults were preserved. Postdeployment S3 upload/download checksum and isolated restore passed with runtime configuration, access roles, existing history and four FAISS indexes preserved. Source-only updates now merge against the latest editorial policy to avoid stale browsers overwriting/rejecting editor changes.

Final source-control regression: source-only PATCH requests preserve the latest model/instruction/voice policy even when an editor has changed it since the client last refreshed; model-field injection remains rejected and ownership is enforced. All 69 tests pass.


## Dedicated live prompt management — 2026-11-14

Added configuration **Промпты** tab and adjacent create/edit actions beside the conversation model/prompt selectors. Editors/admins can create named prompts, edit text/name, choose one for a chat and delete unused entries. Generated IDs replace manual ID entry. The default fallback and in-use prompts are protected; use counts, optimistic revision checks and immutable previous response settings remain enforced. Unrelated unsaved model selections are preserved while creating/editing prompts.

71 backend tests and production frontend build pass; the local desktop/mobile browser workflow covers prompt creation, selection, live editing, in-use deletion protection, unselection/deletion and preserved model choice. Search credentials were copied into the existing production environment, but the initial access verification returned HTTP 401; the secret was never printed and search remains disabled pending correction.


Prompt production verification passed over HTTPS: create, edit, select, preserve the model selection, protect an in-use prompt, unselect/delete, mobile layout and no JavaScript errors. Temporary prompt/chat/session were removed. Postdeployment S3 backup and isolated runtime-configuration/role/FAISS restore checks passed.

Search authentication was corrected without requesting another secret: the secret value and key ID had been placed in reversed environment fields. Their placement was corrected in the existing local and production environment files without printing either value. A subsequent bounded request succeeded (3 search results, approximately 0.49 seconds). The initial failed reservation remains conservatively accounted; reservation amounts are not provider invoices. Credentials are ready; the web adapter and combined retrieval remain disabled/pending. Backup service strips the new search credentials from its process environment as well.


## Web search and combined RAG/web delivery

Added Yandex Search API snippet retrieval and the operational per-chat Internet switch. All roles can use web-only, RAG-only, both or neither. Combined context is bounded and uses validated D/W citation IDs; previews distinguish dated search snippets from full pages, show their original URLs, and open over the chat. No destination page fetching is performed.

Private query/result caching, freshness-sensitive answer reuse, existing spend reservations, bounded search concurrency/timeouts and complete request cancellation are implemented. Saved web citations remain private to the owning chat, including source previews; source failures do not silently fall back. Production credentials were already verified and remain in the unified environment. Final local verification: 86 backend tests and frontend build pass; web browser workflow passes without JavaScript errors, including both source types and disabled modes.


Production web-search verification passed: the Internet switch is active, internet-only answers contain actual W citations, combined answers contain both D and W citations, web previews show real URLs/excerpts, the switch persists and mobile layout has no JavaScript errors. Observed single-run totals were ~2.2 seconds for web-only and ~3.8 seconds for combined retrieval, with the second search served from cache; these are smoke timings, not benchmarks. Temporary test chats/sessions were removed, and existing content/settings were preserved. Offline restore verification now exercises saved web excerpt links as well as normal history when available.


## Browser branding — 2026-11-14

Added the existing green waveform brand as an SVG browser-tab icon, a 16/32/48-pixel ICO fallback, and a 180-pixel touch icon. Frontend build and public HTTPS/browser decode checks passed with correct image MIME types and no JavaScript errors. Only the web container was redeployed; backend service and data were preserved. The owner confirms web search works. Next priorities are grounding evaluation, an admin usage/latency/cache view, and a 5–10-user/venue rehearsal before further integrations.

## Public-page delivery verification — 2026-11-14

161 automated tests and the frontend build passed. Reader checks cover public-address pinning, mixed/private DNS, original TLS hostname and verification settings, redirects, byte/type/encoding/time limits, page cache isolation/freshness/cache directives, HTML cleanup, saved-source privacy after cache expiry, answer-cache invalidation and cancellation of parallel reads. Mock and production browsers passed page-text source popup and mobile checks; labelled snippet fallback also passed in the isolated browser. A live answer read two pages, cited page text and retained the saved excerpt/provenance through source preview and Markdown export (single smoke-check total ~2.8 s). No existing chat selections or budget limits changed. Temporary verification data was removed. Predeployment local/isolated restore and verified off-VM backup checks passed.

Administrative review verification: 165 tests passed, including role denial/revocation, disabled-owner access, complete pagination and an admin read during another user's active generation. Frontend build and isolated/production desktop/mobile checks passed. Saved web citations are previewed from the authorized admin transcript, leaving normal owner-scoped source routes unchanged. Read events contain IDs/counts, not message bodies. Synthetic production fixtures and temporary sessions were removed; viewing made no provider calls or budget changes. Current three-page user overview is indexed under docs/ with a 2026-11-15 edition; the older edition remains available.

Production communication-mode verification passed for a temporary regular user: real browser PCM capture, exactly one automatic submission after explicit Stop, real Yandex LLM/TTS playback, Cancel with no recognition/send, persisted mode/review/classic controls and mobile layout under unchanged CSP. Recognition used a controlled transcript for the fake microphone, not a new live-STT quality test. Synthetic user/chat/session were removed; spending limits were unchanged. Both services are healthy.

Production manual verification passed for a temporary regular user: help icon, Russian sections, linked navigation and keyboard focus, Escape/close with draft preserved, mobile scrolling and close-button visibility, strict CSP and no paid requests. Temporary account/session removed. Backend was not restarted for this frontend-only release.

Live capture reuses the playback AudioContext unlocked by the initial user gesture, avoiding a new autoplay unlock requirement between turns. Capture owns and releases only its input nodes/tracks; Pause/Stop closes the shared playback context too. Startup cancellation releases acquired tracks even if AudioWorklet loading/resume is delayed. Explicit worklet shutdown prevents idle input processing while the shared context plays an answer.

Production live-dialog verification passed two automatic turns with a temporary regular user and real Yandex STT, LLM and TTS. The browser captured/resampled synthesized Russian speech as microphone input; recognition was not stubbed. Verified local lazy VAD under the deployed CSP, microphone off for playback, no overlapping turns, automatic re-listening, Pause release, persisted mode/pause settings without auto-start, updated manual, prior modes and mobile layout. Existing conservative reservations totalled 90 RUB for this bounded verification; limits were unchanged. Synthetic chat/user/session were removed; usage ledger remains accounted. Physical room/device echo and noise rehearsal is still pending.

Documentation-first interruption update verified: frontend build and manual source/bundle equality passed. Production regular-user popup checks confirmed the current workaround and clearly labelled planned interruption, navigation/Escape/focus return, retained draft and mobile layout, with no paid calls. Frontend-only redeploy; interruption behavior and backend were unchanged. README summaries were refreshed to the current three-mode state.
