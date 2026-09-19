# API Surface

The public entrypoint is `scripts/image_gen.py`.

## Read-only commands

`options` prints presets, sizes, quality/format values, provider profiles, routing modes, and defaults.

`capabilities` prints the built-in registry. It always includes `gpt-image-2` and does not contact a provider.

`configure` is the one-time inventory command. It calls `GET /v1/models` exactly once, accepts common `data`, `models`, and `items` list wrappers, filters to executable GPT/Grok image models, assigns deterministic numbers, and atomically writes `image-generation-api-model-catalog.json` (or `--catalog`). The file contains no API key and records the Base URL it belongs to. `gpt-image-2` is inserted as a built-in default when the gateway omits it, with `listed=false`.

`models` remains a live read-only diagnostic call. It reports all recognized image candidates and distinguishes executable GPT/Grok records from `discovered_not_enabled` records. If the list contains another image model but omits `gpt-image-2`, the result is successful with a warning.

`select-model` reads the persisted catalog and never calls the provider. It returns one flat `choices` array plus compact `recommended_choices` and `more_choices` number indexes; each full choice has a stable number, vendor, canonical model id, status, capabilities, and aliases. `--interactive` reads one number, exact id, or alias from that flat list. `--choice` performs the same resolution without stdin. A missing or Base-URL-mismatched catalog returns a structured error instructing the caller to run `configure`.

`doctor` probes `GET /v1/models/{model}` and falls back to `GET /v1/models` when the detail route is unsupported.

## Image endpoints

`generate` sends `POST /v1/images/generations` only for `gpt-image-*` or `grok-imagine-image*`. OpenAI-style payloads include `model`, `prompt`, `size`, `quality`, `n`, and output controls. Grok payloads use `aspect_ratio`, `resolution`, and `response_format`; OpenAI-only fields are omitted. Unknown and other-vendor models are rejected locally.

`edit` sends `POST /v1/images/edits`. GPT Image uses multipart fields and repeated `--image` files, with an optional `--mask`. Grok uses JSON data URLs for one to three images and rejects masks. No generic edit adapter is executable.

`responses` sends `POST /v1/responses` with an `image_generation` tool. The top-level model is text-capable. `--choice N` changes only the image model in the tool; with no choice, the tool defaults to `gpt-image-2`. No hidden model discovery occurs.

## Selection contract

The catalog selection gate is local and deterministic. `--choice N` resolves the persisted number; no number means `gpt-image-2` unless the caller explicitly supplies command-scoped `--model`/`--tool-model` or `--mode`. Legacy environment and `.env` model/routing preferences are diagnostic only. A catalog choice is valid only for the Base URL recorded at configure time. There is no hidden `GET /v1/models`, vendor prompt, or automatic fallback in an image request.

## Output contract

Successful calls return JSON with `ok`, selected `model`, `model_source`, provider profile, saved paths, and `artifacts`. Base64 results are decoded locally. `--size-policy normalize` permits same-aspect resampling, `strict` rejects every mismatch, and `provider` preserves provider dimensions. A different aspect ratio is never silently cropped or stretched.
