# Yandex model compatibility — 2026-11-14

## Deployment review

Default: `yandexgpt/latest`. Additional deployed entries: Alice AI LLM, Alice AI LLM Flash, and legacy YandexGPT Lite. At review time no chat selected Lite; existing chats selected the default or Alice LLM. Preserve those selections and the global default.

The official retirement notice lists YandexGPT Lite 5 retirement on 2026-11-15, with URI `yandexgpt-5-lite`; the account model list also exposes legacy `yandexgpt-lite/latest` aliases. Do not treat an alias remaining in model discovery as a guarantee of future availability. Remove unused Lite entries from the live selection catalog through the audited configuration API; if a concurrent chat starts using one, the existing in-use guard must stop removal. Alice Flash is already available and has passed ordinary and grounded chat checks. Fresh bootstrap configuration now lists Alice Flash and Alice LLM instead of Lite. Existing saved answer/model metadata is retained.

## Reasoning checks

The model-discovery API returned IDs and ownership metadata, with no supported-parameter/capability list. Twelve bounded API checks exercised four reasoning_effort values on each deployed current model:

| Model | `none`, `low`, `medium`, `high` accepted | Visible completion tokens | Reasoning telemetry | Established adjustable reasoning |
| --- | --- | --- | --- | --- |
| YandexGPT (`yandexgpt/latest`) | Yes | 3 on each check | No reasoning-token detail or reasoning-content field | Not confirmed |
| Alice AI LLM | Yes | 4 on each check | No reasoning-token detail or reasoning-content field | Not confirmed |
| Alice AI LLM Flash | Yes | 4 on each check | No reasoning-token detail or reasoning-content field | Not confirmed |

Checks used a short arithmetic question and all finished normally. Accepted requests alone do not establish that a model honors the parameter; identical short outputs do not establish that it ignores it either. Do not infer reasoning behaviour, depth or quality from these checks. The official reasoning documentation explicitly makes controls model-dependent and directs users to the model card. Keep unverified models on provider defaults until model-specific documentation or provider evidence confirms available controls. No hidden reasoning text was logged or stored. These are compatibility checks, not the deferred broad answer-quality review. Per-call application reservations were retained; no budget limits were changed.

## Configuration support

Each model now has an explicit `reasoning_efforts` list drawn from `none`, `low`, `medium`, `high`. Empty means no verified explicit controls; omit the request parameter and use provider defaults. Editors/admins may configure verified lists under Configuration → General settings, and choose an allowed level for a chat under Configuration → Conversation. Unsupported selections are rejected before generation. Existing legacy `disabled`/`enabled` values remain readable and map to `none`/`medium`; the old boolean is a compatibility fallback only when no explicit list exists. The in-use guard prevents removing a mode selected by an existing chat.

Keep capability metadata separate from whether the provider accepts a request. Do not automatically enable all four choices from a successful HTTP response. Future supported models can expose verified subsets without changing the shared adapter. Response metadata records the exact outgoing reasoning effort, and immutable configuration snapshots/cache identity retain the policy and selection. Changing capability lists does not rewrite prior responses.

## Sources

- [Available models](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Model-dependent reasoning controls](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/chain-of-thought)
- [Retirement and migration notices](https://aistudio.yandex.ru/ru/docs/ai-studio/release-notes/deprecations)

Delivery result: removed the unused Lite entry from the live catalog after rechecking selected chat policies. Existing chat settings and the default model were unchanged. All current deployed entries retain empty verified mode lists. Production ordinary generation and rejection of an unverified high-mode choice passed; capability UI and desktop/mobile checks passed. Automated suite: 130 passing tests. No budget limits changed.
