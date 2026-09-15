# Provider Routing

Read this reference when changing model classification, vendor grouping, or request adapters.

## Invariants

- `gpt-image-2` remains in the built-in registry and is explicitly selectable.
- Only `gpt-image-*` and `grok-imagine-image*` are enabled for execution.
- Other image candidates are discovery-only, never enter model selection, and must be rejected before an image `POST`.
- `configure` is the only command that refreshes `/v1/models`; it persists a deterministic numbered catalog bound to the configured Base URL.
- Without a numbered choice, the invariant image default is `gpt-image-2`. Legacy global model variables are diagnostics/compatibility inputs and cannot change that default.
- Only command-scoped `--choice`, `--model`, `--tool-model`, and explicit routing modes can change the current request's model. Do not synthesize a choice from a global preference.
- No automatic paid fallback occurs between models or vendors.
- `/v1/models` proves inventory discovery only; it does not prove private protocol support, cost, generation, editing, or quality.

## Vendor classification

The classifier first honors nested vendor metadata such as `vendor`, `provider`, `owned_by`, `publisher`, and `organization` (including camelCase spellings), then falls back to model-ID inference. It recognizes common OpenAI, xAI/Grok, Google/Imagen, Black Forest Labs/Flux, Stability, Alibaba/Qwen/Wan, ByteDance/Seedream, Tencent/Hunyuan, Baidu, Amazon/Nova, Adobe/Firefly, Midjourney, Ideogram, Recraft, Runway, Kling, Luma, Pika, PixArt, CogView, Spark, and other common image model families. These broader records support inventory diagnosis only; executable grouping is limited to OpenAI GPT Image and xAI/Grok.

Image candidacy can come from model-ID keywords or nested metadata such as `capabilities.image=true`, `tasks=["text-to-image"]`, `output_modalities=["image"]`, `image_generation`, `image_editing`, `inpaint`, and `img2img`.

## Selection states

`group_image_model_records()` first removes discovery-only records, then filters enabled models by operation (`generate`, `edit`, or `any`) and orders models inside each vendor. `flatten_model_choices()` turns those groups into one deterministic numbered list with the entire OpenAI block first and the entire xAI/Grok block second. Proven models are marked `recommended`; retired or unverified aliases remain visible in `more_choices`, but recommendation tiers never split a vendor block and are never silently selected.

`select_model_from_groups()` remains a lower-level compatibility helper for live diagnostics and implements:

1. requested vendor plus one model: automatic;
2. requested vendor plus multiple models: one model choice required;
3. one vendor plus one model: automatic;
4. one vendor plus multiple models: one model choice required;
5. multiple vendors: one complete vendor/model choice required; there is no vendor-then-model prompt.

`--choice` and `--interactive` accept a number, exact model id, or friendly alias (`gpt2`, `gpt2.5`, `gpt4k`, `grok`, `grok2`, `grok-quality`). The alias is resolved before request construction and the canonical id is retained in output for auditability.

The Skill must run `configure` once after API setup (and again after changing Base URL or refreshing inventory). Later generation commands resolve `--choice` from the local catalog and never call `/v1/models`; no number uses `gpt-image-2` directly. `select-model` is an optional local catalog viewer/resolver, not a live discovery gate.

## Request profiles

### OpenAI-style

Used for `gpt-image-2` and known GPT Image aliases. Generation uses `size`, `quality`, output format, optional transparent background, and optional streaming partial images. Edits use multipart image/mask uploads. Responses uses a text-capable top-level model and an `image_generation` tool.

`gpt-image-2` validation keeps dimensions as multiples of 16, a maximum 3840px edge, a maximum 3:1 ratio, and the documented pixel range. Transparent output requires PNG or WebP.

### Grok

Used for `grok-imagine-image*`. Generation maps size to `aspect_ratio` and `resolution`; edits use JSON data URLs for up to three source images. The profile rejects masks, streaming partial images, transparent backgrounds, compression, and multi-image `n` requests.

### Generic compatibility value

`generic` remains accepted as a legacy parser/configuration value so old invocations fail clearly, but it is not an executable adapter. Unknown, Qwen, and other non-GPT/non-Grok models are rejected before generation or editing.

## Adding an adapter

Confirm the exact model ID and official request schema. Add a dedicated executable adapter, capability entry, vendor rule, local rejection/acceptance tests, request-shape tests, and invalid-combination tests. Run discovery plus dry-run checks, and make any live smoke request only with explicit authorization.
