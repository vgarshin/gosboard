# Prepare Yandex Search API credentials

The web-search adapter uses synchronous Yandex Search API v2 (`/v2/web/search`). Preparing credentials does not enable search in the app or make paid search requests.

1. Open [Yandex Cloud Console](https://console.yandex.cloud/) and select the existing project folder used for AI Studio. Confirm its cloud has an active billing account.
2. Under **Identity and Access Management → Service accounts**, create a dedicated service account, for example `aitalks-websearch`.
3. Grant that service account the folder-level role **`search-api.webSearch.user`** on the folder used for searches.
4. Open the service account, choose **Create new key → API key**, and set its scope to **`yc.search-api.execute`**. Choose an expiry suitable for operation and renewal. This is an **API key**, not an S3 static key or an OAuth application secret.
5. Copy the secret API-key value when shown and save it in the existing protected local `gosboard/.env`, in the prepared fields:

```dotenv
YANDEX_SEARCH_FOLDER_ID=your_folder_id
YANDEX_SEARCH_API_KEY=your_secret_api_key_value
YANDEX_SEARCH_API_KEY_ID=your_key_id_optional
```

Use the key's secret value for `YANDEX_SEARCH_API_KEY`, not its ID. Keep the existing AI Studio, SpeechKit, OAuth and S3 fields unchanged. Use the existing `.env`; no separate credentials file is needed. Do not paste secrets into chat. After filling it, tell me **search credentials saved**; I will transfer them securely, verify access with a bounded test, and implement the search adapter separately from the current UI/configuration work.

Only the dedicated search role/scope is needed for the proposed synchronous web-search adapter; do not grant broad folder editor/admin access to this service account. API access may also be constrained by organization policies. A provider-side billing budget alert can help monitor cost, while application request/spending limits will be implemented with the adapter.

Sources checked for this setup:
- [Synchronous text search: required service-account role and API-key scope](https://aistudio.yandex.ru/en/docs/search-api/operations/web-search-sync)
- [Getting started with Search API](https://aistudio.yandex.ru/en/docs/search-api/quickstart/)
- [Creating/managing API keys](https://yandex.cloud/en/docs/iam/operations/authentication/manage-api-keys)

## Verification status — 2026-11-14

Credentials are now installed and verified. The secret/key-ID fields were initially reversed and were corrected without printing their values. The successful verification returned three results. No further key creation is needed for the supplied active credential. The search adapter is now implemented; the Internet switch becomes active with WEB_SEARCH_ENABLED=true and valid credentials.

Production activation verified: the Internet switch is active; web-only and combined RAG/web conversations passed live source-citation and popup checks. No further credential setup is required.

## Public-page enrichment

No additional credentials are required. With live Internet search configured, WEB_PAGE_FETCH_ENABLED defaults true and reads bounded public HTML/plain-text passages from up to two results. WEB_PAGE_CACHE_TTL_SECONDS defaults 600. Override these non-secret settings in the same existing environment if needed; false restores snippet-only operation. Inaccessible/dynamic pages are explicitly labelled as snippet evidence in source previews. See RUNBOOK.md for limits and provenance controls.
