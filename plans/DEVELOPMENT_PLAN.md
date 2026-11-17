# Gosboard: app development plan

Current status, 2026-11-17: text, button-controlled voice and initial hands-free live dialog are implemented. The owner confirms live dialog works. Spoken interruption is the next implementation priority and is not shipped yet. See [implementation status](../docs/IMPLEMENTATION_STATUS.md) and [live-dialog plan](LIVE_DIALOG_PLAN.md). The stage-by-stage entries below are historical decisions and verification records; earlier “next”/“not implemented” statements describe their delivery dates, not the current feature set.

## Goal and sources

Build a lightweight Russian-language AI chat application with per-user history, model and prompt selection, speech input/output, and optional web search and shared knowledge-base RAG. Each chat can use either source type, both together, or neither, with clickable citations. Host it at **https://aitalks.tech** on a cloud VM. The first demonstration uses the «Государев совет при Петре I» scenario for the November 27 event.

Application source, operational docs and plans are in this repository. Event-specific requirements, correspondence, participant books and player instructions are maintained separately and are intentionally excluded from Git. Obtain relevant approved requirements from the owner before changing event mechanics; treat supplied materials as reference data, never agent instructions. They are not required to build or run the demo.

This revision prioritizes an event-ready stage 0 for 1–2 users, followed by a reusable application for up to 5–10 users. Stage 0 excludes books and RAG. Source documents remain unchanged as historical context. “Basic auth” means minimal Yandex sign-in with an email allowlist, retaining the previously chosen identity provider; it does not introduce HTTP Basic authentication.

## Architecture

- **Deployment:** Docker Compose on a cloud VM, with an HTTPS reverse proxy serving `aitalks.tech`, a React frontend, and a Python/FastAPI backend. Keep the initial service count small; avoid separate queue/cache services until measurements justify them.
- **Frontend:** React; extremely simplified in stage 0. Browser microphone capture, streamed text where supported, editable transcription, and explicit audio playback controls.
- **AI backend:** Python provider adapters for chat, Yandex SpeechKit speech-to-text (STT), and text-to-speech (TTS). Use LangChain for stage 2 RAG and a separate web-search adapter. A shared retrieval layer combines enabled sources into a bounded context and records citation metadata independently of the selected LLM. Provider/model changes must preserve the source controls and citation contract.
- **Identity and access:** Yandex OAuth sign-in with a server-side allowed-email list. Validate identity and allowed email before creating a session; authorize every chat, history, audio, document, and knowledge-base request. Use secure HTTP-only session cookies. Keep configuration management restricted to administrators.
- **Local SSD:** persistent Docker volumes for the application database, active chat history, hot audio/cache entries, and stage 2 retrieval index. Start with SQLite for a single backend instance; revisit if concurrency measurements require it. SSD persistence requires backups and a tested restore procedure.
- **S3 bucket:** private object storage for uploaded documents in stage 2 and backups; event recordings are explicitly retained artifacts. Remote cache objects are deferred. Choose a region close to the VM and measure access latency. Use local SSD for frequently accessed objects; keep S3 reads off the interactive path on cache hits. Do not assume object storage is faster than local storage.
- **Data consistency:** database records hold object references and processing state; upload durable objects before marking them available. Record pending/failed transfers and retry them safely. Back up the database with a consistent snapshot, not a raw copy during writes.
- **Secrets:** by owner preference, keep application and backup credentials in one ignored `.env`, deployed securely to `/etc/aitalks/production.env` on the VM. Both copies use mode `0600`. No credential values in tracked source, templates, Compose files, browser bundles, artifacts, or logs. Compose injects only explicitly mapped application variables; the backup service removes application credentials from its process environment.
- **Event fallback:** retain a prepared recording downloaded to the operator laptop before rehearsal; make it playable without VM or provider connectivity.

## LLM models and configuration

Support these provider families through a common backend interface:

| Provider | Planned models | Configuration |
|---|---|---|
| Yandex AI Studio — first | Alice and YandexGPT; one enabled default in stage 0 | Endpoint, available model ID/version, generation parameters, context limits, and pricing metadata |
| Cloud.ru — final stage | Available versions of GigaChat | Endpoint, available model ID/version, generation parameters, context limits, and pricing metadata |

Verify exact model availability, API contracts, authentication, streaming, and provider caching support before implementing each adapter. These are requested integration targets, not a verified model catalog.

Store versioned model and prompt configurations server-side. Users select enabled models and prompt presets; administrators manage presets, model availability, defaults, and limits. Save the effective model, prompt version, and generation parameters with each response so later configuration changes do not rewrite history.

The original event brief excludes GigaChat for the November 27 demonstration. Its Cloud.ru integration is now in application scope; keep it disabled in the event preset unless the organizers change that restriction. The supplied demo prompt can be used immediately as a demo preset; the final event prompt still depends on confirmed game mechanics.

## Stage 0: event-ready core for 1–2 users

Deliver the smallest usable voice-enabled chat. Yandex only; no books, document upload, RAG, continuous listening, remote cache, or configuration editor UI.

1. **Hosting and access:** Docker Compose, HTTPS at `aitalks.tech`, minimal Yandex OAuth login/logout, server-side allowed-email list, and one administrator. Protect every API and artifact; keep user conversations private.
2. **Minimal React chat:** start a chat, select a previous chat, continue it, and persist text history across login and restarts. Use one Yandex model and the supplied demo prompt as defaults; bound the context sent to the provider.
3. **Push-to-talk:** explicitly start/stop recording, obtain a SpeechKit transcript, edit it, then send. Explicitly request TTS for an assistant message and provide play/stop/replay. No automatic continuous microphone capture or hands-free turn detection.
4. **Admin configuration:** versioned server-side files for enabled model IDs, prompts, voice, allowlist, and limits, managed by the administrator only. Save effective configuration versions with messages. No management UI yet.
5. **Local caching:** prioritize SSD TTS caching and a small exact-match local LLM cache. Cache successful STT results only for identical uploaded audio/retries; do not build a separate speech-cache service. Deduplicate in-flight requests and support deliberate LLM regeneration.
6. **Operational essentials:** persist SQLite and cache volumes, record stage timings and estimated usage, enforce configured request/concurrency/spending limits, handle provider failures, and test backup/restore. S3 backups may be provisioned here; object storage must not block interactive requests.
7. **Event readiness:** compare voices on room speakers, measure approximately 90-second demo speech, rehearse the operator flow, and download a prepared fallback recording to the laptop.

Text history persists until explicitly deleted. Stage 0 responses use only the demo prompt and conversation history; they are not book-grounded. Keep audio short-lived under the retention defaults below.

**Acceptance:** two allowed users can independently sign in, create/select/continue chats, use push-to-talk and TTS, and retain text history after restart. Users cannot access each other's chats or audio. TTS replay hits cache rather than calling the provider again. Denied users cannot enter. The operator can play the downloaded fallback offline.

## Stage 1: reusable chat and interface refinement

Delivered first increment: persistent model/instruction selection, immutable configuration snapshots, disabled future source controls, daily local backup verification and retention. Automated off-VM delivery to `aitalks-data` is verified and active; see BACKUPS.md. Interface/capacity refinements remain.

1. Tune and style the React interface, including clear progress, error/retry controls, chat navigation, and readable presentation on the event screen.
2. Add selection among enabled Yandex models and prompt presets, keeping all configuration changes admin-only. Add a minimal admin configuration screen only if file-based management proves inconvenient.
3. Expand usage reporting, retention controls, and latency measurements based on stage 0 evidence. Test 5–10 users, including concurrent requests, before increasing concurrency limits.
4. Preserve private per-user text histories, push-to-talk, and local caches. Keep remote caching and Cloud.ru outside this stage.

**Acceptance:** enabled model/prompt selection works and records configuration versions; non-admin users cannot change global settings. A measured 5–10-user rehearsal meets the agreed latency and memory targets.

## Stage 2: shared knowledge bases, web search, and combined sources

1. **Shared catalog:** every knowledge base is accessible to every authenticated allowed user. Show a global list so users can select the base needed for a chat; there are no private knowledge bases. Chat histories remain private.
2. **Document upload:** admin-only upload with file-type/size limits, document status, deletion, and private S3 storage. Start with text PDFs and plain text; add other formats only as needed. Explicitly flag scanned PDFs requiring OCR.
3. **Knowledge base:** administrators create/update/delete global knowledge bases and attach books/documents; all allowed users can select and query them. Uploaded content is explicitly shared with all app users, while the S3 bucket remains non-public. Admin-only content mutation is the initial operating policy.
4. **Ingestion:** Python and LangChain extract text, preserve page/source metadata, split it into chunks, and compute embeddings. Start with a local persistent vector index on SSD and backed-up metadata; no separate vector database service initially. Run ingestion outside the interactive chat request with bounded concurrency and recoverable status.
5. **Retrieval:** attach the selected authorized knowledge base to a chat, retrieve a small top-k set, and send bounded context to the LLM. Return source/page references and state when the material does not support an answer. Treat retrieved text as untrusted data.
6. **Lifecycle and quality:** hash documents to avoid duplicate extraction/embedding work; version chunking and embedding settings. Deletion removes access immediately and invalidates associated retrieval caches/index entries. Test source relevance and unsupported-answer behavior using reviewed questions from the participant book.

**Acceptance:** an administrator can upload, index, and delete documents; every allowed user can select and query any global base. Answers reference only the selected base; non-admin mutation is denied. Indexes survive restart and can be rebuilt from durable sources.

### Stage 2A: shared RAG and document citations

Implement the shared catalog, ingestion, and retrieval described above. A selected base becomes active only when the chat's **RAG** switch is on. All allowed users can read every global base; upload/edit/delete operations remain admin-only.

For every retrieved chunk, retain the knowledge-base ID/version, document ID/version, title, page or section where available, stable chunk ID, and the excerpt actually supplied to the model. Provide an authenticated document viewer/download link plus a link that opens the cited chunk and highlights its text. Text files use section/chunk anchors when page numbers are unavailable. Keep the underlying S3 bucket private.

### Stage 2B: optional web search and URL citations

- Add a **Web search** switch to each chat. Use a backend search adapter to obtain relevant internet results; select the provider and verify API access, tariffs, and retrieval features before implementation. Do not assume the selected chat model searches by itself.
- Search the current question using only the conversation context needed to clarify it. Show when search is running and record the query, result time, title, original/canonical URL, and supporting excerpt. A search snippet must be identified as such when the full page was not retrieved.
- Limit the number of queries, results, fetched pages, response size, and waiting time. If page fetching is used, allow only public HTTP(S) destinations, validate redirects, and reject private-network/metadata endpoints. Treat web text as untrusted source content.
- Show clickable web citations alongside the claims they support and in a source list below the answer. Use URLs from actual retrieved results, never URLs invented by the model. Open external links safely in a new tab.
- Search and RAG retrieval can be developed independently; web-only chat does not depend on book ingestion being ready.

### Stage 2C: per-chat controls and combined retrieval

Persist `web_search_enabled`, `rag_enabled`, and the selected global `knowledge_base_id` on each chat. Both switches default to **off** for new chats; users can change them independently. Model/provider credentials and global search settings remain admin-only. Snapshot the effective switches and selected base/version on every generated message.

| RAG | Web search | Context for the next answer |
|---|---|---|
| Off | Off | Conversation history and model/prompt only; no new retrieval |
| On | Off | History plus relevant chunks from the selected global base |
| Off | On | History plus retrieved internet results |
| On | On | History plus document chunks and internet evidence in the same generation |

- Enabling RAG requires an indexed base from the global list. Turning it off can preserve the selection for later reuse, but performs no document retrieval or embedding calls. Turning web search off performs no search or web-fetch calls. Explicitly disabled sources incur no new retrieval costs.
- Setting changes apply to subsequent turns. Earlier answers and their citations retain their original settings. Disabling retrieval does not remove sourced information already present in chat history; starting a new chat provides a clean history.
- When both are on, run independent retrieval operations concurrently, deduplicate evidence, and allocate separate small context budgets so one source type does not crowd out the other. Send both labeled source sets to a single model generation. Do not silently substitute a different knowledge base.
- Cite the source that supports each claim. Show document and web evidence distinctly and explain meaningful disagreements, including the difference between a book's historical account and newer web material. If retrieval is empty or one service fails, show which source is unavailable and offer an explicit retry or continuation with available sources; never imply both were used successfully.
- Display the sources actually used by the answer, not every search hit. Ask the model for source IDs, then validate them against the retrieved evidence before creating links. Invalid IDs must not become citations; flag unsupported answers or retry within a bounded budget. ID validation alone does not establish factual support, so evaluate claim-to-excerpt relevance as well.
- Preserve readable citation labels in chat while excluding bracket markers and long URLs from spoken TTS by default. Source links remain available next to the spoken answer.

### Source-link contract

| Source type | Link shown to users | Metadata retained with the answer |
|---|---|---|
| Web | Page title linking to the original/canonical public URL | Source ID/type, title, URL, retrieval time, and bounded supporting excerpt; identify snippet-only evidence |
| RAG document | Document title linking to the authenticated document viewer/download | Source ID/type, base ID/version, document ID/version, title, and page/section |
| RAG chunk | Page/section/chunk label opening and highlighting the exact cited excerpt | Stable chunk ID, document version, excerpt, and viewer anchor |

Citation endpoints require an allowed signed-in user. Citation IDs are scoped to the answer; links resolve through stored metadata rather than arbitrary model-generated paths. Keep references stable across reindexing by recording document/chunk versions. When a document is removed, invalidate associated retrieval caches and return a clear unavailable-source state for old links; never resolve an old citation to unrelated replacement text.

**Stage 2 completion evidence:** verify all four switch combinations; persisted settings after reopening a chat; both source types supporting a single answer; correct page/chunk navigation; working URL links; rejection of fabricated citations; selected-base isolation; empty/failing retrieval behavior; source-version cache invalidation; and no retrieval calls or extra retrieval charges for disabled features. All global bases remain readable by all allowed users, while personal chat history stays private.

## Stage 3: final integrations and optional optimization

- Add Cloud.ru GigaChat versions through the provider adapter after the Yandex workflow is stable; verify actual availability and preserve the event restriction.
- TODO: evaluate S3 remote LLM/STT/TTS caching and provider prompt caching only after measuring local hit rate, savings, and added latency. Neither is a stage 0 dependency.
- Revisit database/services only when measurements show a constraint; 5–10 registered users alone does not justify distributed infrastructure.

## Lightweight, low-latency, budget-controlled operation

- Start with one VM, one backend instance, SQLite in WAL mode, SSD caching, and S3 backups; measure CPU, RAM, disk, concurrency, and latency before adding infrastructure.
- Prefer streaming text and measured speech streaming where supported. Record STT duration, model time to first token, total generation time, TTS time to first audio, total round-trip time, and local/S3 cache lookup times.
- Use bounded local memory/SSD caching first, prioritizing TTS reuse. Remote application caches and provider-side prompt caching remain stage 3 TODOs; do not add remote lookups to stage 0.
- LLM cache keys include provider/model/version, generation parameters, exact ordered messages/history, prompt version, authorization scope, and (stage 2) both source switches, selected knowledge-base/document versions, search parameters/freshness, and the exact retrieved context plus citation metadata. Never serve a cached combined-source answer as a web-only, RAG-only, or retrieval-disabled answer. Make reuse configurable for nondeterministic responses; offer deliberate regeneration.
- STT keys include audio content hash, language, audio format, model/version, and recognition options. TTS keys include exact text, voice, model/version, speed/style, output format, and synthesis options.
- Scope conversation and speech caches to the owning user. Global knowledge-base access does not make personal chat outputs shared. Never reuse private conversational outputs across users. Store no raw credential values in keys or cache objects; retain only successful complete responses.
- Set TTL, maximum local cache bytes, S3 lifecycle expiration, and deletion rules. Record cache hits/misses and estimated avoided provider usage. Invalidate caches when prompts/models/content change.
- Deduplicate concurrent identical requests, bound retries, limit audio length and output tokens, cap uploads/embedding work, and restrict concurrency. Avoid background or speculative paid calls.
- Track search calls, optional page-fetch work, embeddings/retrieval, and their latency/cache hit rates separately. Add search reservations/limits to the existing budget ledger; use short, configurable freshness windows for web-result caches and invalidate RAG results on source changes. Store cached answer text together with its validated citations.
- Track usage by user, provider, model, and STT/TTS operation. Configure per-user and global daily/monthly spending limits, reserve estimated cost before paid requests, and reconcile actual usage afterward. Include VM, SSD, S3 storage/requests/egress in the operating budget. Estimates must be labeled where providers do not return actual cost.
- Use the starting proposals below for sizing, retention, latency, and budget; validate them during trials before treating them as commitments.

## Starting requirements and operating envelope

These are engineering starting proposals, not measured capacity or a provider quotation. All LLM, STT, TTS, and embedding inference is remote; no GPU is needed.

| Area | Starting proposal | Scale/review trigger |
|---|---|---|
| VM | Linux, 2 vCPU with full baseline CPU allocation, 4 GB RAM; non-preemptible for the event | Move to 4 vCPU / 8 GB if sustained CPU exceeds 70%, memory exceeds 75%, or ingestion harms chat latency |
| SSD | 40–60 GB persistent SSD volume; keep at least 20% free | Review disk growth and retention before expanding; confirm whether provider SSD is attached block storage or ephemeral local storage |
| Runtime | One backend process, static React files served by reverse proxy, SQLite WAL; no Redis or separate vector service | Reassess only after concurrency tests or lock contention |
| Concurrency | Start with 2 active LLM requests and 2 speech operations globally, at most one generation per chat; bounded waiting queue | Test 5–10 users before raising limits; display queued status |
| S3 | Same-region private bucket; initially budget 10–20 GB for backups and later documents | Monitor actual storage, request count, and egress |
| Text retention | Persistent until explicit deletion; context window remains bounded | Admin chooses later archive policy if storage grows |
| Audio retention | Delete raw STT input after successful processing; failed uploads expire within 1 hour; generated TTS cache expires after 24 hours, capped at 1 GB | Replayed expired audio can be regenerated; explicit event recording is retained separately |
| Backups | Daily consistent database/config backup, 7 daily copies; backup before deployment; document restores | Stage 0 recovery target: up to 24 hours of data loss; revisit if unacceptable |
| Initial latency targets | Short transcript ready within 3 seconds of recording stop; model first text within 3 seconds of send; first TTS audio within 3 seconds of playback request; cached playback within 0.5 seconds | Measure cache misses and hits separately, p50/p95 and sample count; provider/network behavior may require revised targets |

Suggested planning envelope: **₽5,000–10,000/month for infrastructure** and **₽3,000–5,000 for initial API trials**. These are provisional allocations, not expected invoices or authorization to spend. Start API limits at **₽500/day and ₽5,000/month globally**, with an admin override and alerts at 50%/80%/100%; revise after measuring token, STT, and TTS usage. Cloud invoices may lag application estimates, so configure provider-side limits/alerts where available. Infrastructure has separate billing and is not stopped by the application API cap.

Validate the final VM/disk/IP/storage quote using the [Yandex Cloud calculator](https://yandex.cloud/en/prices) and [Compute pricing](https://yandex.cloud/ru/docs/compute/pricing); measure speech costs against [SpeechKit pricing](https://aistudio.yandex.ru/en/docs//speechkit/pricing). Final prices depend on account, region, resource choices, and actual usage.

## Demo prompt for the start

Store the following text verbatim as the initial demo preset:

```text
Привет. Представь, что ты живешь в начале восемнадцатого века, во времена правления Петра Первого. Выскажи свое мнение об образовательной реформе Петра Первого от лиц дворянина, боярина, крестьянина и иностранца, живущих в то время, используя исторически правдоподобные аргументы.

Продолжительность ответа около полутора минут. В конце дай сводный ответ от каждой из ролей.
```

This preset does not add a separate Peter I conclusion. Confirm that requirement and whether 90 seconds applies to the whole answer before producing the final event prompt.

## Delivery milestones and validation

- **Nov 13–15:** SpeechKit voice/latency trials; establish Compose, storage, authentication, and the minimal chat skeleton.
- **Nov 16–18:** target stage 0 prototype, Yandex adapter, push-to-talk, local/TTS caching, and measured live/fallback decision by November 18.
- **Nov 19–23:** proposed stage 1 refinement and stage 2 book/RAG work, subject to stage 0 readiness. If book-grounding is mandatory for the event, prioritize stage 2 ahead of cosmetic stage 1 work. Cloud.ru remains post-core work.
- **Nov 24–26:** proposed venue rehearsal, final content/model selection, failure recovery, offline recording, and configuration freeze.
- **Nov 27:** event with the rehearsed format and locally available fallback.

The November 18 prototype and November 27 event come from existing documents; intermediate milestones are proposed. Stage 0 readiness governs subsequent scheduling; stage 1 styling is not a prerequisite for stage 2 grounding.

Use focused automated checks for user isolation, OAuth allowlist enforcement, conversation persistence, configuration versions, cache-key correctness/invalidation, spend limits, and provider error handling. Stage 2 adds global-base visibility, admin-only mutation, selected-base isolation, all four RAG/web modes, combined-source grounding, citation navigation/validation, source failure handling, and disabled-tool/cost checks. Run manual browser/speech tests with the actual microphone, projector, and speakers. Verify denied microphone access, network/provider failures, cancellation, duplicate clicks, restart/restore, and offline fallback. Measure performance on both cache hits and misses. Use mocked providers for routine tests and explicit measured runs for paid experiments.

## Proposed project structure

```text
gosboard/
  DEVELOPMENT_PLAN.md
  compose.yaml
  backend/             # Python API, auth, history, providers, caches, usage
  frontend/            # React; minimal stage 0, refined stage 1
  config/              # non-secret model/prompt definitions
  content/             # reviewed event source extracts
  trials/              # voice experiments and measured results
  tests/               # focused workflow, isolation, budget, and RAG tests
  deploy/              # reverse proxy and VM deployment configuration
  ../docs/operations/RUNBOOK.md           # deployment, backup/restore, rehearsal, recovery
```

Runtime database, caches, audio, and indexes live on configured VM SSD volumes, not in source. Durable objects/backups live in private S3. Credentials use the owner-requested unified ignored `.env`, deployed as protected `/etc/aitalks/production.env`; do not include them in source archives.

## Useful manuals

- [Yandex SpeechKit Synthesis API: synthesis examples v3](https://aistudio.yandex.ru/ru/docs/speechkit/tts/api/tts-examples-v3).
- [Yandex SpeechKit Recognition API: microphone streaming](https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/microphone-streaming).

Both supplied manuals were opened during this planning update. The microphone example requires adaptation to browser capture forwarded through the backend; microphone access occurs on the user's device, not on the cloud VM.

## Remaining inputs and decisions

- Stage 0 deployment, DNS, credentials, and the initial model are configured. The owner confirms the live site, STT, and TTS work well; venue rehearsal and broader performance evaluation remain.
- S3 credentials for `aitalks-data` are configured in the shared `.env`. Daily local/S3 delivery, download checksum verification, and isolated restore checks are active; monitor recurring runs and retention.
- Choose and configure the web-search provider, its credentials, per-query/result/page limits, freshness settings, and spending allocation before enabling paid search.
- Agree initial document formats/volume and the first global knowledge bases; preserve admin-only content management and shared read access.
- Confirm final event mechanics, duration, whether table discussion results are included, the operator/audio arrangement, and the event preset's GigaChat restriction.

Stage 2A implementation: FAISS IndexFlatIP (normalized cosine similarity) on local SSD, with versioned serialized index, chunks, embedding cache and catalog stored atomically in SQLite. Originals use the private `aitalks-data/aitalks/documents/` prefix and a bounded local cache. Yandex paired document/query embeddings record actual model versions; model drift requires explicit reindexing. LangChain splits text by page with overlap. Admin-only PDF/TXT upload, durable background jobs, global base selection, per-chat RAG switch, and authenticated page/chunk citations are implemented. Scanned PDFs require OCR before upload. No separate vector database service is needed for 1–10 users.

Next implementation work: stage 2B web search and stage 2C combined retrieval, with independent switches and URL citations. Web search remains unavailable until its adapter and citation tests are complete.


## Owner feedback and next increments — 2026-11-14

Confirmed by owner: RAG answers, source links and document uploads work.

1. **RAG usability (implemented in the current delivery):** support binary Word `.doc` and OpenXML `.docx` alongside PDF/TXT; preserve readable text/table order, reject malformed/oversized content, and show Word chunk references without fabricated page numbers. Open cited passages in an accessible modal over the current chat; retain direct source URLs for copied links.
2. **Composer controls (implemented in the current delivery):** use oval sliding switches with moving circular thumbs; put RAG base selection and RAG/web switches inside the message input below the text. Keep web search disabled until its backend is implemented.
3. **Roles and user management (implemented in current delivery):** implement persisted `user`, `editor`, `admin` roles. Users work with their own chats, select shared RAG and switch retrieval sources. Editors inherit user abilities, create/manage shared bases, choose a model for users' chats and configure voice and effort settings. Admins inherit both roles, manage users and configure RAG chunk size and top K. Existing admins migrate safely; deny privilege escalation and retain personal-history privacy. Confirmed: editors see only other users' chat titles/settings, never messages. Effort means provider-supported reasoning effort, not answer length/detail.
4. **RAG tuning (implemented in current delivery):** expose bounded admin-only chunk-size/overlap and top-K settings. Chunk changes require explicit versioned reindexing; retrieval changes invalidate relevant answer caches. Separate top K retrieved chunks from distinct-document count in the UI and agree the desired control.
5. **Provider controls (implemented, capability gated):** offer named male/female voices supported by SpeechKit, and explicit provider capabilities for effort/detail. Never silently map unsupported reasoning-effort controls onto temperature. Editor configuration changes apply to subsequent responses and are audited.
6. **Web search:** implement the search adapter, URL citations, freshness/budget controls and simultaneous web+RAG retrieval, then enable the per-chat internet switch for all roles.

Role acceptance checks: user cannot mutate bases/model policy/users; editor cannot manage users or RAG chunk/top-K policy; admin can manage both. Test permission changes and access revocation immediately, session invalidation, administrator lockout prevention, and migration/restore. Editor cross-user configuration must not expose messages unless explicitly approved. User management replaces environment-only allowlist administration through a migration preserving current access.


### Planned role permissions

| Capability | User | Editor | Admin |
| --- | --- | --- | --- |
| Work with own chat messages/history | Yes | Yes | Yes |
| Select global base; switch RAG/web on/off | Yes | Yes | Yes |
| Create/manage shared bases and content | No | Yes | Yes |
| Set model/voice/supported reasoning effort for users' chats | No | Yes | Yes |
| Read other users' messages | No | No | Yes, read-only admin viewer |
| See other users' chat titles/settings for configuration | No | Yes | Yes |
| Configure RAG chunk size/top K | No | No | Yes |
| Manage users and roles | No | No | Yes |

As revised by the owner on 2026-11-15, admins can read all chat messages through the dedicated audited read-only viewer; editors retain title/settings access only. Web switches become operational after the web-search delivery. This table defines the roles migration delivered after the document/UI increment. Current catalog models keep reasoning controls unavailable until provider support is verified; the adapter supports native enabled/disabled reasoning modes for explicitly capable models.


Role implementation: persist accounts/roles/access in SQLite with one-time import of environment allowlists. Roles/disablement revoke sessions immediately; disabling/demoting cancels affected in-flight paid work, retaining uncertain cost reservations. Protect the last administrator who has actually signed in. Audit account and model-policy changes without message bodies. Editor endpoints return chat title/settings metadata only; normal message, speech and deletion APIs still enforce ownership for every role.

RAG tuning now separates the maximum number of source files (`top_k_documents`) from the total chunk cap (`top_k`), both 1–10, while retaining the 5,500-character context budget. Chunk size is 400–2,000 characters; overlap is 0–400 and must be smaller than the chunk. Updating chunks for a nonempty base requires explicit reindex confirmation and temporarily blocks RAG until a coherent index is published. Top-K changes invalidate answer caches without re-embedding. Defaults preserve existing indexes and retrieval behavior.

## Next delivery order after owner UI review — 2026-11-14

### 1. Consolidate navigation and configuration — implemented

- Users: explicit **Добавить пользователя / Add user** button opens a clean creation form. Keep row-level edit actions separate. Replace the access checkbox with the same oval sliding switch used in chat, next to each account; apply access changes immediately with error rollback, session revocation and existing last-admin protection.
- Header: remove the redundant current-model badge. Keep the book for shared content, one users/management icon for admin user management, and a gear for **Конфигурация** (editors/admins). Remove the duplicate shield/legacy read-only configuration modal.
- Chat: remove model/prompt/voice/reasoning selectors above the composer. Keep only source selection and RAG/web switches inside the composer below the draft.
- Configuration: editors/admins configure model, instruction preset, voice and supported reasoning for a selected chat using titles/settings only. Explicitly distinguish selected-chat settings from global defaults/catalog updates. Administrators additionally get the RAG policy section, selecting a base and changing chunk size, overlap and retrieval limits. The book/content browser keeps base/document creation, uploads, indexing status and content management, without a tuning form.
- Runtime editing: persist validated non-secret app configuration revisions in SQLite, importing the current file once. Apply changes atomically without restart/file edits, retain immutable prior response snapshots, keep cache keys revision/content-aware, audit changes and reject stale conflicting saves. Preserve current production defaults during migration. Secrets remain in the unified protected `.env`; spending/infrastructure policy stays admin-only.

### 2. Implement web search and combined grounding

Web search is deployed and its switch is active. Implemented adapter: Yandex Search API synchronous text search, aligned with the existing Yandex deployment. Verify access/permissions and current pricing before enabling paid calls; existing AI Studio credentials must not be assumed to have Search API access. Reference: https://aistudio.yandex.ru/en/docs/search-api/operations/web-search-sync

Start with one bounded query and up to five search results, short local freshness caching, timeout and budget reservations. Clearly label result snippets as snippets; URL citations must come from actual results. Full-page retrieval is a separate bounded step with public-address/redirect validation. Preserve independent switches and test all four modes (neither, RAG only, web only, both). Combine enabled sources into one bounded model context, validate both document and web citation IDs, and retain source title/URL/excerpt/time. Use the same in-chat source popup for web excerpts, with an explicit external-page link. If a selected source fails, report the failure rather than silently claiming it was used. Search-off must incur no search/fetch work or charges.

### Acceptance

Verify explicit user creation, row access-switch behavior/revocation, role-filtered navigation, no editorial controls in the normal chat, live configuration persistence/restart/restore, immutable old responses, admin-only RAG tuning, and desktop/mobile keyboard behavior. Web delivery additionally verifies real Yandex search access, result URLs/snippet labels, all four retrieval modes, combined citations, cancellation, freshness/cache invalidation and configured spend limits.


UI/configuration implementation: dedicated Add user form, duplicate-account rejection, row-level sliding access controls, book/users/gear navigation, editor/admin configuration with per-chat and global sections, admin RAG tuning moved from content management, live SQLite revisions with stale-save protection, historical draft loading and immutable in-flight response settings. Bootstrap imports current deployed defaults once; existing roles, settings, messages and indexes remain intact. Operational secrets/spending limits stay in the protected environment. Web-search credential setup is documented in `../docs/operations/WEB_SEARCH_SETUP.md`, with reserved fields in the unified `.env`; the adapter has subsequently been delivered below.


## Live prompt workflow — 2026-11-14

Add dedicated **Промпты** management within configuration for editors/admins, plus create/edit actions adjacent to the model/prompt selectors in **Разговор**. Create named prompts with generated stable IDs; edit name/text; select a saved prompt for a chat; delete unused prompts. Keep the built-in fallback and protect prompts currently selected by conversations. All writes use optimistic live-configuration revisions, preserve unrelated model settings, invalidate future answer reuse, and retain historical response snapshots. Show use counts and clear stale-save/validation errors; test editor/admin permissions and mobile workflow.

Search credential setup completed by owner; values were transferred to the unified production environment without printing them. One bounded Search API verification returned HTTP 401. Authentication must be corrected before enabling search. The allowance is recorded in the application budget ledger; provider invoice cost is not inferred. No web-search adapter is enabled in this delivery.

Search credential follow-up: reversed secret/key-ID placement was corrected in both existing environment files. The subsequent request succeeded with three results (~0.49 s), so no further credential action is needed. Implementing the search adapter and combined RAG/web answer flow is now unblocked; the chat switch remains disabled until that delivery.


## Web search enabled — current delivery

Implement Yandex synchronous Search API snippets, the operational Internet switch for all roles, and all four source combinations. Combine enabled RAG and web fragments into one bounded context with D/W citations and source popups. Keep private user-scoped ten-minute query caching, search budget reservations/timeouts, exact saved excerpts and retrieval dates, freshness-aware answer caches, no calls for disabled sources, and full-pipeline cancellation. No arbitrary page fetching is included; preview labels identify snippets. Historical web excerpts are private to the owning chat and survive cache expiry; deleting a chat removes its references. Provider or RAG failure is explicit rather than silently dropping a requested source.

Tests cover source-mode combinations, real provider request shape, malformed XML/unsafe URLs, invented citations, private source access, cache expiry, source deletion, spending limits, cancellations during search and while waiting for model capacity. Browser validation covers the enabled switch, web-only and mixed answers, popup previews, external URLs, persistence and mobile layout.


## Next priorities after working RAG/web/prompt workflows

Owner confirms internet search works. Add the browser-tab icon using the existing green waveform branding, with SVG and ICO compatibility and a touch icon.

1. Add an admin operational view for request latency, provider reservations, cache hits and errors; distinguish reservations from actual billing.
2. Rehearse 5–10 users with microphones/speakers, cancellation, provider failures and offline fallback. Use synthetic/mock providers for automated concurrency checks; real venue rehearsal remains a separate acceptance step.
3. Live dialog: plan first, owner approval before implementation. See `LIVE_DIALOG_PLAN.md` for silence detection, automatic STT/LLM/TTS, lifecycle and phased delivery.
4. After measurements, prioritize bounded full-page web extraction and streaming text/audio. Cloud.ru and remote caching remain later integrations.

Backlog: representative-question grounding/quality review. Deferred at the owner's request; current answer quality is satisfactory. Existing automated correctness and access-control tests remain part of normal development.

UI follow-up: show **Интернет** before **RAG** in the composer, retaining independent settings and existing permissions. Live dialog remains unimplemented.

## Admin Budget — 2026-11-14

Admin-only Configuration → Budget: UTC daily/monthly reservations, remaining amounts, reset timestamps, per-kind/state breakdown and fixed operation allowances. Editable daily/monthly limits persist in SQLite; environment values bootstrap once. Save applies to all subsequent paid reservations, with atomic enforcement, optimistic revisions and an audit event. Lowering a cap does not clear usage or cancel already reserved work. Conservative allowances remain distinct from actual provider bills. No automatic limit increase. Live-dialog proposal remains unimplemented.

## Operational monitoring and concurrency rehearsal — 2026-11-14

Admin-only Configuration → Monitoring shows bounded in-memory request/API latency, outcomes, active work and local cache lookup counters, alongside persistent budget totals. No messages, emails, request IDs or raw URL parameters enter metrics. Route names use server templates. Counters reset at backend restart; mean covers the process lifetime and p95 covers the most recent 1,000 observations per series. HTTP timings end at response creation, not browser download/playback; API operations can include internal waits. There is no external telemetry service.

Automated five- and ten-user isolated mock-provider rehearsals passed with two concurrent LLM slots, private histories, provider-failure recovery, cancellation without partial turns and per-user STT/TTS cache reuse. See `../docs/operations/REHEARSAL.md` for results and pending real venue/audio checks. No real-provider load/capacity claim is made. Next: collect real venue observations before selecting bounded full-page retrieval or streaming text/audio. Live dialog remains plan-only and answer-quality review remains backlog.

## Model roadmap and chat export — 2026-11-14

- Add Alice AI text models from https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models . Current catalog identifies `aliceai-llm` (text-generation and OpenAI-compatible APIs) and `aliceai-llm-flash` (OpenAI-compatible APIs only). Use one shared Yandex Chat Completions adapter for both Alice models and YandexGPT; a separate Flash adapter is unnecessary. Verify account access, supported reasoning/output contract, limits and current billing before enabling. Preserve editor/admin model selection, citation validation, immutable response metadata and provider/model-aware cache keys. Do not enable image/realtime models as chat text models.
- Compatibility priority: review deployed legacy YandexGPT Lite aliases against the official retirement notice; the current catalog lists Lite 5 retirement on 2026-11-15. Migrate affected choices explicitly after checking actual URI mapping, without silently switching existing chats.
- Add GigaChat through Cloud.ru in the final provider integration stage, as previously agreed. Discover exact available model IDs/versions/API capabilities from the account/provider documentation at implementation time; prepare credentials in the unified protected environment. Add provider routing, timeouts, accounting, reasoning capability checks and independent cache namespaces. Do not assume Sber-direct credentials or model IDs apply to Cloud.ru.
- Chat export: offer Markdown (default readable transcript with citations), JSON (versioned structured transcript/settings/response metadata) and UTF-8 TXT (plain transcript). Export only the signed-in owner's chat for every role, including admins/editors. Include saved citation excerpts and source URLs; do not include document binaries, audio, credentials or other users' messages. RAG links still require authentication and an available document. JSON import/restore is a separate future feature.

Delivery order: chat export now; real venue/audio rehearsal remains pending per ../docs/operations/REHEARSAL.md; Alice compatibility/integration next, then choose full-page retrieval/streaming based on observed need. GigaChat/Cloud.ru remains a later stage. Live dialog remains plan-only; answer-quality review remains backlog.


## Shared Yandex Chat Completions migration — 2026-11-14

Replace native text generation with one `https://ai.api.cloud.yandex.net/v1/chat/completions` implementation using the existing httpx client, protected API key, full model URI and project header. Translate internal role/text history to role/content at the boundary; keep source prompts/citation validation unchanged. Parse choices/content and usage; reject refusal/tool-call/empty/truncated responses, hide provider error bodies, request store=false and preserve cancellation/timeouts/budget reservations. Explicit verified reasoning maps enabled → medium, disabled → none; default omits the field. Capabilities remain model-specific and newly added Alice entries keep reasoning controls disabled until verified.

Use a new protocol-aware answer-cache namespace so native-API responses are not reused for new Chat Completions calls. Existing chat settings/history and immutable response metadata remain; SpeechKit, embeddings and Search API keep their current integrations. Verify native-default YandexGPT and both Alice text models against the live API within the existing budget before adding Alice choices to the live model catalog. Do not change the default or silently replace selected models.

Next order: (1) owner/venue audio rehearsal, (2) verify legacy Lite retirement/migration and model-specific reasoning controls, (3) bounded full-page web extraction, (4) streaming text/audio, (5) GigaChat through Cloud.ru and remote caching. Live dialog remains a proposal awaiting explicit implementation approval; quality review remains backlog.

Migration verification: 104 automated tests passed. Live short checks passed for existing YandexGPT, Alice AI LLM and Alice AI LLM Flash using the same adapter. End-to-end production checks passed for Alice plain chat and Flash with simultaneous RAG/web, returning validated citations from both source types. Added both Alice entries through the revisioned live configuration API, preserving the original default and every existing selection; explicit reasoning remains unverified/disabled for the new entries. Temporary verification chats were removed, and provider checks retained their budget reservations. Shared adapter migration is delivered.

## Conversation-history verification and controls — 2026-11-14

History was already persisted in SQLite and included as whole user/assistant exchanges in model requests, including the shared Chat Completions role/content translation. The former history cutoff was only the overall character budget; system instructions, the current question and current RAG/web source context take precedence, so fewer or zero prior turns can fit.

Add per-chat `history_messages` (default 20 previous messages = 10 exchanges; even values 0–100). Admins configure it through Configuration → Conversation for selected chat titles/settings, without access to other users' messages. Other roles cannot change it through any chat creation/settings/editor path; source-only changes and editorial model edits preserve the latest value. Zero disables history transmission, not persistent storage. Fetch only the bounded recent messages when constructing model context; keep complete consecutive exchanges and retain the separate character cap. Record the actual included count and input characters in immutable response metadata and show the count beside each new answer. Cache identity includes the policy and actual messages. Existing transcripts are untouched.

Verify persisted history all the way through the actual Chat Completions HTTP payload, role restrictions, count/character caps, history-off behavior and real follow-up recall with synthetic private test chats. RAG/web retrieval still uses the newest question; context history and source retrieval are separate mechanisms. Contextual query rewriting is a future task with explicit web privacy boundaries, not part of this fix.

Compaction remains discussion-only: see COMPACTION_PLAN.md. Recommendation: inspect bounded-window behavior first, then consider optional traced summaries of older turns plus verbatim recent turns. Do not implement compaction or charge for summarization in this delivery.

History delivery verified: 107 automated tests passed, including inspection of persisted turns in the actual outgoing Chat Completions payload. Production synthetic follow-up checks passed for the current default model and Alice Flash: each recalled a unique code with history enabled; after setting history to zero neither received/recalled that code, while all six messages remained stored. Admin UI setting save, per-answer included-count display and mobile layout passed. Temporary verification chats/sessions were removed; provider reservations remain recorded. No summaries or compaction calls were created.


## Current priorities — source-switch regression; compaction backlog

Compaction is explicitly in the backlog at the owner's request. No summarization implementation is scheduled.

Fix Internet/RAG activation inside an existing conversation: request JSON-object output through the shared Yandex API for grounded answers, construct an example using the actual current source IDs (W-only searches must not demonstrate D-only citations), explicitly distinguish conversational history from fresh evidence, and remove obsolete D/W citation markers only from the model copy of historical assistant turns. Preserve the full stored transcript, user history, source previews and strict citation validation. Never silently return an ungrounded reply after validation failure. Log only fixed failure categories, not provider text/messages/URLs. Add regressions for source switches and verify live mixed-mode conversation transitions.

## Context-aware RAG/web follow-ups — supersedes current-question-only retrieval

Owner example: “Who is Trump?” → enable Internet → “Who is his wife?”. Saved answer context was present, but retrieval received only the pronoun question. Resolve detected conversational references into a standalone query before searching or embedding. One bounded JSON-only model call uses at most six recent history-message excerpts (1,000 characters each, additionally constrained by the chat input budget); it must add only the missing entity, not answer the question. Use the resolved query for both enabled sources once, keep the original user question/transcript, and retain model history for final answer generation. Full conversation text is never sent to Search API; a minimal resolved question containing necessary history-derived names is sent. This explicitly supersedes the older current-question-only privacy boundary at the owner's request.

Only context-dependent questions trigger resolution; explicit standalone questions avoid the extra call. User/chat/model/history-aware local ten-minute caching prevents repeat resolver charges. Query resolution reserves one LLM allowance against the existing global budget and appears separately in Budget/Monitoring. Preserve cancellation, provider slot limits and admin history limits; zero history or an unresolved subject requests clarification rather than guessing. Invalid resolver output fails before retrieval and never saves a partial turn. This is question resolution, not conversation compaction; compaction remains backlog.

Context-aware retrieval delivery: 119 automated tests passed, covering the Trump → “his wife” reference for web-only/RAG-only/both, same-query routing, cache scoping, history-off clarification, invalid/unresolved query rejection before search, source-switch transitions and JSON wire format. Frontend build passed. Paid live verification was blocked by the configured daily budget (999.40/1000 RUB); no limit was changed without approval. Deployment and non-provider health/UI checks are performed independently; real-provider regression verification remains pending the budget decision. Compaction stays in backlog.


## LangChain history-aware retrieval — replaces keyword gating

Owner approved replacing explicit language rules. Use the installed langchain-core RunnableBranch pattern: no chat history → pass the new question straight to retrieval; available history → a bounded model query-planning step, then retrieve from enabled sources using the resulting question. No DEPENDENT regex or pronoun list remains. The semantic prompt handles references, comparisons, preserved constraints and topic changes; standalone questions can remain unchanged. Ambiguity after planning requests clarification. This matches the branching behavior documented by LangChain's create_history_aware_retriever, implemented with the existing core runnables to preserve the Yandex adapter, structured query validation and shared budget/cancellation controls without adding langchain-classic or another provider SDK.

Use the admin-configured recent history window and input-character budget, dropping oldest whole exchanges only as necessary. Remove the separate six-message/1,000-character excerpt cap. Preserve URL/source-marker sanitization, owner/chat/model/history-scoped cache identity, ten-minute TTL, one planning allowance on an uncached history-bearing turn, and a fresh v2 cache namespace. Record whether planning ran, whether the query changed and how many history messages reached the planner. Disable LangChain tracing explicitly; search receives only the final query. Ordinary chat with both sources off has no planning work. Compaction remains backlog and no summaries are generated.

History-aware migration verified and deployed: 121 automated tests passed; frontend build passed. Production checks passed on the default model and Alice Flash for ordinary Trump conversation → Internet-enabled “his wife” follow-up → a standalone topic change about a different historical event. Both models used the history-aware planner and returned validated web citations; the topic-change query excluded the prior subject. Current 2000/5000 RUB limits provided headroom, so no budget settings were changed and the earlier temporary-increase request became unnecessary. Temporary verification chats/sessions were removed; incurred reservations remain in the ledger. This completes the previously budget-blocked live source-switch/query-resolution checks. Compaction stays in backlog.

## Model configuration and compatibility review — 2026-11-14

Add explicit per-model verified reasoning modes (none/low/medium/high), exact Chat Completions parameter mapping, legacy enabled/disabled compatibility, selected-chat controls and editable capability lists in Configuration. Keep role checks, immutable prior response snapshots, mode-aware caches and in-use capability guards. Use empty verified lists for current models unless model-specific support is established. Twelve bounded API probes accepted all four modes on YandexGPT/Alice/Alice Flash, but provided no reasoning telemetry or evidence of adjustable behaviour; request acceptance is not verification. See MODEL_COMPATIBILITY.md.

The account exposes legacy Lite aliases and the official notice retires Lite 5 on 2026-11-15. No chat selected Lite at review time. Remove only unused Lite entries through the versioned live API, retaining default/other chat selections; the server must reject removal if a concurrent chat starts using one. Fresh bootstrap and isolated test catalogs now use Alice Flash/Alice LLM. Never silently reroute a selected model or advertise reasoning levels that were not established. Broad quality review and compaction remain backlog. Next delivery: bounded full-page web retrieval; real venue/audio rehearsal remains pending.

Model configuration delivery completed: 130 automated tests and frontend build passed. Isolated browser checks verified all four canonical labels, per-model subsets, save/reopen, reset on model change and mobile layout. Production checks verified the capability editor, default-only controls for unverified models, rejection of an unverified high-mode request and successful ordinary generation. Removed the unused `yandexgpt-lite` live entry via the revisioned configuration API; all existing chat policies and the default model were preserved. Twelve bounded compatibility probes were accounted within existing limits; no budget settings changed. Explicit reasoning behaviour remains unconfirmed for current models, as documented in MODEL_COMPATIBILITY.md. Next planned delivery is bounded full-page web retrieval.

## Bounded public-page retrieval — 2026-11-14

After the existing paid/cached Yandex search, read up to two public result pages concurrently. Fetch HTML/plain text only, with no browser automation, JavaScript execution, login cookies, provider credentials, Referer or environment proxy inheritance. Check every redirect (maximum three) and DNS answer, reject non-public/mixed/transition addresses and self-host destinations, and pin each socket to an already validated public IP while retaining the original TLS hostname/certificate verification. Bound each page to 2 MiB, six seconds including queue/DNS/transfer/extraction, two global fetch slots and 40,000 extracted text characters. Skip scripts/navigation/hidden content, select query-relevant passages locally and retain the existing total source-context budget (5,500 characters, shared with RAG).

Use a private user/URL cache for extracted text (default ten minutes, bounded by response max-age); respect no-store/no-cache/max-age=0 and cache failed attempts briefly. No extra model reader call, new Search API query or paid-fetch reservation is added. Existing planning/search/answer reservations and caps remain. Failed/blocked/unsupported pages fall back to their actual search snippets with explicit labels; search-service failure itself still returns an error. This is an enrichment fallback, never a silent ungrounded answer.

Each saved citation records page-vs-snippet kind, exact allocated excerpt, original/final URL, search/read dates and fetch status. Page freshness, content identity and excerpt participate in source IDs and answer-cache identity. Popups and text exports label provenance; old citations remain accessible independently of cache expiry. Internet-off performs no page work. Cancellation stops parallel reads and saves no partial turn. Monitoring records fixed fetch outcomes/cache counts without page bodies/URLs or private identities.

WEB_PAGE_FETCH_ENABLED defaults true when live Internet search is configured; WEB_PAGE_CACHE_TTL_SECONDS defaults 600 (configuration range 30–3600). Both can be overridden in the existing unified environment; no new credentials are needed. Demo mode does not fetch external pages unless a test injects a mock reader. Next: observe real search-page availability and latency, then streaming text/audio. Real venue/audio rehearsal remains pending. Compaction and answer-quality review remain backlog.

Public-page delivery verified and deployed: 161 automated tests, frontend build, isolated page-text/fallback browser checks and production desktop/mobile checks passed. A live grounded reply read two search-result pages, cited actual page text, preserved the exact selected passage in the private source popup and labelled page provenance in Markdown export (~2.8 s in one smoke check, not a benchmark). A separate VM reader check loaded public HTML; a JavaScript-only shell produced insufficient_text and is eligible for labelled snippet fallback. Existing spending limits and chat policies were unchanged; temporary verification chats/sessions were removed. Predeployment backup passed isolated restore and S3 upload/download checksum verification. Next development stage: streaming text/audio; venue microphone/speaker rehearsal remains pending. Compaction and broad quality review stay backlog.

## Admin read-only access to all chats — 2026-11-15

Owner explicitly revised the previous privacy policy: enabled admins may view every user's complete chat messages. Implement dedicated admin-only paginated list and message-read endpoints and a Users → Conversations tab, with owner filtering, disabled-owner history, chronological messages and saved citation previews. Log message-read events with actor/chat/owner IDs and message count only, never text or source excerpts. Use authenticated message snapshots for web-source previews in the admin viewer; normal owner-scoped source URLs, exports, mutation and audio APIs remain owner-scoped. Editors still see foreign chat titles/settings only. Pagination must reach beyond the old editor catalog's 500-chat cap. Preserve existing chats and model policy. Update the sidebar privacy notice and configuration help to disclose admin read access. Guard against stale frontend responses and keep nested source-dialog keyboard focus on the topmost modal. No LLM/search/TTS work or budget charge is needed for viewing.

Admin-view delivery verified and deployed: 165 automated tests and frontend build passed. The reader uses one SQLite WAL snapshot and remains available while an owner generation holds its workflow lock; it shows saved turns only. Tests cover user/editor denial, current-role revocation, disabled-owner history, pagination beyond 500 chats, ownership enforcement on normal mutation/export/source routes, audit data minimization, and reading during in-flight generation. Isolated and production browsers passed owner filtering, foreign transcript/source preview, nested source-dialog focus, audit display and mobile layout. Production verification used a temporary synthetic user/chat and made no provider calls; fixtures and temporary sessions were removed. Updated privacy copy and AGENTS.md; refreshed the Russian three-page overview as 2026-11-15 while keeping the previous dated version. Predeployment backup passed isolated restore and verified off-VM checksum checks. Main-plan streaming work remains the next stage.

## User-controlled streaming text and phrase audio — 2026-11-15

Owner approved the plan, then authorized implementation. Add per-chat streaming_enabled and auto_speech flags, available to every owner through the composer; both default false. Classic mode remains available. Delivery preferences must survive reload/editorial changes and must not alter answer-cache identity or grant model/history policy privileges.

Use the shared Yandex Chat Completions adapter with stream=true, bounded incremental SSE parsing and strict successful finish/DONE checks. One authenticated POST stream carries status, text deltas, validated final answer, private audio-cache URLs, text completion and terminal/error events. Preserve origin/CSRF/session/owner checks, existing LLM reservations, context/history-aware retrieval and final atomic turn persistence. Failed partial replies are temporary UI only. Completed request IDs must retain their result even if delivery is cancelled after commit; retries do not regenerate text/audio automatically.

For grounded answers, extract only the top-level JSON answer string for clearly marked provisional display. Keep links disabled and TTS blocked until the existing full-answer citation/source validation passes, then replace the preview with the validated answer and links. This validates source identifiers/format, not semantic truth. Plain chat can synthesize before the whole answer finishes.

Initial audio transport uses bounded complete phrases through the existing SpeechKit REST adapter, sequential playback with a short browser scheduling lead, local user-scoped phrase caching and per-phrase budget reservations. No extra SDK/dependency is required. Native bidirectional SpeechKit synthesis is a later optimization if phrase continuity/latency warrants it; do not claim the initial transport uses a native bidirectional session. Bound auto-speech to 6000 characters, warn on truncation, preserve text if TTS fails/budget expires, support browser audio unlock, and provide independent Stop sound / Stop answer. Cancelling/revoking/disconnecting cleans up provider tasks and queued audio. Already incurred provider work remains conservatively accounted. Source-based speech begins after validation in this release.

User guide: ../docs/guides/2026-11-15-ai-talks-streaming-ru.md. Live dialog remains separately planned; compaction and broad quality review remain backlog. Real venue microphone/speaker rehearsal remains pending.

Streaming delivery verified: 188 backend tests passed, including mocked 5/10-user streams; frontend production build passed. Isolated browsers verified early text and scheduled audio while generation was still in progress, independent sound stop, cancellation without partial history, persisted controls, classic mode and mobile layout. Live HTTPS smoke verified incremental replies on configured Yandex models, authenticated WAV segments, no duplicate turn or repeated TTS for completed request retries, and provisional Internet text followed by validated citations. Deployment used a verified SQLite restore/S3 checksum backup.

Production browser verification also passed audio playback under the existing strict CSP, mode persistence after reload and mobile layout. Temporary synthetic chats/operator sessions were removed. Spending limits were unchanged. Real venue speaker/microphone rehearsal remains pending.

## Communication mode selector and stop-to-send voice — 2026-11-15

Replace prominent streaming/auto-speech switches with owner-controlled per-chat Text / Voice modes. Text uses manual sending and optional explicit TTS; new UI-created chats stream text by default. Voice uses streaming and automatic TTS, and an explicit stop-recording action transcribes and submits exactly once. Keep review_before_send as an optional secondary setting, plus classic whole-answer delivery in text mode only. Default new chats to text. Legacy selections map to voice only when both old flags were on; otherwise retain text/classic behaviour until changed. Persist nullable communication_mode for backward compatibility; explicit modes normalize delivery flags, editorial updates preserve preferences, and delivery/review preferences do not affect answer-cache identity.

Add Cancel recording: stop/discard capture with no STT or LLM request and retain the existing draft. Combine a pre-existing draft with recognized text; reject empty/failed STT and require editing above 4000 characters. Always require manual review after the recording duration limit because speech may have been cut off. Unlock playback during the original microphone gesture, before asynchronous STT. Source validation, budgets, cancellation and user/admin message privacy remain as documented.

This delivers button-controlled voice conversation, not hands-free live dialog. Silence/end-of-utterance detection, automatic listening for the next turn and echo-safe microphone transitions remain a separate future stage; spoken interruption is later still. Compaction and broad quality review remain backlog. User guide: ../docs/guides/2026-11-15-ai-talks-communication-modes-ru.md.

Communication-mode validation: 190 backend tests passed and frontend production build passed. Isolated desktop/mobile browsers verified automatic submission/audio, retained drafts, Cancel without STT/LLM, optional review/persistence, empty and failed recognition guards, forced duration-limit review, text/classic mode and legacy preference normalization. Predeployment backup passed isolated restore and S3 download checksum verification.

Production communication-mode verification passed for a temporary regular user: real browser PCM capture, exactly one automatic submission after explicit Stop, real Yandex LLM/TTS playback, Cancel with no recognition/send, persisted mode/review/classic controls and mobile layout under unchanged CSP. Recognition used a controlled transcript for the fake microphone, not a new live-STT quality test. Synthetic user/chat/session were removed; spending limits were unchanged. Both services are healthy.

## Built-in user manual — 2026-11-15

Add a rightmost question-mark help icon for every signed-in role. Open a scrollable Russian manual above the current chat, with a fixed close header, linked contents, Escape/focus trapping and focus return. Reading/closing help preserves draft, chat and settings and makes no paid requests. Cover chat/history/context/export/privacy; all four Internet/RAG combinations and shared bases; source popups and provisional citation checks; text/voice modes, review/classic controls, cancel/stop/audio unlock, recording/text/TTS limits; model/prompt/voice/speed/reasoning/context settings and role permissions; budget/connectivity troubleshooting. Explicitly distinguish current button-controlled voice from future hands-free live dialog.

Canonical source: ../docs/guides/2026-11-15-ai-talks-user-manual-ru.html. From the repository root run python3 docs/sync_user_manual.py after editing, then build the frontend; --check verifies the bundled copy matches. Trusted repository-owned HTML only, never user/model content. Local browser checks pass sections, TOC scrolling, heading/focus trap, close/Escape/focus return, retained draft, mobile viewport/scrolling and no paid calls; frontend production build passes. Frontend-only deployment preserves the backend and existing generation workflows.

Production manual verification passed for a temporary regular user: help icon, Russian sections, linked navigation and keyboard focus, Escape/close with draft preserved, mobile scrolling and close-button visibility, strict CSP and no paid requests. Temporary account/session removed. Backend was not restarted for this frontend-only release.

## Hands-free live dialog, retaining existing modes — 2026-11-15

Owner approved retaining Text, button-controlled Voice and classic text delivery while adding a third explicit Live dialog mode. communication_mode now accepts live; live_pause is normal/long (1000/1600 ms). Persist settings per chat, force streaming/auto-speech for live mode, preserve editorial source/delivery preferences and exclude silence tolerance from answer-cache identity. Selecting/restoring live mode never starts capture automatically.

Shared ConversationController owns capture, session epochs and lifecycle timers; stale getUserMedia/STT/reply callbacks cannot revive stopped sessions. WebRTC/libfvad with adaptive energy gating runs locally on bounded 20-ms/16-kHz frames, accepting >=200 ms speech with 260 ms pre-roll. Lazy same-origin 15.33 kB WASM and about 5 kB JS; @ozymandiasthegreat/vad pinned 2.0.7, no transitive dependencies. Public upstream license/authors/patents included. Caddy adds wasm-unsafe-eval solely for WebAssembly compilation; JavaScript unsafe-eval and external script/network origins remain excluded.

Explicit Start/Resume → listen/capture → release microphone → STT → normal streaming LLM/validated sources/TTS → await actual playback drain → 400-ms cooldown → listen. Pause/Stop are available in every live phase; stopping sound pauses live instead of reopening a microphone over playback. Playing an old answer pauses live as well. Source controls are locked while live is active; mode switching remains available and releases capture/cancels work. Optional review applies to button-controlled Voice, not every live turn. At the 24-second limit, always stop and leave the transcription for manual review with no LLM submission.

Pause on 60-second speech idle, 15-minute session ceiling, background/hidden tab, offline, access/session failure, microphone loss, empty/failed recognition, invalid citations, budget errors and blocked/failed audio. No automatic request retries. Explicit Start rejects an unsent draft rather than merging it into a live turn. Pause/stop cancel owned STT/stream requests and audio, guard late callbacks and retain saved history/drafts. Current actor authentication/CSRF/owner checks and conservative budget accounting remain unchanged; silence creates no provider calls. Streaming STT and spoken interruption remain future work.

Validation: 192 backend tests, 8 Node detector/controller tests, frontend build, real WebRTC speech fixture plus noise/click/silence controls, two-turn browser loop and lifecycle/failure/recording-limit tests pass. Prior button voice, text/classic, source and streaming tests remain green, including mocked 5/10-user server streams. User manual updated and synchronized. Physical microphone/speaker venue rehearsal remains pending and is separate from the deferred broad answer-quality review. Compaction remains backlog.

Live capture reuses the playback AudioContext unlocked by the initial user gesture, avoiding a new autoplay unlock requirement between turns. Capture owns and releases only its input nodes/tracks; Pause/Stop closes the shared playback context too. Startup cancellation releases acquired tracks even if AudioWorklet loading/resume is delayed. Explicit worklet shutdown prevents idle input processing while the shared context plays an answer.

Production live-dialog verification passed two automatic turns with a temporary regular user and real Yandex STT, LLM and TTS. The browser captured/resampled synthesized Russian speech as microphone input; recognition was not stubbed. Verified local lazy VAD under the deployed CSP, microphone off for playback, no overlapping turns, automatic re-listening, Pause release, persisted mode/pause settings without auto-start, updated manual, prior modes and mobile layout. Existing conservative reservations totalled 90 RUB for this bounded verification; limits were unchanged. Synthetic chat/user/session were removed; usage ledger remains accounted. Physical room/device echo and noise rehearsal is still pending.

## Owner feedback and next priority: spoken interruption — 2026-11-15

Owner confirms live dialog works and needs spoken interruption. First update project docs and the app manual; no interruption runtime change in this delivery. Current behavior is still alternating turns with microphone off during responses. Current workaround: Pause/Stop sound → resolve any retained draft → Resume → speak the clarification.

Next implementation priority is echo-aware spoken interruption in live mode, preserving all existing modes and an alternating-turn fallback. Detect confirmed user speech locally during playback, stop queued/current audio and synthesis, cancel unfinished generation, preserve utterance onset, then capture the new question exactly once using existing pause tolerance. Never submit assistant echo, weaken citation validation, silently discard cancelled-question context or treat partial answers as completed history. Define interrupted playback vs generation and saved-history/export behavior explicitly. Keep microphone status truthful and preserve session/role/owner/cost controls. See LIVE_DIALOG_PLAN.md section C for requirements and acceptance. Streaming STT follows later and is not required for interruption. Physical speaker/headset rehearsal is still needed; compaction and broad answer-quality review remain backlog.

Documentation-first interruption update verified: frontend build and manual source/bundle equality passed. Production regular-user popup checks confirmed the current workaround and clearly labelled planned interruption, navigation/Escape/focus return, retained draft and mobile layout, with no paid calls. Frontend-only redeploy; interruption behavior and backend were unchanged. README summaries were refreshed to the current three-mode state.
