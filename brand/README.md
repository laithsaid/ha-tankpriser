# Brand assets

The Tankpriser icon: a fuel pump wearing the Dannebrog.

| File | Size | Purpose |
| --- | --- | --- |
| `icon.png` | 256×256 | The icon Home Assistant and HACS show |
| `icon@2x.png` | 512×512 | High-DPI version |
| `preview-48.png` | 48×48 | Only for checking legibility at sidebar size |
| `make_icon.py` | — | Generates all of the above |

The PNGs are generated, not hand-edited: to change a colour or proportion,
edit `make_icon.py` and re-run it (`python make_icon.py`), then copy the
output over `icon.png` / `icon@2x.png`. It needs Pillow and nothing else.

Everything is drawn at 4× and downsampled, because Pillow's shape drawing has
no antialiasing. The cross deliberately sits left of centre — that is what
makes a Dannebrog read as Danish rather than as a generic cross.

## Getting the icon into Home Assistant

Home Assistant does not read these files from this repository. They have to be
submitted to [home-assistant/brands](https://github.com/home-assistant/brands)
as `custom_integrations/tankpriser/icon.png` (and `icon@2x.png`). Until that
PR is merged, the HACS validation "brands" check fails, which is why
`.github/workflows/validate.yml` currently passes `ignore: brands`. Remove
that line once the brands PR is merged.
