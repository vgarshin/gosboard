# Backups and restore verification

## Current state

Daily local backups are enabled on `aitalks-test-01` using `aitalks-backup.timer`, scheduled at 03:15 UTC with up to five minutes of jitter and catch-up after downtime. The job was run successfully during deployment. Local snapshots are stored under `/var/backups/aitalks/daily`; predeployment snapshots have a separate `/var/backups/aitalks/predeploy` directory. Each category retains its latest seven successful archives.

Every run creates a consistent SQLite snapshot without sessions, OAuth state, caches, or idempotency response bodies; includes the non-secret application configuration; checks database integrity; and rehearses restoration in a temporary, network-disabled container. The rehearsal opens existing history, saves a synthetic turn, checks two-user isolation and settings persistence, and then discards the temporary database. It never replaces or mutates the production database. Configuration snapshots and per-chat settings are preserved in backups.

A checksum-verified predeployment archive was also copied temporarily to `/tmp/aitalks-offvm-safety`. Durable automated off-VM copies now go to the private Yandex Cloud bucket `aitalks-data`, under `aitalks/backups/daily/` (or `predeploy/` for that category).

Live S3 delivery is enabled and verified. The real systemd job completed with `status: success`, `off_vm: true`, and `remote.download_sha256_verified: true`. Upload, metadata/size checks, download SHA-256, and isolated restore checks passed. Storage returned custom metadata as `Sha256`; metadata keys are normalized case-insensitively without weakening checksum validation. The focused backup test suite passes all eight cases, including metadata-case variants.

## Configure S3

All credentials and settings now live in the same `.env`: the existing local `gosboard/.env` is deployed to `/etc/aitalks/production.env` on the VM. Both files have mode `0600`. The single source template is `.env.example`; there is no separate backup credentials file.

The bucket `aitalks-data`, Yandex HTTPS endpoint, and `ru-central1` region are already set. Fill `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the existing `.env` with dedicated static S3 credentials. The bucket remains private because archives contain personal text history. Application OAuth/model credentials are not S3 credentials. The current configuration has `BACKUP_LOCAL_ONLY=false` after the successful live trial. Set it to `true` only when intentionally switching to VM-only snapshots.

After future credential changes, run the service and verify its status:

```bash
sudoedit /etc/aitalks/production.env
sudo systemctl start aitalks-backup.service
sudo systemctl show aitalks-backup.service -p Result -p ExecMainStatus
sudo cat /var/backups/aitalks/status-daily.json
sudo systemctl list-timers --all aitalks-backup.timer
```

Success must include `off_vm: true` and `remote.download_sha256_verified: true`. Missing settings, failed uploads, or mismatched downloads fail the service; they are never reported as successful off-VM protection. Existing local backups remain available. The script uploads a SHA-256-tagged archive, checks its object metadata/size, downloads it again and verifies the checksum before rotating older remote files. Daily and predeployment prefixes have independent retention. No shared bucket lifecycle policy is modified.

The backup SDK is installed separately under `/opt/aitalks/backup-venv`; it runs independently of the chat server and adds no long-running service. The root-owned systemd backup service reads the shared production environment file and removes application credentials from its process environment. Compose uses explicit variable mappings, so only required credentials are injected. Stage 2A passes S3 credentials to the backend for private document ingestion/download. Keep the unified `.env` ignored and protected; never print rendered Compose configuration.

## Snapshot contents

Each archive contains only:

- `database.sqlite3`: portable, compacted, consistent database snapshot.
- `config/app.json`: effective administrator catalog/defaults at snapshot time.
- `manifest.json`: file checksums, UTC timestamp, backup category, and restore-check results.

Provider secrets, production environment files, TLS private keys, and transient audio are excluded. Keep a separate secure recovery copy of credentials. Stage 2A originals live in the same private bucket under `aitalks/documents/`. Do not expire that prefix with backup rotation. SQLite snapshots contain the catalog, chunks, embedding vectors and serialized FAISS indexes; local originals caches need not be restored. The isolated restore check validates FAISS checksums, dimensions and vector counts without network calls.

## Recovery

1. Select a verified archive. If using S3, download it through the configured private client and compare its SHA-256 with the object metadata. Check each file against `manifest.json` after unpacking into a protected recovery directory; do not extract arbitrary archives into the live application directory.
2. Run the isolated rehearsal before changing production. The automated job uses `scripts/verify_restore.py` with no network, no production volume, and fake model responses; a successful rehearsal is recorded in the manifest.
3. For an actual recovery, stop the backend deliberately and restore the database into an **empty** app data volume with ownership `10001:10001`. Restore the matching non-secret config and provision credentials from their secure external store. An empty volume avoids stale WAL files. Preserve the previous volume until verification completes.
4. Start the application, sign in again, open an existing chat, confirm its settings, and save a new turn. Restored sessions are deliberately invalidated. Do not overwrite a database while its process is running.

For rollback across Spaces, restore the previous images with a verified pre-Spaces database and its matching configuration. Earlier backends do not enforce space membership. A recovery snapshot taken after stopping traffic at cutover preserves the latest pre-migration state. After reopening the site, restoring that snapshot discards writes made after cutover.

## Manual off-VM safety copy

Use `scripts/pull_backup.py` to retrieve the latest verified snapshot over SSH into a destination outside this synced project:

```bash
python3 scripts/pull_backup.py \
  --ssh-config ~/.ssh/config \
  --destination /path/to/a/durable/private/backup-directory
```

The helper refuses overwrite, validates generated filenames, writes with mode `0600`, and compares local/remote SHA-256. Choose a durable location; `/tmp` is suitable only for a short-lived deployment safety copy. Use `--known-hosts` only when an explicitly maintained alternative SSH trust file is required.

Provider reference: [Yandex Object Storage S3 API and static key authentication](https://yandex.cloud/en/docs/storage/s3/index).

Role migration adds `access_accounts`, migration markers and audit records to SQLite snapshots. They are preserved alongside RAG chunk/top-K policy; restore verification checks that startup does not overwrite role/disabled state from bootstrap environment lists. Existing-history checks select an enabled owner so disabled access does not make a valid snapshot fail.

Live configuration revisions and their active pointer are also in SQLite. The copied `config/app.json` is now only the bootstrap; the database holds the current effective settings. An isolated restore explicitly checks preservation of the active runtime revision, roles and FAISS indexes.

Saved web citation metadata (URL, dated snippet, query and source identity) is retained with private messages. Offline restore checks reopen web-source previews when present in the sampled history; no web requests are made during restore. Search caches remain disposable and are excluded with other caches.
