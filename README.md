# image-generation-api

Provider-aware image generation, editing, and Responses tooling for GPT Image and xAI/Grok Image through OpenAI-compatible or third-party gateways. Configure the API once, persist a deterministic numbered catalog, then select a model with one number. Only `gpt-image-*` and `grok-imagine-image*` enter the executable catalog or image requests.

`IMAGE_GENERATION_*` is canonical. `GPT_IMAGE_*` and `OPENAI_*` remain compatible aliases. `gpt-image-2` stays in the built-in registry and remains explicitly selectable even when the current gateway does not list it.

## Configure

```dotenv
IMAGE_GENERATION_API_KEY=your-third-party-key
IMAGE_GENERATION_BASE_URL=https://your-provider.example/v1
IMAGE_GENERATION_MODEL=gpt-image-2
IMAGE_GENERATION_VENDOR=openai
IMAGE_GENERATION_MODEL_CATALOG=~/.codex/image-generation-api-model-catalog.json
IMAGE_GENERATION_RESPONSES_MODEL=gpt-5.4
IMAGE_GENERATION_TOOL_MODEL=gpt-image-2
IMAGE_GENERATION_TIMEOUT=180
IMAGE_GENERATION_RETRIES=2
IMAGE_GENERATION_RETRY_DELAY=1
IMAGE_GENERATION_USER_AGENT=gpt-image-client/1.0
IMAGE_GENERATION_PROVIDER_PROFILE=auto
IMAGE_GENERATION_ROUTING_MODE=auto
IMAGE_GENERATION_TOOL_MODEL_POLICY=auto
```

On Windows, user-level values under `HKCU\Environment` are read directly, so a new global value is available to already-running shells. The old `GPT_IMAGE_*` variables remain valid. Legacy model, vendor, provider-profile, and routing-mode preferences are retained only for diagnostics; they do not silently replace the no-choice `gpt-image-2` default. The catalog contains model metadata and numbers only; it never stores the API key.

## Configure and Select

```powershell
python .\scripts\image_generation_api.py configure
python .\scripts\image_generation_api.py select-model
python .\scripts\image_generation_api.py select-model --choice 2
python .\scripts\image_generation_api.py models --image-only
```

Configuration and selection rules:

- `configure` makes exactly one read-only `GET /v1/models` call and writes the numbered catalog;
- the catalog includes every currently executable GPT Image and Grok Image model, with all OpenAI entries first and all xAI/Grok entries second;
- Alibaba/Qwen and other recognized vendors are reported by `models` as discovery-only and are never executable;
- `--choice N` resolves the same number in `generate`, `edit`, and `responses` without a network request;
- a number supplied with the request, or as the next standalone reply for a pending request, maps directly to `--choice N` without another vendor/model prompt;
- numbers remain stable until the next explicit `configure`, and the catalog is bound to its Base URL;
- without `--choice`, the image model is always `gpt-image-2` (legacy model environment variables do not override it);
- explicit `--model`/`--tool-model` and explicit routing modes remain compatibility overrides;
- no automatic fallback or second model-selection prompt is performed.

`select-model` reads the local catalog only. `generate --dry-run` is also offline; neither command refreshes the provider inventory.

Supported convenience aliases include `gpt2`, `gpt2.5`, `gpt4k`, `grok`, `grok2`, and `grok-quality`. They are never sent upstream as-is; output records both the input alias and the resolved provider id.

`select-model` exposes one full `choices` array plus compact `recommended_choices` and `more_choices` number indexes. Each choice contains a stable number, vendor, model id, status, capabilities, and aliases. Proven models are recommended first; retired or unverified gateway aliases remain visible but are never silently selected.

`models` is a read-only discovery call. Missing `gpt-image-2` is a warning when another image model exists; the local default and built-in capability are not removed.

In `models` output, `image_model_count` counts every discovered image candidate, while `executable_image_model_count` and `vendor_count` cover only the enabled GPT/Grok execution set. `discovery_only_image_model_count` reports the remainder.

Discovery reads nested/camelCase vendor and capability fields, ignores obvious video-only models and video tasks, and honors explicit negative image-capability flags.

## Generate

```powershell
python .\scripts\image_generation_api.py generate `
  --prompt "A clean product hero image" `
  --preset quality `
  --output ".\output\imagegen\hero.png" `
  --dry-run
```

Use `--choice N` to select a configured model, or omit it for `gpt-image-2`. Use `--mode final`, `--mode transparent`, or another explicit mode when you want a deterministic route. Use `--save-request` to write a redacted payload for gateway support.

## Edit

```powershell
python .\scripts\image_generation_api.py edit `
  --prompt "Replace the background with a clean studio scene" `
  --image ".\refs\product.png" `
  --mask ".\refs\mask.png" `
  --choice 1 `
  --preset quality `
  --output ".\output\imagegen\edited.png" `
  --dry-run
```

Grok edits use JSON data URLs and one to three references; masks stay on `gpt-image-2`.

## Responses

```powershell
python .\scripts\image_generation_api.py responses `
  --input-text "Create a campaign poster" `
  --model gpt-5.4 `
  --tool-model gpt-image-2 `
  --preset quality `
  --output ".\output\imagegen\poster.png" `
  --dry-run
```

The top-level `--model` is text-capable; `--choice N` selects the image model inside the `image_generation` tool. If no choice is supplied, that tool uses `gpt-image-2`.

## Validation

```powershell
python <skill-creator-root>\scripts\quick_validate.py .
python -m unittest discover -s .\tests -p "test_*.py"
```

Model-list presence is discovery evidence only. This Skill deliberately refuses image calls for non-GPT/non-Grok models until a tested provider adapter is implemented. Private parameters, pricing, generation, editing, and quality still require provider documentation or an explicitly authorized smoke request.
