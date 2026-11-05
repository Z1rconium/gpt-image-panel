# Official documentation evidence (checked 2026-11-05)

- https://developers.openai.com/api/docs/models/gpt-image-2.6-flare
- https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst
- https://developers.openai.com/api/docs/guides/image-generation
- https://developers.openai.com/api/docs/guides/image-prompting
- https://developers.openai.com/api/docs/pricing

Official guide explicitly covers GPT Image 2.6. API reference pages still have some older 2.5-only enumerations; the new generation guide/prompting page establish new fields. Do not infer unsupported snapshots or add reasoning.summary/input_fidelity. Complexity low/medium/high, omission defaults high. Six quality levels auto/low/medium/high/xhigh/max.

Images generations output_format svg requires n=1. svg/gif are not edit outputs, do not support stream/partial_images/output_compression/prompt_rewriter. GIF frame_count 2..64 default9; frame_duration_ms20..2000 default200, rounded to10ms. Size positive multiples16 with model constraints; total frame pixel budget is not numerically established in the guide, so do not invent it.

Markup: separate transparent PNG, same post-orientation dimensions as first image; applies to first input, max16777216 pixels per first image/markup, max50MB markup. Cannot combine mask or prompt_rewriter. Multipart file field markup. JSON Images edits uses images array and markup object exactly one image_url/file_id, data URLs allowed.

2.6 token pricing USD/M: text input5, image input8, image output30. SVG also bills authoring-model tokens; usage may be insufficient for complete cost. Preserve this limitation.

Renderer references: https://cairosvg.org/documentation/ and https://github.com/Kozea/CairoSVG/releases/tag/2.9.1 . Use >=2.9.1 (security fix), external loading denied, unsafe=False. Cairo native dependency needed in runtime and local docs.
