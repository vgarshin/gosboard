# Working on AI Talks / Gosboard

## Repository and current state

- This directory is the Git repository; run commands relative to it. Do not assume a parent workspace, personal absolute paths, event correspondence or files outside the clone are available.
- Read `README.md`, `docs/IMPLEMENTATION_STATUS.md` and the relevant document under `plans/` or `docs/operations/` before changes. Old roadmap/runbook delivery entries are historical; prefer current status and the latest owner decision.
- Preserve text, button-controlled voice, live dialog and classic text delivery. Spoken interruption is the next priority, not an implemented feature. Streaming STT is a later optimization, not a prerequisite. Compaction and broad answer-quality review remain backlog unless the owner changes scope.
- Keep changes within the requested scope. Local documentation/repository housekeeping does not imply deployment, a commit, a push or paid provider calls.

## Architecture and correctness

- Single FastAPI backend, React frontend, SQLite WAL and local FAISS; Docker Compose serves HTTPS through Caddy. In-process locks and limits assume one backend process. Do not introduce more services without evidence of need.
- Reuse provider adapters, conservative budget reservations, request IDs, cancellation ownership and user-scoped caches. Failed/cancelled work may still incur provider charges; do not silently release reservations or retry paid work automatically.
- Preserve bounded history/context and history-aware retrieval. Internet and RAG may work together. Never weaken full citation validation or speak unvalidated sourced answers.
- ConversationController owns live capture/timers/epochs. Microphone state must match the UI. Pause/Stop, backgrounding, mode/session changes and errors must release input and invalidate stale callbacks. Current live dialog turns the microphone off during processing/playback; do not claim spoken interruption works.
- Preserve public-only, DNS-pinned web-page fetching and its redirect/size/time limits.

## Access, secrets and data

- Administrators can read all users' messages only through the dedicated audited viewer. Editors see foreign chat titles/settings without message bodies. Normal mutation, export and audio routes stay owner-scoped; admin read access does not imply send-as-owner or foreign audio/export rights.
- Knowledge bases are shared among allowed users; do not introduce private bases by default.
- Use one ignored `.env` for application and backup credentials; production uses `/etc/aitalks/production.env`. Never print values, store them in examples or expose them to frontend code. Validate Compose with `config --quiet`, never a rendered credentialed config.
- Do not commit runtime databases, caches, recordings, backups, original knowledge documents, private chats, books, correspondence or local credential stores. Test fixtures must be synthetic or publicly licensed/attributed. Keep dependency locks and upstream license notices.
- Do not initialize Store/KnowledgeStore/create_app against a live database merely to inspect it: initialization can change pending-work state. Prefer authenticated APIs or deliberate read-only queries for authorized diagnostics.

## Documentation

- Keep only `README.md` and `AGENTS.md` as Markdown files at the repo root. Put feature plans in `plans/`, operational guides in `docs/operations/`, architecture notes in `docs/architecture/`, and user materials in `docs/guides/` or `docs/briefings/`.
- Maintain `docs/README.md` and `plans/README.md`. Update links when moving files. New documentation must not rely on the parent workspace.
- Write user-facing materials in plain Russian unless requested otherwise. Distinguish working features, proposals and future work. Do not claim automatic game scoring; judging requires actual team arguments and agreed criteria. Event mechanics require supplied approved requirements.
- The canonical in-app manual is `docs/guides/2026-11-15-ai-talks-user-manual-ru.html`. Update it when user controls, limits or behavior change; run `python3 docs/sync_user_manual.py` and verify `--check`. Commit the matching `frontend/src/user-manual-ru.html`; never edit only the generated copy or insert user/model HTML into the help component.
- Name new user documents `YYYY-MM-DD-ai-talks-<topic>-<language>.*`. Keep editable sources and reproducible builders beside exported PDFs, index them, and check requested page count, Cyrillic readability and layout. Never include secrets/private chats in shared documents.

## Verification

Run relevant checks from this repository root; use an external virtual environment:

```bash
PYTHONPATH=backend /tmp/gosboard-venv/bin/pytest -q tests
node --test frontend/tests/*.test.mjs
python3 docs/sync_user_manual.py --check
npm --prefix frontend run build
docker compose --env-file deploy/local-demo.env config --quiet
```

- Backend test dependencies: `backend/requirements-dev.txt`; legacy DOC parsing needs `antiword`. Use the checked-in dependency locks. Install Python/Node dependencies and caches in writable paths without touching secrets.
- Browser tests are optional Playwright/Chrome checks. Use the isolated `tests/live_preview.py` and documented ports; do not run demo-only fixtures against production. Simulated microphone/VAD tests do not replace a real venue microphone/speaker rehearsal.
- Choose checks appropriate to the change; do not add implementation-mirroring tests or repeatedly rerun unchanged checks. For user-interface changes verify desktop/mobile and keyboard focus as relevant.
- Before an authorized backend deployment, verify backup/restore and off-VM checksum delivery as described in the operations docs. Avoid backend restarts for frontend-only releases. Do not overwrite live environment/runtime configuration from examples.
- Before preparing a commit, inspect candidate files and ignore rules without printing secrets. Do not commit or push unless requested.
