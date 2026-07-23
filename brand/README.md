# Brand assets (source)

The Tankpriser icon: Material Design's `mdi:gas-station` — the same fuel pump
Home Assistant itself draws — with the Dannebrog on the pump body.

**The images Home Assistant actually uses live in
`custom_components/tankpriser/brand/`.** This folder holds only the sources
that generate them.

| File | Purpose |
| --- | --- |
| `make_icon.js` | Renders every PNG into `custom_components/tankpriser/brand/` |
| `tankpriser-light.svg` | Vector master, light theme (dark pump) |
| `tankpriser-dark.svg` | Vector master, dark theme (light pump) |

## Regenerating

```
npm install sharp
node brand/make_icon.js
```

This writes `icon.png`, `icon@2x.png`, `dark_icon.png` and `dark_icon@2x.png`
into the integration's `brand/` folder. Edit the path data or colours in
`make_icon.js` — the SVGs are outputs, not inputs.

## How the icon reaches Home Assistant

Since **Home Assistant 2026.3**, custom integrations ship their own brand
images in `custom_components/<domain>/brand/`, and local images take priority
over the brands CDN. No PR to
[home-assistant/brands](https://github.com/home-assistant/brands) is required.

On Home Assistant **older than 2026.3** this mechanism does not exist, and the
integration will show a generic placeholder unless the icons are submitted to
the brands repository as `custom_integrations/tankpriser/`.

## Design notes

The pump outline is MDI's `gas-station` path, used **unmodified** — including
the filler-neck handle — so the icon sits naturally beside Home Assistant's
own icons. Deliberate additions:

* The display, which MDI leaves as a hole, is filled. Left as a hole it shows
  the page through it, so it looked dark on a dark theme and white on a light
  one.
* A separate `dark_icon.png` inverts the pump body to light grey. The default
  dark navy body all but disappears against a dark card background.
* The cross bars are thicker than a true Dannebrog's ~1/7 ratio; at sidebar
  size the correct thin cross greys out into the red.
* The glyph is trimmed to ~96% of the canvas height, per Home Assistant's
  requirement that brand images contain minimal empty space. It is taller than
  it is wide, so the side margins are the leftover of keeping it square.

The cross sits left of centre — that is what makes a Dannebrog read as Danish
rather than as a generic centred cross.
