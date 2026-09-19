---
name: image-generation-api
description: Use when the user wants to create, edit, or iterate on images from a text prompt - covers, illustrations, posters, character art, photoreal scenes, any "画一张/生成图" request. Polishes the user's idea into a structured image prompt, confirms once, then generates through a configured OpenAI-compatible gateway using GPT Image or Grok models via a provider-aware CLI with a numbered model catalog. Also covers reference-image edits, masks, Responses image_generation flows, and reproducing past generations from sidecar records.
---

# Image Generation API

A prompt-to-image skill for agents. The Python CLI in `scripts/` is the engine; you are the photographer's art director: polish the idea, lock the plan, run one command, deliver the file. Every successful generation writes a `.json` sidecar next to the image recording the exact prompt and parameters, so any past image can be understood and reproduced.

Prerequisites: `IMAGE_GENERATION_API_KEY` and `IMAGE_GENERATION_BASE_URL` (an HTTP(S) URL ending in `/v1`) configured in the environment or `.env`. If a call fails with missing-credential errors, show the user the exact variable names to set — never ask them to paste a key into chat. Run `configure` once per gateway to snapshot the model catalog; the catalog path defaults to `%USERPROFILE%\.codex\image-generation-api-model-catalog.json`.

## The core workflow

### Step 1 — Polish the prompt

The user usually speaks in one casual sentence. A raw sentence wastes the model. Read [references/prompt-craft.md](./references/prompt-craft.md) and turn the request into a structured English prompt (subject, composition, style, lighting, quality). While polishing, also decide:

- **Model**: default `gpt-image-2` when no preference exists. `--choice N` selects from the persisted catalog (`select-model` prints it, offline). Grok models are a valid pick when the user asks for Grok explicitly or wants its look.
- **Preset**: pick from the table below based on use (cover → portrait-2k, banner → landscape-2k, quick draft → fast). Size flags override presets when the user names exact dimensions.

### Step 2 — Confirm once

Present one compact block before the paid call:

> **Prompt**: `<final English prompt>`
> **Plan**: gpt-image-2 · quality · 1024x1024 png · → `output/imagegen/2026-09-19-hero.png`

Skip the confirmation only when the user said "直接出图 / just generate / don't ask". If their request already pins style, ratio, and size in detail, confirm with a single short line instead of a full block. Generate on their confirmation — do not renegotiate details they already decided.

### Step 3 — Generate

```bash
python scripts/image_generation_api.py generate \
  --prompt "<final English prompt>" \
  --preset quality \
  --output "output/imagegen/2026-09-19-hero.png"
```

Rules for the paid call:

- Use `--choice N` / `--model` / `--preset` exactly as planned in Step 2; never silently swap model or vendor.
- `--dry-run` is for debugging payload shape with the user, not a routine step.
- One output file per call under a dated, slug-named path; the sidecar `<output>.json` is written automatically (`--no-sidecar` to opt out). Never overwrite a previous image — change the slug.
- Use `--prompt-file` for prompts over ~1 line to avoid shell-quoting bugs, especially on Windows.
- Long prompts or multilingual text: still send English (polished) unless the user insists on Chinese keywords for typography in the image.

### Step 4 — Deliver and iterate

Report the saved path (and the sidecar path). Then offer direction, not silence: propose 2-3 concrete next moves — e.g. "换构图", "更亮的打光", "同 prompt 出 3 张变体（`--n 3`）", or an `edit` pass on the saved file. When the user reacts with vague feedback ("不太对", "更高级一点"), read [references/prompt-craft.md](./references/prompt-craft.md) for how to convert that into prompt deltas, and iterate on the polished prompt rather than starting over.

## Quick reference

Presets (flags override preset fields):

| Preset | Size | Quality | Format | Typical use |
|---|---|---|---|---|
| `fast` | 1024x1024 | low | png | drafts, idea probing |
| `standard` | 1024x1024 | medium | png | everyday images |
| `quality` | 1024x1024 | high | png | default for finals |
| `final` / `square-2k` | 2048x2048 | high | png | square finals |
| `landscape-2k` / `portrait-2k` | 2048x1152 / 1152x2048 | high | webp | banners / covers |
| `landscape-4k` / `portrait-4k` | 3840x2160 / 2160x3840 | high | webp | print-scale |
| `transparent` | 1024x1024 | high | png | cut-out assets (GPT Image) |

Model aliases: `gpt2`/`gpt2.5`/`gpt4k`, `grok`/`grok2`/`grok-quality`. Catalog numbers (`--choice N`) stay stable until the next `configure`; with no `--choice` and no `--model`, the default is `gpt-image-2`.

Other commands: `configure` (refresh catalog after changing gateway), `models --image-only` (live inventory), `select-model` (print/resolve catalog), `capabilities`, `options`, `doctor`. Run `python scripts/image_generation_api.py <command> --help` for the full flag list of any command.

## Edits and Responses flows

- **Reference-image edit**: `edit --prompt "..." --image ref.png [--mask mask.png] --output ...` — use when the user supplies an image to restyle, blend, or fix. Grok takes 1-3 references via JSON data URLs and supports no masks; masks are GPT Image only.
- **Text+image Responses flow**: `responses --input-text "..." --input-image ref.png --model <text model> --tool-model <image model>` — for conversational generation where a text model drives the image tool.

Read [references/api-surface.md](./references/api-surface.md) for exact request contracts before first use of `edit` or `responses`.

## Boundaries

- Never print or store the API key; reports show a masked tail only.
- Never make an automatic paid fallback to another model or vendor. A failed call is surfaced, not rerouted.
- Only `gpt-image-*` and `grok-imagine-image*` models execute. Other vendors (e.g. Alibaba/Qwen) are discovery-only: classify, display, reject locally before any image POST.
- `configure`, `models`, `select-model`, and `--dry-run` never generate images or cost money.
- `401`/`403` stop retries until the user fixes credentials. Repeated `524`/`522`/`504` means upstream/Cloudflare timeout — tell the user, don't blame the model name.
- Output normalization never silently crops, stretches, or pads a different aspect ratio.

## Read-when-needed

| Situation | Read |
|---|---|
| Polishing prompts, style packs, sizes, consistency | [references/prompt-craft.md](./references/prompt-craft.md) |
| First `edit`/`responses` use, exact request contracts | [references/api-surface.md](./references/api-surface.md) |
| Vendor-specific routing, Grok vs GPT parameter mapping | [references/provider-routing.md](./references/provider-routing.md) |
| Interpreting structured errors, retry behavior | [references/errors.md](./references/errors.md) |
