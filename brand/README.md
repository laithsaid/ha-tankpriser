# Brand assets

The Tankpriser icon: Material Design's `mdi:gas-station` — the same fuel pump
Home Assistant itself draws — with the Dannebrog on the pump body.

| File | Size | Purpose |
| --- | --- | --- |
| `icon.png` | 256×256 | The icon Home Assistant and HACS show |
| `icon@2x.png` | 512×512 | High-DPI version |
| `preview-48.png` | 48×48 | Only for checking legibility at sidebar size |
| `tankpriser-icon.svg` | vector | The master; edit this, not the PNGs |
| `make_icon.js` | — | Renders the PNGs from the path data |

## Regenerating

```
npm install sharp
node make_icon.js
```

then copy `fin_256.png` / `fin_512.png` over `icon.png` / `icon@2x.png`.

## Design notes

The pump outline is MDI's `gas-station` path, used **unmodified** — including
the filler-neck handle on the right — so the icon sits naturally beside Home
Assistant's own icons. Three deliberate additions:

* The display, which MDI leaves as a hole, is filled white. Left as a hole it
  shows the page through it, so it looked dark on a dark theme and white on a
  light one.
* The cross bars are thicker than a true Dannebrog's ~1/7 ratio. At 48 px the
  correct thin cross greys out into the red.
* The glyph is trimmed and re-centred, because MDI's 24×24 grid leaves uneven
  margins around this particular icon.

The cross sits left of centre — that is what makes a Dannebrog read as Danish
rather than as a generic (Swiss/Red Cross) centred cross.

## Getting the icon into Home Assistant

Home Assistant does not read these files from this repository. They have to be
submitted to [home-assistant/brands](https://github.com/home-assistant/brands)
as `custom_integrations/tankpriser/icon.png` (and `icon@2x.png`). Until that
PR is merged the HACS validation "brands" check fails, which is why
`.github/workflows/validate.yml` currently passes `ignore: brands`. Remove
that line once the brands PR is merged.
