# Repository Guidelines

## Project Structure & Module Organization

This repository contains an agent skill (Agent Skills format, usable by Codex, Claude Code, ZCode, and similar agents) for prompt-to-image generation through third-party APIs. `SKILL.md` is the skill entry point: it defines the polish → confirm → generate → deliver workflow and must stay lean, pushing detail into `references/`. `references/prompt-craft.md` is the prompt-polishing handbook; `references/api-surface.md`, `references/provider-routing.md`, and `references/errors.md` carry provider behavior and command contracts — keep conditional details there instead of expanding `SKILL.md`. `scripts/image_generation_api.py` implements the CLI; every successful paid call writes a sidecar JSON record next to the output image (prompt, model, parameters, SHA-256), disabled with `--no-sidecar`. `install.py` is the no-Node installer; `agents/openai.yaml` defines the Codex UI metadata and implicit invocation policy. Tests are in `tests/test_image_generation_api.py` and use a local fake HTTP server, so they must not contact paid image endpoints.

## Build, Test, and Development Commands

Run these commands from the repository root on Windows PowerShell:

```powershell
python -m py_compile .\scripts\image_generation_api.py
python -m unittest discover -s .\tests -p "test_*.py"
python <skill-creator-root>\scripts\quick_validate.py .
python .\scripts\image_generation_api.py generate --prompt "smoke test" --dry-run
```

The first command checks syntax, the second runs the full offline suite, and `quick_validate.py` checks skill metadata and structure. Use `--dry-run` to inspect request construction without generating or charging for an image.

## Coding Style & Naming Conventions

Use Python 3 type hints, four-space indentation, `snake_case` for functions and variables, and `UPPER_SNAKE_CASE` for constants. Prefer small provider-specific helpers over branching throughout command handlers. Keep user-facing errors actionable and never include credentials. Markdown uses short headings, fenced PowerShell examples, and canonical model IDs such as `gpt-image-2`.

## Testing Guidelines

Use `unittest`; name tests `test_<behavior>`. Cover request shape, invalid combinations, catalog ordering, redaction, Base URL binding, offline model selection, and sidecar record contents (including `--no-sidecar` and credential absence). Network-facing tests must use `FakeImageServer`. A model listed by `/v1/models` is discovery evidence only, not proof of generation support.

## Commit & Pull Request Guidelines

This repository starts without historical conventions. Use imperative Conventional Commit subjects, for example `fix: keep OpenAI choices before xAI`. Pull requests should describe behavioral changes, list verification commands, note provider/API compatibility, and include redacted dry-run output when request payloads change. Never attach API keys, `.env` files, generated images, or raw provider responses containing private data.

## Security & Configuration

Store credentials in environment variables, never in the catalog or repository. Preserve `gpt-image-2` as the no-choice default, do not add automatic paid fallback, and require explicit authorization before live generation tests.
