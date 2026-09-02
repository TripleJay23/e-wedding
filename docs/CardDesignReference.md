# Wedding Floral V10 Card Design Reference

## Approved identity

Wedding Floral V10 is an exact verified private template package. It must not be
redrawn, approximated, or implemented through template-specific renderer logic.

```text
template key: wedding_floral
template version: 10
canvas: 1240 x 1748
raw package SHA-256: b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782
logical package SHA-256: 75446293b53acf292b8900e61042239c01ad80b6ed66365e30710834ded6eed4
manifest SHA-256: ccf84b1cb02716c1d0b5c9b830203d1399675a41aacedd02b2b52ac917bb01be
approved preview SHA-256: 1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8
preview QR payload: PREVIEW-QR-DO-NOT-SCAN
logical entries: 9 (manifest, 6 assets, 2 fonts)
```

The verified archive is 598,060 bytes. Its approved synthetic preview is a
metadata-free 1240 x 1748 RGBA PNG. Both stay in ignored private storage.

## Logical-file integrity

| Package entry | SHA-256 |
|---|---|
| `manifest.json` | `ccf84b1cb02716c1d0b5c9b830203d1399675a41aacedd02b2b52ac917bb01be` |
| `assets/bottom_left_floral.png` | `f1f6e07d53bb34147e29e66847ef9ec75b565b4a27f3fed1fe66b128536e319d` |
| `assets/bottom_right_floral.png` | `031fde58dff37b2854df2096367f841c221a38ecce60ff494e5d3e436d520ac7` |
| `assets/photo_floral_frame.png` | `8d13590ccbbe36ae62f0e2108f7f944e0d2e164fb7d32a4f52a39d634d4d139a` |
| `assets/photo_gold_rings.png` | `3e28304cf1f029d3b74a72df6dd4b4f122a24a61cc47b2549e9d7a861c013ea6` |
| `assets/qr_frame.svg` | `1b96180158dfe49f10532331893f80945a240a444f55d04a87f43597205bc568` |
| `assets/top_right_floral.png` | `fd494cce736a1fef0da9d20154497818c6618a8f234db34d374c0285b1f1c28d` |
| `fonts/body_font.otf` | `de1eddf3f49fbc0782a1008a87053207f69174a3860f7068520ee4cd901ef926` |
| `fonts/couple_script.ttf` | `9706c0bb3f05f576e5fe13abcad1bb5e27c76b0b0da055c94e2a2cd88c51bd5d` |

The package validator rejects extra, duplicate, absolute, traversal, undeclared,
wrong-sized, or wrong-digest entries before rendering.

## Rendering contract

The renderer accepts only the validated manifest, declared assets/fonts, closed
placeholders, and supplied context. It supports generic text, asset, photo, QR,
shape, and line layers with deterministic ordering and fitting. No renderer branch
may inspect `template_key` to change composition.

Approved composition includes the sage QR frame, top-right floral decoration,
centered framed couple photo, uppercase invitation heading, green script couple
names, cream invitation panel, highlighted guest name, three-column date block,
venue/address details, and lower floral decoration.

## QR geometry

```text
decorative frame: x=63, y=70, width=232, height=232
QR image:         x=80, y=87, width=198, height=198
spacing:          17 px
foreground:       #4f6f52
background:       #fff8e7
matrix reference: 33 x 33 at 6 px/module
quiet zone:       4 modules
```

The preview QR uses only the non-resolving sentinel. Permanent cards use the
protected image associated with the guest's active real credential, and the
rendered QR must decode independently.

## Photo and name controls

The primary photo target is `x=352`, `y=166`, `width=536`, `height=536` with an
elliptical mask. Generic controls are `COVER` or `CONTAIN`, focus X/Y in `0..1`,
and zoom in `1..3`; `CONTAIN` requires zoom `1.0`.

Wedding Floral V10 applies display-only naming:

- couple: first non-honorific token from each persisted name;
- guest: first and final non-honorific tokens, or the single remaining token.

Whitespace is normalized and leading `Mr`, `Mrs`, `Ms`, `Miss`, `Dr`, and `Prof`
are ignored case-insensitively with an optional period. Original persisted names
are not changed.

The manifest's closed placeholder set is `event.address`, `event.couple_names`,
`event.date`, `event.primary_photo`, `event.time`, `event.venue`,
`guest.full_name`, and `system.qr_image`. Contact data, notes, internal IDs, raw QR
values, and hashes are not template inputs.

## Provenance and publication

Technical identity and staging acceptance do not establish copyright, font
embedding, or redistribution permission. No licensing provenance is embedded in
the nine-entry package. Until the owner separately confirms those rights, the
archive, preview, six assets, two fonts, and generated cards must remain private,
ignored, and excluded from publication. Public examples require separately
licensed or synthetic replacements.
