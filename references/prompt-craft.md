# Prompt Craft

How to turn a casual request into a prompt that produces the wanted image. The image models behind this skill respond to concrete, visual, English description — not to adjectives like "好看" or "高级". Your job in Step 1 of the workflow is to translate intent into visual language.

## The structure formula

Build the polished prompt in this order, keeping it 1-3 sentences (front-load what matters):

```
[medium/style] [subject with concrete detail] [action or pose] [environment] [composition/camera] [lighting] [palette] [quality/finish]
```

- **Subject**: specific beats generic. "a 25-year-old delivery rider checking a cracked phone screen" > "a delivery man".
- **Composition/camera**: `close-up`, `low-angle wide shot`, `centered portrait`, `over-the-shoulder`, `top-down flat lay`, `rule-of-thirds`.
- **Lighting**: `soft window light`, `golden hour backlight`, `neon rim lighting`, `overcast diffuse`, `single hard spotlight`, `volumetric fog`.
- **Palette**: name 2-3 colors or a scheme (`muted earth tones`, `teal-and-orange`, `monochrome ink`).
- **Finish**: `photorealistic, 85mm lens, shallow depth of field` / `clean vector, flat colors` / `cinematic film still, 35mm grain`.

Do not pile every slot full. A 40-word prompt that lands beats a 120-word prompt that drowns the subject.

## Worked example (Chinese input → English prompt)

User says:

> 给我画个雨夜便利店的图，要有那种孤独感

Weak (literal translation): `A convenience store on a rainy night, lonely feeling.`

Polished:

> A lone convenience store glowing on an empty rain-soaked street at 2am, a single customer silhouette at the window, shot from across the wet asphalt with reflections, cinematic night photography, cool cyan and warm tungsten palette, neon signage bleeding into puddles, shallow depth of field, quiet and melancholic mood

What changed: "孤独感" became concrete visuals (empty street, 2am, single silhouette, reflections, melancholic mood); "雨夜" became wet asphalt and puddle reflections; added camera position, palette, and finish. Always show the user the polished English prompt (Step 2) — they can read enough English to veto a wrong subject.

## Style packs

Ready-made keyword blocks to combine with a subject. Pick one, don't stack three.

| Intent | Keyword block |
|---|---|
| Photoreal | `photorealistic, 85mm lens, shallow depth of field, natural skin texture, soft window light` |
| Cinematic still | `cinematic film still, anamorphic, 35mm grain, teal-and-orange grade, volumetric light` |
| Anime / manga | `anime illustration, cel shading, clean lineart, vibrant colors, detailed background art, key visual` |
| Flat illustration | `flat vector illustration, minimal geometric shapes, limited palette, subtle grain texture` |
| 3D render | `3D render, octane, soft studio lighting, subsurface scattering, pastel palette, isometric` |
| Watercolor / ink | `watercolor on textured paper, loose brush strokes, bleeding pigments, white space` |
| Novel cover (portrait) | `dramatic book cover composition, strong focal subject, negative space at top for title text, moody rim light, high contrast` |
| Product shot | `studio product photography, seamless backdrop, softbox lighting, crisp reflections, commercial polish` |

## Size and format selection

| Use | Preset | Note |
|---|---|---|
| Novel/web fiction cover | `portrait-2k` (1152x2048) | covers are tall; leave top negative space if title text will be added |
| Banner / hero | `landscape-2k` | |
| Social square, avatar | `quality` or `square-2k` | |
| Cut-out sticker/asset | `transparent` | GPT Image only; Grok has no transparent background |
| Quick draft to probe an idea | `fast` | probe cheap, finalize expensive |

Exact dimensions requested by the user: pass `--size` (GPT Image, e.g. `1536x1024`) or `--aspect-ratio`/`--resolution` (Grok, e.g. `16:9` `2k`) directly; they override the preset. Grok caveat: some gateways ignore `aspect_ratio` and return the model's native shape — treat Grok dimensions as advisory, and use `--size-policy provider` when the native shape is acceptable.

## Converting vague feedback into prompt deltas

When the user reacts to a generated image with vague words, map them to edits of the polished prompt — do not restart from zero:

| Feedback | Prompt delta |
|---|---|
| "不够高级 / too plain" | upgrade finish: add `editorial photography, refined color grade` or swap style pack up-market |
| "太乱了 / cluttered" | add `minimalist composition, generous negative space, single focal subject` |
| "人不对 / face is off" | be more specific about age, expression, hair, clothing; then use `edit` with the saved image as reference |
| "颜色不对" | replace palette clause with named 2-3 colors; avoid ambiguous "warmer" |
| "构图不对" | swap the composition/camera clause explicitly (`centered portrait` → `low-angle wide shot`) |
| "再来点这种但不完全一样" | keep subject and lighting clauses, vary one: style, palette, or camera angle |

## Cross-image consistency (characters, series)

Same character or style across multiple images is achieved by reusing exact clauses + reference edits, not by hoping:

1. Keep a frozen "identity block" (age, hair, outfit, build, art style) and paste it verbatim into every prompt of the series.
2. For a truer match, generate the canonical image first, then derive further scenes with `edit --image <canonical.png>` instead of pure text-to-image.
3. Record the identity block in the sidecar prompt (it is saved automatically) so the series stays reproducible weeks later.

## Things image models handle badly

- **Text in images**: short single words usually work; sentences garble. Prefer adding title text in post; request `negative space at top for title` instead.
- **Counting**: "three cups" often yields two or four. Regenerate or fix with `edit`.
- **Hands, small text, fine patterns**: expect defects; offer an `edit` pass on the saved file rather than infinite regeneration.
- **Real people / trademarked characters**: the gateway may refuse; suggest a described lookalike instead.
