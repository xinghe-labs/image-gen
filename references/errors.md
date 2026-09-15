# Error Reference

## Model selection required

`selection_required` with exit code `2` means discovery found more than one valid choice. The result includes `vendor_count`, compact vendor groups, and model IDs. Choose with `--model`, `--vendor` plus interactive model input, or `select-model --interactive`. No image `POST` is sent while this state is unresolved.

## Authentication and permission

`401` or `invalid_api_key` means the key was rejected or the process has an old value. Stop retries and inspect only the masked key tail.

`403` is non-retryable until base URL, model permission, image-generation access, WAF/IP/region policy, or account state changes.

## Timeouts and transient errors

`429` and most `5xx` responses use bounded exponential retries. `522`, `524`, and `504` are gateway/upstream timeout categories. A repeated timeout is evidence of a slow or unavailable route, not proof that the local model string or key is wrong. Do not silently switch providers.

## Discovery warnings

`gpt_image_2_not_listed_on_current_gateway` means the current `/v1/models` response omitted `gpt-image-2`. The skill still supports and exposes it for explicit selection. Verify gateway access before a paid call.

`model_probe_not_supported` from `doctor` means neither model probe route gave a usable inventory. It is a warning; use `generate --dry-run` to inspect the request shape.

`no_image_models_discovered` means no model ID or capability metadata looked image-capable. Check the gateway's model-list schema and provider documentation.

## Provider validation

Use `gpt-image-2` for masks, transparent output, high-fidelity reference edits, and streaming partial images where supported. Grok rejects masks, streaming, transparent backgrounds, moderation, compression, and `n > 1` in this adapter. Unknown models accept only the generic documented shape.

## Output processing

`output_dimension_mismatch` is raised under `strict` when pixels differ. `output_aspect_ratio_mismatch` is raised under `normalize` when the provider changes composition. Use `provider` only when accepting raw provider dimensions is intentional. The CLI never silently crops, pads, stretches, or distorts a different aspect ratio.
