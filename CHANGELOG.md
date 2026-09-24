# Changelog

## Unreleased

- Mask edge smoothing now only adds editable pixels; it never removes a painted area.
- Automatic gap filling is enabled by default in the mask editor. The added area is visible in the preview and coverage readout.
- Imported soft alpha masks treat pixels with alpha below 128 as editable. Black and white masks use white as the editable area; same-ratio masks can be scaled on import.
- Masked edits paste the model result back onto the primary image by default. The final result reports whether paste-back was applied or why it was skipped; the switch can be turned off for each edit.
- The preview of a masked edit can switch between the final result and the primary image used for that request.
- The paste-back drift guard now compares red, green, and blue separately, so a color shift that leaves luminance unchanged is also caught.
