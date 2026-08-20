# Exact Wedding Floral Card Design Reference

## Non-negotiable rule

Do not recreate the approved card by eye.

The clean rebuild must recover and use the exact validated Wedding Floral Version 10 package from the frozen project.

## Identity anchors

```text
template_key: wedding_floral
template_version: 10
canvas: 1240 × 1748
raw package SHA-256: b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782
approved default preview SHA-256: 1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8
preview QR payload: PREVIEW-QR-DO-NOT-SCAN
logical package entries: 9
assets: 6
fonts: 2
```

The implementation agent with frozen-folder access must locate the package by hash and verify every logical file before reuse.

## Visual composition

Approved composition:

- sage rounded QR treatment at top-left;
- floral decoration at top-right;
- centered oval/circular couple photo with gold-ring treatment and floral lower frame;
- uppercase invitation heading;
- large green script couple names;
- rounded cream invitation panel;
- guest name highlighted in green within mixed-style invitation text;
- structured three-column date presentation;
- venue/address/reception information;
- floral decoration at the bottom corners/region.

## QR geometry from approved reference

```text
QR decorative frame: x=63, y=70, width=232, height=232
QR image:            x=80, y=87, width=198, height=198
spacing:             17 px
foreground:          #4f6f52
background:          #fff8e7
matrix reference:    33 × 33 at 6 px/module
quiet zone:          4 modules (24 px)
```

Styled QR must remain independently decodable.

## Photo geometry and controls

```text
PHOTO target: primary_photo
x: 352
y: 166
width: 536
height: 536
```

Generic controls:

- `photo_fit`: COVER or CONTAIN; default COVER
- `photo_focus_x`: 0.0–1.0; default 0.5
- `photo_focus_y`: 0.0–1.0; default 0.5
- `photo_zoom`: 1.0–3.0; default 1.0

Rules:

- COVER fills/crops around focus and zoom;
- CONTAIN preserves the whole image;
- CONTAIN with zoom above 1.0 remains invalid unless the recovered final contract proves otherwise;
- no face detection/AI cropping is required;
- controls are generic manifest controls, not Wedding Floral conditionals.

## Placeholder/name contract

Approved controlled placeholders include event, guest, and system values such as:

```text
event.couple_names
event.date
event.time
event.venue
event.address
event.primary_photo
guest.full_name
guest.allowed_companions
guest.table_number
guest.category
system.qr_image
system.card_identifier
```

Do not expose guest email, phone, notes, internal IDs, raw QR token, or QR hash as template placeholders.

Wedding Floral V10 applies a deterministic display-only naming policy in the
card presentation layer:

- bride/groom: first non-honorific name token only;
- guest: first and final non-honorific name tokens, or the single token when
  only one remains.

The policy normalizes whitespace and ignores the common leading honorifics
`Mr`, `Mrs`, `Ms`, `Miss`, `Dr`, and `Prof` (case-insensitive, with or without a
trailing period). It preserves token spelling, casing, hyphens, and apostrophes.
Persisted event and guest full names remain unchanged; no display-name columns
are added.

## Asset recovery hints

The legacy private asset inventory previously included names similar to:

```text
right_top_line.svg
top_right_floral.svg
bottom_left_floral.svg
bottom_right_floral.svg
default_profile_placeholder.svg
left_bottom_line.svg
left_top_line.svg
profile_ring_floral.svg
qr_code_border.svg
qr_code_template.svg
right_bottom_line.svg
```

These names are search hints only. The recovered Version 10 manifest/package is authoritative.

## Acceptance

The rebuild has not preserved the design until:

1. exact package hash is verified;
2. logical file inventory matches;
3. package validates under the new generic manifest validator;
4. default deterministic preview matches the approved digest where environment/font rendering is intentionally identical;
5. QR decodes correctly;
6. representative portrait/landscape/square photo framing works;
7. no template-specific renderer branch exists.
