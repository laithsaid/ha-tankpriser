/*
 * Tankpriser brand images.
 *
 * The pump is Material Design's mdi:gas-station path, used unmodified — the
 * same fuel pump Home Assistant itself draws — with the Dannebrog on the body.
 *
 * Outputs go straight into custom_components/tankpriser/brand/, which is where
 * Home Assistant 2026.3+ reads brand images for custom integrations from.
 * Local images take priority over the brands CDN, so no PR to
 * home-assistant/brands is needed.
 *
 *   npm install sharp && node make_icon.js
 */
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

const OUT = path.join(__dirname, "..", "custom_components", "tankpriser", "brand");

// mdi:gas-station, verified against @mdi/svg
const GAS_STATION =
  "M18,10A1,1 0 0,1 17,9A1,1 0 0,1 18,8A1,1 0 0,1 19,9A1,1 0 0,1 18,10M12,10H6V5H12" +
  "M19.77,7.23L19.78,7.22L16.06,3.5L15,4.56L17.11,6.67C16.17,7 15.5,7.93 15.5,9A2.5,2.5 0 0,0 18,11.5" +
  "C18.36,11.5 18.69,11.42 19,11.29V18.5A1,1 0 0,1 18,19.5A1,1 0 0,1 17,18.5V14C17,12.89 16.1,12 15,12H14V5" +
  "C14,3.89 13.1,3 12,3H6C4.89,3 4,3.89 4,5V21H14V13.5H15.5V18.5A2.5,2.5 0 0,0 18,21A2.5,2.5 0 0,0 20.5,18.5V9" +
  "C20.5,8.31 20.22,7.68 19.77,7.23Z";

const RED = "#C60C30";
const WHITE = "#FFFFFF";

// Light theme: dark pump. Dark theme: light pump, or it disappears against a
// dark card background. The flag stays red in both — it is the brand.
const THEMES = {
  light: { body: "#262C33", display: "#FFFFFF" },
  dark: { body: "#E9ECEF", display: "#AEB6BF" },
};

// Flag panel on the solid lower body, as large as it fits.
const fx = 5.3, fy = 11.4, fw = 7.4, fh = 8.2;
// Bars are thicker than a true Dannebrog's ~1/7 ratio: at sidebar size the
// correct thin cross greys out into the red.
const barW = 1.75;
const vx = fx + fw * 0.33;   // left of centre = Danish, not a centred cross
const hy = fy + fh * 0.4;

function svgFor({ body, display }) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path d="${GAS_STATION}" fill="${body}"/>
  <rect x="6" y="5" width="6" height="5" fill="${display}"/>
  <rect x="${fx}" y="${fy}" width="${fw}" height="${fh}" rx="0.45" fill="${RED}"/>
  <rect x="${vx}" y="${fy}" width="${barW}" height="${fh}" fill="${WHITE}"/>
  <rect x="${fx}" y="${hy}" width="${fw}" height="${barW}" fill="${WHITE}"/>
</svg>`;
}

// Home Assistant requires square icons at exactly 256 and 512, trimmed so the
// subject nearly touches the edges. The glyph is taller than it is wide, so
// height fills the canvas and the leftover shows as narrow side margins.
const PAD = 0.02;

async function render(svg, size, file) {
  const big = await sharp(Buffer.from(svg), { density: 2048 })
    .resize(1600, 1600, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  const art = await sharp(big).trim({ threshold: 1 }).png().toBuffer();

  const inner = Math.round(size * (1 - 2 * PAD));
  const fitted = await sharp(art).resize(inner, inner, { fit: "inside" }).toBuffer();
  const meta = await sharp(fitted).metadata();

  await sharp({
    create: {
      width: size, height: size, channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  })
    .composite([{
      input: fitted,
      left: Math.round((size - meta.width) / 2),
      top: Math.round((size - meta.height) / 2),
    }])
    .png({ compressionLevel: 9 })
    .toFile(path.join(OUT, file));
  return file;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const written = [];
  for (const [theme, colors] of Object.entries(THEMES)) {
    const svg = svgFor(colors);
    const prefix = theme === "dark" ? "dark_icon" : "icon";
    fs.writeFileSync(path.join(__dirname, `tankpriser-${theme}.svg`), svg);
    written.push(await render(svg, 256, `${prefix}.png`));
    written.push(await render(svg, 512, `${prefix}@2x.png`));
  }
  console.log("wrote", written.join(", "));
})();
