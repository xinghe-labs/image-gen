---
name: image-generation-api
description: Use when generating or editing images through a configured third-party API with GPT Image or xAI/Grok Image models, including one-time model catalog configuration, numbered model selection, gpt-image-2 defaults, references, masks, Responses image_generation flows, local saving, or gateway diagnosis. Other discovered image vendors are reported but not executed.
---

# Image Generation API

Use one provider-aware CLI for GPT Image and Grok Image generation and editing. Run `configure` once to snapshot the current `/v1/models` inventory into a stable, numbered local catalog. Only `gpt-image-*` and `grok-imagine-image*` models are executable in the current scope.

`IMAGE_GENERATION_*` is the canonical configuration prefix. Existing `GPT_IMAGE_*` and `OPENAI_*` names remain supported as fallbacks. `gpt-image-2` is always retained in the built-in capability registry and remains explicitly selectable, even when a gateway does not list it.

## Workflow

1. Use `scripts/image_generation_api.py generate` for text-to-image through `POST /v1/images/generations`.
2. Use `edit` for reference-image or mask edits through `POST /v1/images/edits`.
3. Use `responses` for a Responses request containing an `image_generation` tool. The top-level Responses model must be text-capable; the image model belongs in `--tool-model`.
4. Use `models` to inspect the live `/v1/models` inventory without generating an image.
5. Run `configure` after changing the API endpoint. It makes one read-only `GET /v1/models` call and writes the numbered catalog.
6. Use `--choice N` on later `generate`, `edit`, or `responses` calls to select the corresponding catalog entry. With no number, the invariant default is `gpt-image-2`.
7. Use `select-model` only to inspect or resolve the persisted catalog; it never performs live discovery. Use `capabilities` for the offline registry, `options` for controls, and `doctor` for endpoint/model reachability.
8. Run `--dry-run` before a paid or slow call. Dry-run output and saved request records are redacted.

## Model Selection

`configure` is the only generation-skill command that refreshes the model inventory. It performs one `GET /v1/models`, keeps only executable GPT Image and Grok Image entries, orders them deterministically by vendor (`OpenAI` first, then `xAI/Grok`) and by model preference within each vendor, assigns stable numbers, and persists the Base URL binding. Other recognized image models remain discovery-only and never receive an image request.

For `generate`, `edit`, and `responses`:

- `--choice N` resolves a number from the local catalog without network access;
- when a user supplies a number with a request, or replies with only a number while an image request is pending, map it directly to `--choice N` and continue without another vendor/model prompt;
- exact model IDs and supported aliases are accepted for compatibility, but numbers are the portable interface;
- no `--choice` means `gpt-image-2`, regardless of legacy `IMAGE_GENERATION_MODEL`/`GPT_IMAGE_MODEL` preferences;
- explicit `--model`/`--tool-model` and explicit routing modes remain compatibility overrides;
- a catalog from another Base URL is rejected and must be refreshed with `configure`;
- no automatic paid fallback occurs between models or vendors.

Use the number shown in the persisted catalog. The CLI also accepts these short aliases: `gpt2`, `gpt2.5`, `gpt4k`, `grok`, `grok2`, and `grok-quality`; output always records the canonical provider id.

`select-model` exposes the persisted `choices` array and can resolve one number, exact id, or alias. The number remains stable until the next explicit `configure`; it does not make a network request.

The numbered catalog is global for the configured Base URL. Re-run `configure` after changing the endpoint or when the provider inventory changes. `generate --dry-run` stays offline and never queries `/v1/models`.

`gpt-image-2` is a built-in capability, not a promise that every gateway grants access. If it is absent from `/v1/models`, `models` reports a warning and keeps it selectable; an explicit request may still be sent after the user verifies gateway access.

## Configuration

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

Resolution order is CLI flags, current-process `IMAGE_GENERATION_*`, Windows current-user `HKCU\Environment` values, legacy `GPT_IMAGE_*`/`OPENAI_*` values, `.env`, then built-in defaults. The script reads the Windows user environment directly when the parent process has not inherited recently changed values. Legacy model, vendor, provider-profile, and routing-mode preferences remain visible for diagnostics, but only command-scoped `--choice`, `--model`/`--tool-model`, or `--mode` can override the no-choice `gpt-image-2` default.

`base_url` must be an HTTP(S) URL ending in `/v1`. Never paste a dashboard URL or a root domain. The default User-Agent remains `gpt-image-client/1.0`; changing it does not turn a third-party request into Codex native transport.

## Commands

List controls and defaults:

```powershell
python .\scripts\image_generation_api.py options
```

Configure once, then inspect the catalog or live inventory:

```powershell
python .\scripts\image_generation_api.py capabilities
python .\scripts\image_generation_api.py configure
python .\scripts\image_generation_api.py models --image-only
python .\scripts\image_generation_api.py select-model
```

Resolve a persisted number without a second prompt:

```powershell
python .\scripts\image_generation_api.py select-model --choice 2
```

Generate with a dry run first:

```powershell
python .\scripts\image_generation_api.py generate `
  --prompt "A clean product hero image" `
  --preset quality `
  --output ".\output\imagegen\hero.png" `
  --dry-run
```

Generate through a chosen catalog entry:

```powershell
python .\scripts\image_generation_api.py generate `
  --prompt "A cinematic wide composition" `
  --choice 2 `
  --aspect-ratio 16:9 `
  --resolution 2k `
  --output ".\output\imagegen\wide.webp" `
  --dry-run
```

Edit references and masks:

```powershell
python .\scripts\image_generation_api.py edit `
  --prompt "Replace the background with a clean studio scene" `
  --image ".\refs\product.png" `
  --mask ".\refs\mask.png" `
  --model gpt-image-2 `
  --preset quality `
  --output ".\output\imagegen\edited.png" `
  --dry-run
```

Responses image tool:

```powershell
python .\scripts\image_generation_api.py responses `
  --input-text "Create a campaign poster from this product reference" `
  --input-image ".\refs\product.png" `
  --model gpt-5.4 `
  --tool-model gpt-image-2 `
  --preset quality `
  --output ".\output\imagegen\poster.png" `
  --dry-run
```

Use `--save-request` for a redacted provider-support artifact. Use `--prompt-file` for long or multilingual prompts. Use `--extra-json` only for fields documented by the selected gateway.

The `generate`, `edit`, and `responses` commands accept `--choice` from the persisted catalog. For `responses`, the selected image id belongs in the image-generation tool; the top-level `--model` remains the text-capable Responses model.

## Provider Boundary

The only executable adapters are OpenAI-style GPT Image and xAI/Grok. Other recognized vendors, including Alibaba/Qwen, may be classified and displayed by `models`, but they are marked `discovered_not_enabled`, excluded from automatic or interactive selection, and rejected locally before any image `POST`. A future provider adapter requires its own request-schema implementation and tests.

Model discovery honors nested/camelCase vendor and capability metadata. Obvious video-only IDs or `image-to-video` tasks are excluded from still-image choices unless the metadata explicitly advertises image generation or editing. Explicit negative image capability metadata is also respected.

OpenAI-style models support multipart edits, masks, GPT Image 2 size checks, transparent PNG/WebP, and streaming partial images where the provider documents them. Grok models use JSON data URLs for edits, map size to `aspect_ratio`/`resolution`, and do not support masks or streaming in this skill. See [provider routing](./references/provider-routing.md) before adding an adapter.

## Safety and Errors

- Never print a full API key. Reports contain only a masked tail.
- Never make an automatic paid fallback request through another model or vendor.
- `configure`, `models`, `select-model`, and `--dry-run` do not generate images; an unsupported model is rejected even during dry-run.
- `401` and `403` stop retries until credentials or provider state changes.
- `429` and transient `5xx` errors use bounded retries and write structured errors.
- Repeated `524`/`522`/`504` indicates an upstream or Cloudflare timeout, not proof of a bad model name.
- Output normalization never silently crops, stretches, or pads a different aspect ratio.

Read [references/api-surface.md](./references/api-surface.md) for command contracts, [references/provider-routing.md](./references/provider-routing.md) for model/provider rules, and [references/errors.md](./references/errors.md) for structured error categories.

## Validation

```powershell
python <skill-creator-root>\scripts\quick_validate.py .
python -m py_compile .\scripts\image_generation_api.py
python -m unittest discover -s .\tests -p "test_*.py"
```
