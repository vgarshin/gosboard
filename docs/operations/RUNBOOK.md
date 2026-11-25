# Stage 0 runbook

## Fill the configuration

1. Use the existing ignored `.env` as the single application/backup configuration source and deploy it securely to `/etc/aitalks/production.env` on the VM. Restrict both copies to `0600`; do not fill or commit `.env.example`. Compose explicitly injects only application variables into its containers; the backup service reads the same deployed file and unsets application credentials.
2. Register a **Yandex ID web login** application, grant `login:email` and `login:info`, and register the exact callback `https://aitalks.tech/api/auth/callback`. Fill `YANDEX_OAUTH_CLIENT_ID` and `YANDEX_OAUTH_CLIENT_SECRET`.
3. Fill `ALLOWED_EMAILS` with the primary email returned by each user's Yandex account. Fill `ADMIN_EMAILS` with a subset. Values are comma-separated, normalized to lowercase. Secondary email matching is intentionally not used.
4. Create Yandex Cloud service-account API keys with the model-invocation and SpeechKit recognition/synthesis permissions needed for the selected folder. Fill `YANDEX_CLOUD_FOLDER_ID`, `YANDEX_AI_API_KEY`, and `YANDEX_SPEECHKIT_API_KEY`. OAuth client credentials and service-account API keys serve different purposes.
5. Review `config/app.json`: the default model is `yandexgpt/latest`, voice `marina`, TTS model `general`. Verify model availability in your folder before changing it. Only administrators edit this mounted configuration; restart after changes. Bump `version` for each change. Old messages retain effective configuration metadata.
6. Review the current provider tariff and maximum request sizes against `LLM_RESERVE_RUB`, `STT_RESERVE_RUB`, and `TTS_RESERVE_RUB`. Then set `PROVIDER_BUDGET_REVIEWED=true`. Without this explicit setting, live startup fails closed.

The application uses global daily/monthly **reservation caps**, not exact invoice reconciliation. Each cache miss reserves a fixed conservative amount before contacting a provider. TTS reserves per bounded utterance. Failures and cancellations retain reservations because the provider may still charge. The starting values deliberately overestimate typical usage and may exhaust the app cap early. They do not guarantee a provider invoice ceiling; also set provider-side billing alerts/limits. Daily/monthly windows use UTC. The admin screen reports accumulated reservations; detailed rows are available through authenticated `/api/admin/usage`.

## Enable DeepSeek and GigaChat

On an installation with a saved catalog, open **Конфигурация → Общие настройки → Модели в списке** and add these entries to the current revision. Fresh installations load them from `config/app.json`.

| Catalog ID | Display label | Model |
|---|---|---|
| `deepseek-v4-1-flash` | DeepSeek V4.1 Flash (Yandex) | `deepseek-v4.1-flash` |
| `gigachat-3` | GigaChat 3 (Cloud.ru) | `cloudru/ai-sage/GigaChat3-10B-A1.8B` |
| `gigachat-3-pro` | GigaChat 3 Pro (Cloud.ru) | `cloudru/GigaChat/GigaChat-3-Pro` |

Leave reasoning controls at their defaults. Select the model in the space policy for new chats. Saved chat profiles retain their selected model.

DeepSeek uses the existing AI Studio key and folder. Confirm model access in that folder. [AI Studio model catalog](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)

Create a Foundation Models API key and set `CLOUDRU_API_KEY` in the same ignored application environment. Recreate the backend after changing its environment. [Cloud.ru Foundation Models quickstart](https://cloud.ru/docs/foundation-models/ug/topics/quickstart)

Review the models' tariffs and the shared LLM reservation before enabling them. Selecting GigaChat sends the bounded conversation context and, for sourced turns, selected source excerpts to Cloud.ru. Verify an ordinary reply and a sourced reply with synthetic content in both classic and streaming modes after activation. Keep GigaChat out of the November 27 event space policy per the event plan.

## Space access and RAG instruction

Use **Пользователи → Назначить пространства** for memberships. Admins see a separate **Управление пространствами** set of switches on editor accounts. Editors can manage membership for approved users in their managed spaces. Space policies remain under **Управление**.

The admin-only **Базы знаний → Инструкция RAG** tab saves the shared instruction in SQLite. It affects sourced replies in existing and new chats. Edit with care and check a sourced reply after changes. The application still enforces its citation rules. The backup carries the saved instruction.

## VM deployment

Production target: SSH alias `aitalks-test-01`. Configure this alias in your own SSH configuration and pass its actual path with `-F`; examples use `~/.ssh/config`.

1. Inspect existing services before installation or port changes. Prepare Docker Engine and Compose, a persistent SSD-backed Docker data directory, and an external backup destination. Do not run this app on ephemeral-only storage.
2. Point the `aitalks.tech` A record at the VM; configure an AAAA record only if IPv6 is working. Allow inbound TCP 80/443 and restrict SSH appropriately.
3. Copy the application source to a dedicated directory, e.g. `/opt/aitalks/app`. Exclude `node_modules`, local build output, caches, runtime data, letters, and secret files. Build from the `gosboard/` application directory.
4. If the loopback VM demo is running, stop its containers first (keep its volumes):

```bash
docker compose --env-file deploy/vm-demo.env down
```

Then start using the external environment file:

```bash
docker compose --env-file /etc/aitalks/production.env config --quiet
docker compose --env-file /etc/aitalks/production.env up -d --build
docker compose --env-file /etc/aitalks/production.env ps
```

Caddy obtains HTTPS certificates automatically when DNS and ports are correct. The backend exposes no host port. Run only one backend process: in-process request locks and concurrency limits assume that topology. Do not use `docker compose config` without `--quiet` on a credentialed setup, because rendered environment values would be printed.

Never deploy `DEMO_MODE=true` to a public origin. For local demo use the separate `deploy/local-demo.env` and project name; mock data never stands in for a real provider rehearsal.

## Current production deployment

Production is live at **https://aitalks.tech** on `aitalks-test-01`, source directory `/opt/aitalks/app`. Both domains have valid Let’s Encrypt certificates; `www.aitalks.tech` redirects to the main domain. DNS points to `153.51.165.91`.

The owner-supplied environment is installed at `/etc/aitalks/production.env` with mode `0600`. Use `sudoedit /etc/aitalks/production.env` on the VM for future edits. The file `/opt/aitalks/app/config/app.json` bootstraps the first runtime configuration. After migration, the active non-secret configuration lives in SQLite and is edited through the gear icon without restart. Restart the affected service after configuration changes. The provider budget review flag is enabled after tariff review; configured caps remain in force.

The former loopback demo containers are stopped, with their volumes preserved. Do not start the demo while production occupies ports 80/443. To run the demo again, first stop production deliberately, then use `deploy/vm-demo.env` and the SSH tunnel instructions in that file.

Live YandexGPT, TTS, and STT smoke checks passed. The public login button reaches Yandex sign-in; an allowed user still needs to complete the first actual login/callback. Venue trials remain outstanding; daily local/S3 backup, download checksum verification, and isolated restore checks are enabled. See BACKUPS.md.

## Daily use

- Sign in, create/select a conversation, and type a message or click the microphone to start recording. Click Stop to end recording; capture stops automatically before 25 seconds.
- Check/edit the transcript, then send. Press Enter to send, Shift+Enter for a newline.
- Click **Озвучить** for the chosen response. Replay reuses cached audio for up to 24 hours; expired audio can be generated again.
- The generation Stop control cancels the app's provider task. Already processed provider work can still be billed. A cancelled response is not saved as a half-turn.
- If the browser loses connection, reopen the chat to check whether the response completed before sending again. Request IDs prevent a direct retry of the same in-flight send from creating a second turn.
- Editors/admins use the gear icon → Конфигурация → Разговор for model, instruction, voice and reasoning settings. These controls are no longer above the composer; regular users retain the editorial policy. Changes apply to subsequent answers; old messages retain their effective configuration snapshot. Select a ready shared base and turn RAG on to use its documents. Turn it off for ordinary chat. Internet search remains disabled.
- The book icon manages shared content; the users icon opens admin user management; the gear opens editor/admin live configuration.

## Data and retention

Named `app_data` volume holds SQLite (text history, sessions, usage, local response/audio cache). Raw STT PCM stays in memory and is discarded after recognition. Generated audio expires after 24 hours; the shared local cache has a 1 GB cap and a minute-based cleanup cycle. Expired entries are inaccessible immediately; file-level forensic erasure is not guaranteed by SQLite. Text history persists until deletion. Deleting a chat also clears that user's derived caches/request results. Backups may retain earlier text until their retention expires.

Knowledge bases can be shared by space policy. Editors administer only the bases they own or are assigned. Space membership controls use of bases through a chat.

## Backup and restore

Keep backup files outside this synced project. `scripts/backup.py` uses SQLite's online backup API, removes caches, OAuth state, sessions, and request results, then compacts the backup. Usage reservations and message metadata remain.

Example on the VM, after copying the helper into the container:

```bash
docker compose --env-file /etc/aitalks/production.env cp scripts/backup.py backend:/tmp/backup.py
docker compose --env-file /etc/aitalks/production.env exec -T backend \
  python /tmp/backup.py /data/gosboard.sqlite3 /data/backups/aitalks-backup.sqlite3
docker compose --env-file /etc/aitalks/production.env cp \
  backend:/data/backups/aitalks-backup.sqlite3 /var/backups/aitalks/aitalks-backup.sqlite3
```

The snapshot is staged on the persistent volume because the container has only a small `/tmp` filesystem. Ensure free space for the source database plus snapshot/compaction, then clean up exported staging copies as part of backup rotation. Use a unique destination filename for each snapshot; the helper refuses overwrite. The installed systemd timer now handles daily snapshots and seven-copy retention; predeployment snapshots are separate. See BACKUPS.md for the live job and S3 activation. Copy the non-secret config separately. Automated S3 backup delivery is now active for `aitalks-data`; retrieval uses local SQLite/FAISS; S3 is used for ingestion and original-file cache misses. See BACKUPS.md.

Restore rehearsal: stop the backend, restore the compacted snapshot into an empty app volume with ownership `10001:10001`, and restart. Never replace the database while its process is running; use an empty volume so stale WAL files cannot accompany the snapshot. Confirm an existing chat opens and a new turn can be saved. Users must sign in again after restore. Preserve the old volume until the restore has been checked.

## Event rehearsal

Check actual microphones, projection, room speakers, and network. Measure uncached STT, LLM, and full-response TTS separately; stage 0 buffers complete responses/audio rather than claiming streaming latency. Check naturalness and duration of the generated historical demo with the team. Prepare and download an approved recording to the operator laptop and verify offline playback. Keep that media separate from expiring TTS cache.

Provider failure: show the error, check history, and switch to the local recording. Do not rely on a cached web page or VM connection as the offline fallback. Stage 0 cannot yet be declared event-ready until the actual user OAuth callback and venue trials pass.

## Provider references

- [Yandex ID authorization code and PKCE](https://yandex.ru/dev/id/doc/ru/codes/code-url)
- [Yandex ID user profile](https://yandex.com/dev/id/doc/en/user-information)
- [Yandex text generation API example](https://yandex.cloud/ru-kz/docs/foundation-models/prompts/yandexgpt/customer-service-generating-response)
- [SpeechKit REST TTS v3](https://aistudio.yandex.ru/en/docs/speechkit/tts/api/tts-v3-rest)
- [SpeechKit synthesis API fields](https://aistudio.yandex.ru/en/docs/speechkit/tts-v3/api-ref/Synthesizer/utteranceSynthesis)
- [SpeechKit synchronous STT](https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/request-examples)
- [Streaming microphone manual for later latency work](https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/microphone-streaming)

## Shared knowledge bases

Use the book icon (**Базы знаний**) to browse global materials. Administrators can create/delete bases, upload PDF, DOC, DOCX or UTF-8 TXT (10 MB maximum), inspect background progress/errors, retry failed jobs, and explicitly reindex. Everyone on the allowlist can read all bases and originals; personal histories remain private. Select a ready base inside the composer below the draft, then turn **RAG** on. Settings persist for that conversation.

Answers open `[D1]` markers in a popup over the current chat with the exact highlighted chunk and PDF page when available; **Скачать оригинал** downloads the authenticated original. Copied direct source URLs still work. No public S3 URL is exposed. Unsupported questions return an insufficient-evidence message. Citation validation checks IDs, not factual entailment; review substantive historical claims in the linked passage. Deletion removes access immediately, clears derived answer caches, and schedules original-object deletion (three attempts per startup). Backups retain earlier data until their retention expires.

The local FAISS index is a versioned SQLite blob, published atomically with chunks/catalog. The `app_data` volume also contains persistent embedding vectors and a 200 MB originals cache; originals remain durable in private S3 under `aitalks/documents/`. Serialized indexes use SHA-256 checks before loading. A restored snapshot includes indexes/vectors and can download originals from S3; preserve the document prefix separately from expiring backup prefixes. Do not apply backup lifecycle expiration to documents.

RAG uses paired `text-search-doc/latest` / `text-search-query/latest` Yandex embeddings. Actual model versions are recorded. A model/dimension mismatch fails safely: explicitly reindex the base. `RAG_MIN_SCORE` defaults to 0.2 and top context is bounded to five chunks / 5,500 characters; tune from representative questions rather than treating similarity as proof. Current limits: 20 bases, 100 active documents, 10,000 active chunks, 100 pages / 1 million extracted characters / 2,000 chunks per upload. Scans need external OCR; DOC/DOCX text extraction is supported; Word pagination is not inferred, so citations use chunks. Legacy DOC uses antiword; very short or unsupported legacy files should be resaved as DOCX.

Enable using `RAG_ENABLED=true` in the unified `.env`. Existing S3 settings are shared by backup and document storage; Compose passes these credentials to the backend because document ingestion/download now needs bucket access. Keep LangSmith tracing disabled. Missed embeddings reserve `EMBEDDING_RESERVE_RUB` (default 0.1 RUB) against the same global budgets; query vectors cache per user for one hour, document vectors persist, and source-version changes invalidate answer reuse. These reservations are estimates, not invoices.

For an initial trusted-operator seed, `scripts/seed_rag.py` runs inside the backend container with a PDF path. It uses an existing allowed administrator's temporary session, revokes it in `finally`, checks original checksums and a grounded response, and removes its synthetic chat. Normal content management uses the authenticated UI.


## Users, editors and administrators

Open **Управление** in the header. Administrators use **Пользователи** to add a primary Yandex email before first login, assign `user` / `editor` / `admin`, or disable access. Initial `.env` allowlists are imported once into SQLite; subsequent changes use this screen, and editing those bootstrap lists does not overwrite database roles. Credentials remain in the unified `.env`. All existing admin emails remain admins at migration; existing non-admin users become users and keep their histories/settings.

Users work in spaces where they have chat access. Editors can create spaces and knowledge bases, and manage resources they own or were assigned. Administrators can manage every resource and grant or revoke editor assignments. Chat access is separate from space management. Editors can see chat titles and settings in the spaces they manage. Administrators can read chat contents through the audited viewer. Chat and audio actions remain owner-scoped. Role changes or disablement revoke affected sessions.

Manage per-base search settings from **Базы знаний → Настройки поиска**. Administrators can also configure them in **Настройки → Настройки RAG**. **Top K документов** limits distinct source files; the separate chunk limit bounds the total fragments included. Context size may produce fewer sources. Chunk changes require the paid-reindex confirmation checkbox; top-K changes require no re-embedding. New defaults match existing chunking/retrieval, so deployment itself does not reindex books.

Voice choices include Marina (female) and Filipp (male). Voice changes apply to subsequent synthesis, with distinct audio cache keys; model settings apply to subsequent generated answers. Current catalog models have `reasoning_supported=false`: the UI shows that reasoning settings are unavailable. After separately verifying a model's native API support, an editor/admin may mark reasoning support on the model entry in the live configuration and save. The adapter then sends `reasoningOptions.mode=DISABLED` or `ENABLED_HIDDEN`; it does not invent low/medium/high effort or map effort to temperature. See [Yandex reasoning API](https://aistudio.yandex.ru/en/docs/ai-studio/operations/generation/async-request), [model release notes](https://aistudio.yandex.ru/en/docs/ai-studio/release-notes/) and [SpeechKit voices](https://aistudio.yandex.ru/en/docs/speechkit/tts/voices). Verify support for the exact model/version and endpoint before changing capability flags.

**Журнал** records account, model and RAG policy changes. It contains IDs/settings, not chat messages. Access roles, audit records and tuning are part of the SQLite backup. Restore checks preserve disabled accounts and roles and only open existing history for an enabled owner. Changes apply on the server immediately; an idle open chat refreshes its settings periodically. Reopen/reload a chat if another editor has just changed its settings.


## Live configuration and user controls

The header now has a book for content, a users icon for administrators, and a gear for editors/admins. The redundant model badge and duplicate read-only configuration screen are removed. The composer contains only the draft, RAG/web source controls, microphone and send actions.

**Пользователи → Добавить пользователя** opens a dedicated creation form. Existing emails are rejected as duplicates rather than silently overwritten. Each row has a sliding access switch; failures restore its previous visual state. **Изменить роль** edits an existing account separately. Access/role changes retain session revocation and last-admin protection.

**Конфигурация → Разговор** edits the selected chat's model, instruction, voice and supported reasoning settings. **Общие настройки** edits global defaults, model/prompt catalogs, generation/context bounds, TTS speed and the start example. Saves create immutable SQLite revisions and take effect for future requests without file edits/restarts. The screen distinguishes per-chat selections from global defaults. Old answers and in-flight responses retain their captured configuration. Existing chats prevent deletion of a catalog entry/capability they still use. Concurrent saves reject stale revisions; use **Загрузить актуальную версию** and review before retrying.

**История изменений** loads a previous configuration as a draft; it becomes active only after saving a new revision. **Настройки RAG** appears only for admins and selects a base to tune. Content upload/deletion/status stay in the book browser. Secrets and operational spending limits remain in the protected server environment; model/prompt/voice/generation configuration is live.

`config/app.json` is now a one-time bootstrap/fallback for a new database. Editing it after migration does not override saved live settings. Database snapshots include the active pointer and all runtime revisions. Restore checks verify the active revision is preserved even when the bootstrap file differs. The bootstrap file is still archived for empty-database setup; restore the database to recover the actual latest settings.

For the next web-search stage, follow [WEB_SEARCH_SETUP.md](WEB_SEARCH_SETUP.md) and fill the reserved search fields in the same ignored `.env`. This delivery makes no search calls.


## Prompt editing beside model selection

Editors/admins open **Конфигурация → Разговор** to select a model and prompt. **Создать промпт** and **Редактировать промпт** open the dedicated **Промпты** tab in the same configuration screen. Prompts can also be managed there without selecting a chat. IDs are generated automatically; enter only the name and text, then save. **Подставить в настройки разговора** returns the selection to the conversation form; click **Сохранить настройки разговора** to apply it.

Editing a shared prompt affects subsequent answers in chats using it. Old answers retain their saved configuration. The built-in **Обычный диалог** text is editable but cannot be deleted. A named prompt can be deleted after all conversations using it select another prompt; the UI shows its use count and the server enforces the rule. Refresh the prompt list after a conflicting save. All prompt changes create normal runtime revisions and are restored with the database.


## Internet search and combined sources

The **Интернет** sliding switch inside the composer now enables Yandex Search API for the current chat. Every allowed role can switch it independently of RAG. Choose a ready base and enable both for combined grounding; leave both off for ordinary chat. The flags persist per chat. Source failures return an error instead of silently pretending that source was used; turn a failing source off explicitly to continue without it.

One search is made for the current question (whitespace normalized, at most 400 characters); personal chat history is not sent to the search service. Results are up to five search snippets, with a 15-second overall bound and two concurrent search operations. No destination pages are fetched. Web query/result caches are per user for ten minutes by default. An uncached request reserves `SEARCH_RESERVE_RUB` against existing daily/monthly limits; cached searches and disabled web search make no new search reservations. Reservations are conservative allowances, not provider invoices.

Answers use **[W1]** for internet snippets and **[D1]** for RAG chunks. Clicking either opens the source popup over the chat. Web previews show the actual URL, query, retrieval date and the exact excerpt, explicitly labelled as a search-result snippet rather than a fetched full page. **Открыть страницу** opens the external site. Only URLs returned by search can become web citations; invented IDs/URLs are rejected. Search excerpts are treated as untrusted data. In combined mode, each source type receives part of the bounded context; up to five candidates of each type are supplied, with a 5,500-character excerpt budget and exact serialized-context fit checks.

Saved web excerpts are part of the owning chat's private message metadata, independent of short search-cache TTL. Other users/admins cannot open them through the citation-preview API. Deleting all owning chat references removes access. Source URLs can naturally change after retrieval; the dated saved snippet remains available with the response. Source freshness/identity and both mode flags are part of response-cache keys. Stop cancels the entire send pipeline, including search, embedding work or waiting for an LLM slot; already-processed work may still incur cost.

The backend reads the dedicated verified `YANDEX_SEARCH_API_KEY` and `YANDEX_SEARCH_FOLDER_ID` from the unified production environment. `WEB_SEARCH_ENABLED=true` enables the feature; missing credentials fail startup rather than showing a nonworking active switch. `SEARCH_CACHE_TTL_SECONDS` defaults to 600 and is bounded to 30–3600. `SEARCH_RESERVE_RUB` defaults to 10. The backup service strips search credentials from its environment. Search result snippets are the initial delivery; fetching and extracting full pages is a later, separately bounded feature.

## Live budget administration

Administrators: gear → Конфигурация → Бюджет. Refresh to see the current daily/monthly totals, remaining balance, reset times and operation/state breakdown. Save positive daily/monthly limits to apply immediately to future paid operations without restart. Limits are global across users. Concurrent admin edits return a conflict; refresh before saving again. Changes are audited and backed up in SQLite. Existing reservations remain; lowering below usage blocks further paid calls. Raising the daily limit does not bypass the monthly limit.

The protected environment's DAILY_LIMIT_RUB/MONTHLY_LIMIT_RUB now bootstrap the persistent policy only once; subsequent environment edits do not override the saved policy. Per-operation allowances still come from the environment. Limits reset by UTC calendar day/month (03:00 Moscow time). Pending, complete and uncertain reservations all count. A request can be rejected before the visible total reaches its limit because its entire required allowance must fit. Cache hits add no reservation. These are conservative spending controls, not Yandex invoice totals; failed/cancelled outcomes retain allowances. TTS reserves TTS_RESERVE_RUB for each text segment of up to 240 characters, so a long answer can need substantially more allowance than one text request.

## Operational monitoring

Admins: gear → Конфигурация → Мониторинг → Обновить мониторинг. Shows active paid API work/message handlers, HTTP route-template outcomes and mean/p95 latency, provider-operation outcomes and local cache hits/misses, plus current persistent budget totals. Metrics retain no message text, emails or raw path/query identifiers. Health and monitoring reads are excluded from HTTP counters. Metrics are process-local and reset after restart: mean uses all observations since start; p95 uses at most the latest 1,000 per series. Provider-operation duration includes any internal queue wait; HTTP timing stops at response creation and excludes client download/playback. Cache counters measure decision lookups, not responses; forced generation bypasses lookup. RAG counters cover query-vector cache, not FAISS/document embedding reuse. No external monitoring dependency is required. Budget data continues to persist and is not reset with metrics. See REHEARSAL.md for automated results and pending real-device acceptance.

## Chat export

Open a conversation with messages → download icon beside its title → choose Markdown (default), JSON or TXT → Скачать. All formats use UTF-8 and preserve saved citation excerpts and links. Markdown is recommended for reading/sharing; JSON includes schema_version=1, chat settings and message metadata for structured processing; TXT is plain text. No audio or original document files are embedded, and JSON import is not implemented. RAG links require sign-in and an available document; exported excerpts remain readable without the app. Every role may export only its own chats. Export makes no provider calls or budget reservations. Downloads are authenticated attachments with no-store caching; once downloaded, files are managed by the user.

## Shared Yandex text-generation adapter

Text chats now use Yandex's OpenAI-compatible Chat Completions endpoint via the existing httpx client. This is still Yandex hosting/billing and needs no OpenAI account or new SDK. Existing AI key/folder variables remain unchanged; API key authentication and OpenAI-Project identify the same folder as the full model URI. Requests set store=false and max_completion_tokens. Chat settings/history, RAG/web grounding and SpeechKit/embeddings remain in place. Answer cache namespace llm-v5 includes the protocol; historical answers are unchanged. Empty, refused, tool-call and truncated outputs return an error and are not saved/cached as valid turns; incurred reservations remain.

Editors/admins select Alice AI LLM or Alice AI LLM Flash from Configuration → Conversation, then save the selected chat. The default model and existing selections are not automatically changed. New Alice entries use provider-default reasoning; confirm that the selected model supports the exact Chat Completions reasoning parameters before marking it capable. Existing explicit on/off choices map to medium/none, while default omits reasoning_effort. Model IDs are configured inside the current Yandex folder. Per-operation allowances remain conservative application reservations, not actual per-model pricing.

## History in model context

Full chat history is persisted independently of what fits a model request. Every request includes the current instruction, optional fresh RAG/web source context, whole recent user/assistant exchanges and the new question. Admins: Configuration → Conversation → selected chat → Предыдущих сообщений в контексте → Save. Default 20 previous messages means 10 exchanges; use an even number 0–100. Zero disables sending past turns but retains the transcript. Editors/users cannot change this count. The existing context character limit remains an upper bound, so large sources/instructions or a large latest turn can reduce the actual count to zero. Oldest history is omitted first; never truncate a turn to fit. New answers show the actual included history count, also saved in metadata with the configured limit and context size. Old answer metadata is not rewritten.

This is client-managed history sent on each stateless completion request; store=false does not disable app history. Source retrieval still searches the latest question only, so use an explicit topic when asking RAG/web follow-ups. Compaction is not enabled; ../../plans/COMPACTION_PLAN.md describes future options and tradeoffs.

## Enabling sources during an existing conversation

Grounded replies explicitly request Chat Completions JSON-object output. Their instruction includes the exact current source IDs and a matching example, and distinguishes earlier conversational replies from fresh source evidence. Old D/W citation markers are removed only from historical assistant text sent to the model; stored messages, source popups and exports stay intact. Ordinary replies continue in text mode. Citation validation remains strict; invalid outputs are not saved or cached as answers. Fixed validation categories are logged without model output or private conversation text. No automatic extra generation/repair calls were added. Compaction is deferred to backlog.

## Context-aware retrieval questions

When RAG or Internet is enabled and the user asks a context-dependent follow-up (for example “Кто его жена?” after discussing Trump), the app first resolves the missing entity from permitted recent chat history. Both RAG and Internet receive the same standalone question, e.g. “Кто жена Дональда Трампа?”. The original question stays in the transcript and the final answer receives normal chat context. Search gets only the resolved question, not the full history. The actual search query is visible in source previews and response retrieval metadata.

In chats with sources enabled, the selected model decides whether to answer directly, search, or ask a clarifying question. It sees the current message, the enabled source types, and recent whole turns within the configured history and context limits. Search receives only the standalone query. Each routing cache miss reserves one LLM allowance under `query-resolution`, including first turns and chats with history disabled. Routing decisions are cached per user, chat, model and input for ten minutes. Direct answers and sourced generation retain their own reservations. An invalid routing response stops the turn. A search with insufficient evidence keeps the sourced-response policy. Demo mode supplies a fixed search decision to exercise the retrieval pipeline with test data.

## Verified reasoning modes and Lite retirement

Configuration → General settings contains a verified reasoning-mode list for the default and each catalog model. Mark only modes confirmed by model-specific provider information. Configuration → Conversation displays that model's allowed subset; default omits reasoning_effort. The labels Выключены / Низкий / Средний / Высокий send none / low / medium / high exactly. Empty capability lists show «Режимы не подтверждены» and keep the provider default; this does not assert that the model is incapable of reasoning. Successful HTTP acceptance alone does not prove a setting has an effect. Current probes did not establish adjustable reasoning on deployed YandexGPT/Alice models, so no explicit modes were automatically enabled. Legacy on/off data remains readable. Removing a mode used by an existing chat is blocked until that chat selects another supported setting. Prior answers retain their original metadata.

Unused Lite entries are removed because of the provider retirement notice; existing selected models and the global default are preserved. Fresh bootstrap uses Alice text models. Live configuration is authoritative after bootstrap, and catalog updates are audited/versioned. ../architecture/MODEL_COMPATIBILITY.md records the review, evidence and limitations.

## Reading search-result pages

With Internet on, the app now attempts to read the first two search-result URLs and selects relevant passages for the model. The total source text budget stays at 5,500 characters, shared with enabled RAG. A citation marked «Интернет · текст страницы» contains selected plain text from an actually read page; the popup shows the page-read date and whether its content came from cache. «Поисковый фрагмент» means the evidence is the search result snippet. Blocked, slow, JavaScript-only, oversized or unsupported pages use explicitly labelled snippet fallback. The message shows how many readable page passages were prepared; the citation list identifies which evidence the model actually used. Search API failures still stop the turn rather than pretending to have searched.

Bounds: two result pages per question, two global fetch slots, six-second per-page deadline including queue, maximum three redirects, 2 MiB raw transfer per page, 40,000 extracted text characters. HTML/plain text only; no JavaScript, authenticated browsing, PDF extraction or link crawling. Each destination and redirect is validated; DNS must contain only allowed public addresses and the connection is pinned to a validated IP with normal TLS verification and the original hostname. No provider/session credentials, cookies, Referer or environment proxy settings go to page sites. No additional LLM reader or paid Search request is made for extraction.

Optional settings in the same protected environment: WEB_PAGE_FETCH_ENABLED=true and WEB_PAGE_CACHE_TTL_SECONDS=600 (30–3600). Set the first false to return to snippet-only search without disabling Internet. Cache is per-user/per-URL, respects response no-store/no-cache and a lower max-age, and stores failed fetch status for 60 seconds. Cache hits/fetch outcomes appear in Monitoring. Saved private citations contain only the selected excerpt and provenance, not full downloaded HTML, and survive cache expiry. MD/TXT/JSON exports retain source identity; page-vs-snippet labels are explicit. Old snippets remain readable. Deleting the owning chat removes its source references. Source URLs/excerpts never enter operational logs.

## Admin read-only chat review — policy updated 2026-11-15

Admins: Users icon → Пользователи → Разговоры. The paginated list includes all users and accounts with disabled access. Filter by owner, select a conversation and read its messages in chronological order. Refresh explicitly to load newly saved messages. The view offers no composer, delete, export or speech-generation actions for another user's chat. RAG sources retain normal shared-base access; web previews use the exact saved citation from the authorized admin transcript. Opening the original public URL remains available. Normal direct web-source routes outside this viewer remain owner-scoped.

GET /api/admin/chats accepts owner (user ID), offset and limit (1–100, default 50). GET /api/admin/chats/{id}/messages requires an enabled admin at request time and logs chat.read with actor/chat/owner IDs and message count. It returns no-store content. Reads do not call paid providers or change conversation history/model settings. Revoked or demoted accounts lose endpoint access immediately. Editors retain title/settings access only, and ordinary users retain their own chat access. Normal chat writes/deletes/exports/audio APIs continue to enforce ownership for every role. The current privacy notice states that messages are visible to their owner and administrators, superseding earlier owner-only descriptions in historical documents.

## Streaming delivery — 2026-11-15

Composer switches **Потоковый ответ** and **Озвучивать автоматически** are owner-controlled, persisted per chat and default off for existing/new chats. Classic mode ignores auto_speech while retaining its preference. No credentials, environment changes or new dependencies are needed. PATCH /api/chats/{cid}/sources now accepts these flags and preserves omitted source/delivery fields. Editorial model changes preserve both flags. Delivery flags are excluded from LLM cache identity.

POST /api/chats/{cid}/messages/stream uses the normal MessageInput/session/CSRF/owner checks and requires streaming_enabled. SSE events: status, delta (provisional for grounded output), answer (validated/persisted), text_done, audio (private expiring cache URL), audio_error (text continues), done, error. Keepalive comments are emitted during waiting. Caddy's SSE response forwarding must be verified live; do not expose the backend port. Malformed/truncated/refused provider streams never become completed turns. Fully saved request results survive cancellation during delivery. A retry of a completed request returns its result with no repeated speech. No automatic reconnect/regeneration is performed.

Only the top-level answer field of the structured source reply is previewed. It remains visibly provisional with no active citations; full validation is required before active links, history persistence or TTS. Native early sourced audio requiring segment-level validation is deferred. Citation validation is not a semantic fact checker.

Speech is delivered as bounded WAV phrases using the existing REST synthesis adapter, not a native bidirectional gRPC session. First complete phrase may start at >=80 characters; subsequent phrases target >=160, capped at 240. Queue and total speech input are bounded; auto-speech covers at most 6000 characters and warns for longer replies. Per-phrase user-scoped tts-v1 cache is shared with identical classic utterances; TTL/budget/slot controls remain. Phrase boundaries can increase reservation count relative to full-text splitting. Frontend decodes/schedules in order with a short playback lead; a gesture recovery button appears when the browser suspends audio.

POST /api/requests/{rid}/audio/stop cancels only that owner's synthesis/queued audio; normal request cancel stops text and sound. Session/role revocation cancels active streaming work too, including TTS after text completion. Client disconnect cancels the producer; pending paid operations remain uncertain. TTS failures/exhausted allowance retain completed text. Client sends repeated request IDs after transport failure, never automatically starts another paid turn. Completed responses use the same history and export formats as classic chat. See the [Russian user guide](../guides/2026-11-15-ai-talks-streaming-ru.md).

## Communication mode selector and stop-to-send voice — 2026-11-15

Replace prominent streaming/auto-speech switches with owner-controlled per-chat Text / Voice modes. Text uses manual sending and optional explicit TTS; new UI-created chats stream text by default. Voice uses streaming and automatic TTS, and an explicit stop-recording action transcribes and submits exactly once. Keep review_before_send as an optional secondary setting, plus classic whole-answer delivery in text mode only. Default new chats to text. Legacy selections map to voice only when both old flags were on; otherwise retain text/classic behaviour until changed. Persist nullable communication_mode for backward compatibility; explicit modes normalize delivery flags, editorial updates preserve preferences, and delivery/review preferences do not affect answer-cache identity.

Add Cancel recording: stop/discard capture with no STT or LLM request and retain the existing draft. Combine a pre-existing draft with recognized text; reject empty/failed STT and require editing above 4000 characters. Always require manual review after the recording duration limit because speech may have been cut off. Unlock playback during the original microphone gesture, before asynchronous STT. Source validation, budgets, cancellation and user/admin message privacy remain as documented.

This delivers button-controlled voice conversation, not hands-free live dialog. Silence/end-of-utterance detection, automatic listening for the next turn and echo-safe microphone transitions remain a separate future stage; spoken interruption is later still. Compaction and broad quality review remain backlog. User guide: ../guides/2026-11-15-ai-talks-communication-modes-ru.md.

Communication-mode validation: 190 backend tests passed and frontend production build passed. Isolated desktop/mobile browsers verified automatic submission/audio, retained drafts, Cancel without STT/LLM, optional review/persistence, empty and failed recognition guards, forced duration-limit review, text/classic mode and legacy preference normalization. Predeployment backup passed isolated restore and S3 download checksum verification.

Production communication-mode verification passed for a temporary regular user: real browser PCM capture, exactly one automatic submission after explicit Stop, real Yandex LLM/TTS playback, Cancel with no recognition/send, persisted mode/review/classic controls and mobile layout under unchanged CSP. Recognition used a controlled transcript for the fake microphone, not a new live-STT quality test. Synthetic user/chat/session were removed; spending limits were unchanged. Both services are healthy.

## Built-in user manual — 2026-11-15

Add a rightmost question-mark help icon for every signed-in role. Open a scrollable Russian manual above the current chat, with a fixed close header, linked contents, Escape/focus trapping and focus return. Reading/closing help preserves draft, chat and settings and makes no paid requests. Cover chat/history/context/export/privacy; all four Internet/RAG combinations and shared bases; source popups and provisional citation checks; text/voice modes, review/classic controls, cancel/stop/audio unlock, recording/text/TTS limits; model/prompt/voice/speed/reasoning/context settings and role permissions; budget/connectivity troubleshooting. Explicitly distinguish current button-controlled voice from future hands-free live dialog.

The [canonical help source](../guides/2026-11-15-ai-talks-user-manual-ru.html) contains shared sections and a table of contents with role-specific entries marked by `data-roles`. The popup selects sections from the current session role. Run `python3 docs/sync_user_manual.py` after editing, check it with `--check`, and rebuild the frontend. The popup renders repository-owned HTML. Local browser checks pass sections, TOC scrolling, heading/focus trap, close/Escape/focus return, retained draft, mobile viewport/scrolling and no paid calls; frontend production build passes. Frontend-only deployment preserves the backend and existing generation workflows.

Production manual verification passed for a temporary regular user: help icon, Russian sections, linked navigation and keyboard focus, Escape/close with draft preserved, mobile scrolling and close-button visibility, strict CSP and no paid requests. Temporary account/session removed. Backend was not restarted for this frontend-only release.

## Hands-free live dialog, retaining existing modes — 2026-11-15

Owner approved retaining Text, button-controlled Voice and classic text delivery while adding a third explicit Live dialog mode. communication_mode now accepts live; live_pause is normal/long (1000/1600 ms). Persist settings per chat, force streaming/auto-speech for live mode, preserve editorial source/delivery preferences and exclude silence tolerance from answer-cache identity. Selecting/restoring live mode never starts capture automatically.

Shared ConversationController owns capture, session epochs and lifecycle timers; stale getUserMedia/STT/reply callbacks cannot revive stopped sessions. WebRTC/libfvad with adaptive energy gating runs locally on bounded 20-ms/16-kHz frames, accepting >=200 ms speech with 260 ms pre-roll. Lazy same-origin 15.33 kB WASM and about 5 kB JS; @ozymandiasthegreat/vad pinned 2.0.7, no transitive dependencies. Public upstream license/authors/patents included. Caddy adds wasm-unsafe-eval solely for WebAssembly compilation; JavaScript unsafe-eval and external script/network origins remain excluded.

Explicit Start/Resume → listen/capture → release microphone → STT → normal streaming LLM/validated sources/TTS → await actual playback drain → 400-ms cooldown → listen. Pause/Stop are available in every live phase; stopping sound pauses live instead of reopening a microphone over playback. Playing an old answer pauses live as well. Source controls are locked while live is active; mode switching remains available and releases capture/cancels work. Optional review applies to button-controlled Voice, not every live turn. At the 24-second limit, always stop and leave the transcription for manual review with no LLM submission.

Pause on 60-second speech idle, 15-minute session ceiling, background/hidden tab, offline, access/session failure, microphone loss, empty/failed recognition, invalid citations, budget errors and blocked/failed audio. No automatic request retries. Explicit Start rejects an unsent draft rather than merging it into a live turn. Pause/stop cancel owned STT/stream requests and audio, guard late callbacks and retain saved history/drafts. Current actor authentication/CSRF/owner checks and conservative budget accounting remain unchanged; silence creates no provider calls. Streaming STT and spoken interruption remain future work.

Validation: 192 backend tests, 8 Node detector/controller tests, frontend build, real WebRTC speech fixture plus noise/click/silence controls, two-turn browser loop and lifecycle/failure/recording-limit tests pass. Prior button voice, text/classic, source and streaming tests remain green, including mocked 5/10-user server streams. User manual updated and synchronized. Physical microphone/speaker venue rehearsal remains pending and is separate from the deferred broad answer-quality review. Compaction remains backlog.

Live capture reuses the playback AudioContext unlocked by the initial user gesture, avoiding a new autoplay unlock requirement between turns. Capture owns and releases only its input nodes/tracks; Pause/Stop closes the shared playback context too. Startup cancellation releases acquired tracks even if AudioWorklet loading/resume is delayed. Explicit worklet shutdown prevents idle input processing while the shared context plays an answer.

Reproduce isolated live browser checks from the project root with the test venv: `PYTHONPATH=backend python tests/live_preview.py`, then `API_PROXY=http://127.0.0.1:8031 npm --prefix frontend run dev -- --port 5196`. Run `browser_live_dialog.py`, `browser_live_faults.py` and `browser_live_startup.py` with Playwright/Chrome installed. Tests use the attributed public speech fixture, controlled STT responses and demo model/audio; no real account data or paid calls. Prior browser tests accept TEST_BASE_URL=http://localhost:5196. Node checks: `node --test frontend/tests/*.test.mjs`.

Production live-dialog verification passed two automatic turns with a temporary regular user and real Yandex STT, LLM and TTS. The browser captured/resampled synthesized Russian speech as microphone input; recognition was not stubbed. Verified local lazy VAD under the deployed CSP, microphone off for playback, no overlapping turns, automatic re-listening, Pause release, persisted mode/pause settings without auto-start, updated manual, prior modes and mobile layout. Existing conservative reservations totalled 90 RUB for this bounded verification; limits were unchanged. Synthetic chat/user/session were removed; usage ledger remains accounted. Physical room/device echo and noise rehearsal is still pending.

## Owner feedback and next priority: spoken interruption — 2026-11-15

Owner confirms live dialog works and needs spoken interruption. First update project docs and the app manual; no interruption runtime change in this delivery. Current behavior is still alternating turns with microphone off during responses. Current workaround: Pause/Stop sound → resolve any retained draft → Resume → speak the clarification.

Next implementation priority is echo-aware spoken interruption in live mode, preserving all existing modes and an alternating-turn fallback. Detect confirmed user speech locally during playback, stop queued/current audio and synthesis, cancel unfinished generation, preserve utterance onset, then capture the new question exactly once using existing pause tolerance. Never submit assistant echo, weaken citation validation, silently discard cancelled-question context or treat partial answers as completed history. Define interrupted playback vs generation and saved-history/export behavior explicitly. Keep microphone status truthful and preserve session/role/owner/cost controls. See ../../plans/LIVE_DIALOG_PLAN.md section C for requirements and acceptance. Streaming STT follows later and is not required for interruption. Physical speaker/headset rehearsal is still needed; compaction and broad answer-quality review remain backlog.

Documentation-first interruption update verified: frontend build and manual source/bundle equality passed. Production regular-user popup checks confirmed the current workaround and clearly labelled planned interruption, navigation/Escape/focus return, retained draft and mobile layout, with no paid calls. Frontend-only redeploy; interruption behavior and backend were unchanged. README summaries were refreshed to the current three-mode state.
