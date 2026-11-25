# AI Talks / Gosboard

Lightweight Russian-language AI chat with text and voice conversations, persistent history, Internet search and shared document knowledge bases. Production: [aitalks.tech](https://aitalks.tech). Repository: [vgarshin/gosboard](https://github.com/vgarshin/gosboard).

## Features

- **Text:** manual sending, streamed replies, optional playback and classic whole-answer delivery.
- **Voice:** record and press Stop to send automatically; optional transcription review before sending.
- **Live dialog:** explicitly start listening, ask a question and pause; the app answers aloud and listens again after playback. Pause/Stop are always available. The microphone is off while answering.
- **Spaces:** membership controls working access. Each space has its own chats and a policy whose effective settings are captured when a chat is created. Removed or archived memberships leave owner-readable history.
- **Sources and documents:** managers upload PDF, TXT, DOC and DOCX. Space policies can combine several shared bases with web search. Citation validation precedes sourced speech. Active chat owners can open saved excerpts and follow public web links. Full RAG documents require management access. Exports contain the conversation text.
- **Configuration:** YandexGPT, Alice, DeepSeek and GigaChat model and prompt catalogs, voice settings, history limits, role management and budget controls. Runtime settings are stored in SQLite and changed through the application.
- **Operations:** local response/audio caching, bounded paid operations, cancellation, chat export (Markdown/TXT/JSON), verified local/S3 backups and restore checks.

Sign in with Yandex ID or VTB Edu. New accounts await administrator approval unless access was enabled in advance. Space membership and editor management assignments are configured under **Пользователи**. Editors manage the spaces and knowledge bases they own or are assigned. Administrators manage all spaces and bases, maintain the shared instruction for sourced answers, and can read user messages through an audited viewer. See the [role access matrix](docs/guides/2026-11-25-ai-talks-rbac-ru.md).

**Next priority: spoken interruption.** It is planned, not implemented. Current live dialog alternates listening and answering; use Pause/Stop sound, then Resume to clarify. Streaming recognition, Remote caching remains later work. Compaction and broad answer-quality review remain backlog. See the [live-dialog plan](plans/LIVE_DIALOG_PLAN.md).

## Repository layout

```text
backend/           Python/FastAPI, providers, auth, SQLite, retrieval
frontend/          React UI, audio capture/playback, local speech detection
config/            Non-secret bootstrap configuration
deploy/           Caddy, credential-free demo environments, VM/backup services
scripts/           Backup, restore and ingestion utilities
tests/             Backend/browser checks and attributed synthetic/public fixtures
docs/              User guides, implementation status, architecture and operations
plans/             Roadmap and feature plans
compose.yaml       Backend + frontend/Caddy deployment
```

## Quick start: credential-free demo

Run commands from the repository root. Docker Engine and Docker Compose are required.

```bash
docker compose --env-file deploy/local-demo.env up --build
```

Open **http://localhost**. Published ports bind to loopback. Demo mode returns labelled fixed model/transcription responses and silent test audio; it never calls paid providers and cannot run on a public production origin. Ports 80/443 must be available; use a separate Compose project/port configuration alongside another deployment.

## Live configuration and secrets

1. Copy `.env.example` to the ignored `.env` and fill the Yandex OAuth, AI Studio, SpeechKit and optional Search/S3 settings. Keep all application/backup credentials in this one file.
2. Restrict its permissions: `chmod 600 .env`. Never commit credentials or print rendered Compose environment values. Use `docker compose config --quiet` for validation.
3. Register the OAuth callback for your domain, for example `https://aitalks.tech/api/auth/callback`. Login uses the primary Yandex email. `ALLOWED_EMAILS` and `ADMIN_EMAILS` bootstrap enabled accounts; admins then manage users in the UI.
4. Follow the [operations runbook](docs/operations/RUNBOOK.md) for deployment, HTTPS, persistent volumes and recovery. Production uses `/etc/aitalks/production.env`; changing the live site is separate from local development.

[config/app.json](config/app.json) is a non-secret bootstrap. After first startup, SQLite revisions are authoritative; editors/admins use **Конфигурация**. RAG requires its object-storage and embedding configuration. See [web search setup](docs/operations/WEB_SEARCH_SETUP.md) and [backups](docs/operations/BACKUPS.md).

## Local development

Use Python 3.12, Node.js 24 and npm (matching the Docker images). Keep virtual environments and runtime data outside the checkout. Legacy DOC parsing needs `antiword` locally; the backend image includes it.

```bash
python3 -m venv /tmp/gosboard-venv
/tmp/gosboard-venv/bin/pip install -r backend/requirements-dev.txt
APP_ENV=development APP_ORIGIN=http://localhost:5173 DEMO_MODE=true \
  DATA_DIR=/tmp/gosboard-data CONFIG_PATH=config/app.json PYTHONPATH=backend \
  /tmp/gosboard-venv/bin/uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8011 --no-access-log
```

In a second terminal, from the repository root:

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open **http://localhost:5173** using that hostname for Origin checks. If the backend port changes, set `API_PROXY=http://127.0.0.1:NEW_PORT` when starting Vite. No paid credentials are needed for demo development.

## Checks

```bash
PYTHONPATH=backend /tmp/gosboard-venv/bin/pytest -q tests
node --test frontend/tests/*.test.mjs
python3 docs/sync_user_manual.py --check
npm --prefix frontend run build
docker compose --env-file deploy/local-demo.env config --quiet
```

Backend requirements and npm dependencies are locked. Optional browser checks need Playwright (`pip install -r tests/requirements-browser.txt`) and Chrome; the [runbook](docs/operations/RUNBOOK.md) describes the isolated live-dialog preview in `tests/live_preview.py`. A real microphone/speaker room rehearsal is still needed before event use; mocked load and browser fixtures do not prove venue performance.

## Documentation

- [Documentation index](docs/README.md): user guides, operations and architecture references.
- [Role-specific help source](docs/guides/2026-11-15-ai-talks-user-manual-ru.html): the app’s **?** menu selects the user, editor, or administrator version from the session role. After editing, run `python3 docs/sync_user_manual.py`; commit the updated bundled copy too.
- [Implementation status](docs/IMPLEMENTATION_STATUS.md): current features and historical delivery evidence.
- [Plans](plans/README.md): roadmap, spoken interruption and deferred compaction.
- [Agent instructions](AGENTS.md): repository scope, privacy, verification and documentation rules.

This repository is self-contained for building, testing and updating the manual. Private event requirements, books, correspondence, live chat data and credentials belong outside Git. Existing operational notes include deployment examples; provision your own host, domain, bucket and credentials for another installation.
