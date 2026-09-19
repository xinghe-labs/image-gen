# image-generation-api

A prompt-to-image **agent skill**: you describe the picture you want, the agent polishes your idea into a structured prompt, confirms the plan once, and runs a provider-aware CLI to deliver the file. Under the hood it calls **GPT Image** and **xAI/Grok Image** models through any OpenAI-compatible or third-party gateway. Configure the API once, persist a deterministic numbered catalog, then select a model with one number. Only `gpt-image-*` and `grok-imagine-image*` enter the executable catalog or image requests.

Works with any agent that reads the [Agent Skills](https://agentskills.io) format (Codex, Claude Code, ZCode, Cursor, ...).

## How an agent uses it

`SKILL.md` defines a four-step workflow the agent follows for every image request:

1. **Polish** — the agent turns a casual request into a structured English prompt (subject, composition, style, lighting, quality) using [`references/prompt-craft.md`](references/prompt-craft.md), and picks model + preset.
2. **Confirm** — one compact block: final prompt, model, parameters, output path. Skipped when you say "just generate".
3. **Generate** — one CLI call. The image lands under a dated output path.
4. **Deliver & iterate** — the agent reports the file and offers concrete next moves (variants, restyle, `edit` pass). Every successful generation writes a **sidecar record** next to the image, so any past image can be reproduced later.

```
You: 画一张雨夜便利店的图，要有那种孤独感
Agent: polished prompt + plan (gpt-image-2 · quality · 1024x1024) → confirm?
You: 可以
Agent: saved output/imagegen/2026-09-19-convenience-store.png (+ .json sidecar)
       want: 3 variants · warmer palette · low-angle composition?
```

## Installation

### Skills CLI (recommended)

Detects the agents installed on the machine and installs the skill where each one looks for it:

```bash
npx skills add xinghe-labs/image-generation-api                 # interactive: pick agents
npx skills add xinghe-labs/image-generation-api -g --copy -y    # non-interactive: user-level, copy
```

### No-Node fallback

From a clone or an extracted release archive, with Python 3.10+ (standard library only):

```bash
python install.py              # first detected skill root (~/.agents/skills, ~/.codex/skills, ~/.claude/skills)
python install.py --root "<other-skill-root>"   # explicit target
python install.py --all        # every detected skill root
```

The installer refuses to replace a foreign directory unless `--force` is passed. To update an install, `git pull` in the clone and re-run the command.

### Run from a clone

```bash
git clone https://github.com/xinghe-labs/image-generation-api.git
cd image-generation-api
python scripts/image_generation_api.py --help
```

## Configuration

```dotenv
IMAGE_GENERATION_API_KEY=your-third-party-key
IMAGE_GENERATION_BASE_URL=https://your-provider.example/v1
IMAGE_GENERATION_MODEL_CATALOG=~/.codex/image-generation-api-model-catalog.json
IMAGE_GENERATION_RESPONSES_MODEL=gpt-5.4
IMAGE_GENERATION_TOOL_MODEL=gpt-image-2
IMAGE_GENERATION_TIMEOUT=180
IMAGE_GENERATION_RETRIES=2
IMAGE_GENERATION_RETRY_DELAY=1
IMAGE_GENERATION_USER_AGENT=gpt-image-client/1.0
IMAGE_GENERATION_PROVIDER_PROFILE=auto
IMAGE_GENERATION_ROUTING_MODE=auto
```

`base_url` must be an HTTP(S) URL ending in `/v1` — never a dashboard URL or root domain. `IMAGE_GENERATION_*` is canonical; `GPT_IMAGE_*` and `OPENAI_*` remain compatible aliases. On Windows, user-level values under `HKCU\Environment` are read directly, so a new global value is available to already-running shells. The catalog contains model metadata and numbers only; it never stores the API key.

## Model catalog and --choice

```bash
python scripts/image_generation_api.py configure     # one read-only GET /v1/models → numbered catalog
python scripts/image_generation_api.py select-model  # print the persisted catalog (offline)
python scripts/image_generation_api.py models --image-only  # live inventory, no generation
```

- `configure` is the only command that refreshes the inventory; numbers stay stable until the next `configure`, and the catalog is bound to its Base URL.
- `--choice N` resolves the same number in `generate`, `edit`, and `responses` without a network request.
- Without `--choice`, the image model is always `gpt-image-2`, even when the gateway does not list it (legacy model environment variables do not override it).
- Explicit `--model`/`--tool-model` remain compatibility overrides. Convenience aliases: `gpt2`, `gpt2.5`, `gpt4k`, `grok`, `grok2`, `grok-quality` — output always records the canonical provider id.
- Alibaba/Qwen and other recognized vendors are reported by `models` as discovery-only and are never executable.
- No automatic fallback or second model-selection prompt is ever performed.

## Commands

```bash
# Text-to-image (the default flow; --choice N selects a catalog model)
python scripts/image_generation_api.py generate \
  --prompt "A lone convenience store glowing on a rain-soaked street" \
  --preset quality \
  --output output/imagegen/2026-09-19-store.png

# Reference-image edit (Grok takes 1-3 references; masks are GPT Image only)
python scripts/image_generation_api.py edit \
  --prompt "Replace the background with a clean studio scene" \
  --image refs/product.png --mask refs/mask.png \
  --choice 1 --output output/imagegen/edited.png

# Responses flow: a text model drives the image_generation tool
python scripts/image_generation_api.py responses \
  --input-text "Create a campaign poster from this product" \
  --input-image refs/product.png \
  --model gpt-5.4 --tool-model gpt-image-2 \
  --output output/imagegen/poster.png
```

Add `--dry-run` to inspect the exact request without sending it, and `--prompt-file` for long or multilingual prompts. Presets cover common shapes: `fast`, `standard`, `quality`, `square-2k`, `landscape-2k`, `portrait-2k`, `landscape-4k`, `portrait-4k`, `transparent`.

## Sidecar records

Every successful `generate`, `edit`, or `responses` call writes `<output>.json` next to the image — the exact prompt, model, catalog choice, parameters, byte size, and SHA-256 of the file. Months later you can answer "which prompt made this picture?" from the file alone, and rerun it verbatim. Pass `--no-sidecar` to opt out; records never contain credentials.

## Provider boundary and safety

- Never prints or stores the API key; reports show a masked tail only.
- No automatic paid fallback across models or vendors; failures are surfaced, not rerouted.
- Only `gpt-image-*` and `grok-imagine-image*` models execute; everything else is discovery-only, rejected locally before any image POST.
- `configure`, `models`, `select-model`, and `--dry-run` never generate images.
- `401`/`403` stop retries until credentials change; repeated `524`/`522`/`504` indicates an upstream timeout, not a bad model name.
- Output normalization never silently crops, stretches, or pads a different aspect ratio.

See [`references/api-surface.md`](references/api-surface.md) for command contracts, [`references/provider-routing.md`](references/provider-routing.md) for model/provider rules, and [`references/errors.md`](references/errors.md) for structured error categories.

## Validation

```bash
python -m py_compile scripts/image_generation_api.py
python -m unittest discover -s tests -p "test_*.py"
python <skill-creator-root>/scripts/quick_validate.py .
```

The test suite is fully offline and uses a local fake HTTP server; it never contacts paid image endpoints.

## License

[MIT](LICENSE)
