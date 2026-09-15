# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Codex skill for provider-aware image generation through third-party APIs. `SKILL.md` is the skill entry point and must keep the invocation rules concise. `scripts/image_generation_api.py` implements the CLI. Provider behavior and command contracts live in `references/`; keep conditional details there instead of expanding `SKILL.md`. `agents/openai.yaml` defines the Codex UI metadata and implicit invocation policy. Tests are in `tests/test_image_generation_api.py` and use a local fake HTTP server, so they must not contact paid image endpoints.

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

Use `unittest`; name tests `test_<behavior>`. Cover request shape, invalid combinations, catalog ordering, redaction, Base URL binding, and offline model selection. Network-facing tests must use `FakeImageServer`. A model listed by `/v1/models` is discovery evidence only, not proof of generation support.

## Commit & Pull Request Guidelines

This repository starts without historical conventions. Use imperative Conventional Commit subjects, for example `fix: keep OpenAI choices before xAI`. Pull requests should describe behavioral changes, list verification commands, note provider/API compatibility, and include redacted dry-run output when request payloads change. Never attach API keys, `.env` files, generated images, or raw provider responses containing private data.

## Security & Configuration

Store credentials in environment variables, never in the catalog or repository. Preserve `gpt-image-2` as the no-choice default, do not add automatic paid fallback, and require explicit authorization before live generation tests.
