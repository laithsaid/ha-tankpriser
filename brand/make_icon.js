/*
 * Tankpriser icon: Material Design's mdi:gas-station outline — the same pump
 * Home Assistant itself draws — with a Dannebrog on the pump body.
 *
 * The MDI path is a filled silhouette on a 24x24 grid: the body sits at
 * roughly x 4..14, y 3..21 with the display cut out as a hole at x 6..12,
 * y 5..10, and the filler-neck arm reaches to x 20.5. The flag goes on the
 * solid lower body; the display hole is filled so the icon looks identical on
 * light and dark backgrounds instead of showing the page through it.
 */
const fs = require("fs");
const sharp = require("sharp");

const GAS_STATION =
  "M18,10A1,1 0 0,1 17,9A1,1 0 0,1 18,8A1,1 0 0,1 19,9A1,1 0 0,1 18,10M12,10H6V5H12" +
  "M19.77,7.23L19.78,7.22L16.06,3.5L15,4.56L17.11,6.67C16.17,7 15.5,7.93 15.5,9A2.5,2.5 0 0,0 18,11.5" +
  "C18.36,11.5 18.69,11.42 19,11.29V18.5A1,1 0 0,1 18,19.5A1,1 0 0,1 17,18.5V14A2,2 0 0,0 15,12H14V5" +
  "A2,2 0 0,0 12,3H6A2,2 0 0,0 4,5V21H14V13.5H15.5V18.5A2.5,2.5 0 0,0 18,21A2.5,2.5 0 0,0 20.5,18.5V9" +
  "C20.5,8.31 20.22,7.68 19.77,7.23Z";

const DARK = "#262C33";
const RED = "#C60C30";
const WHITE = "#FFFFFF";

// Flag panel: as large as the solid lower body allows, so the cross survives
// being scaled down to sidebar size.
const fx = 5.3, fy = 11.4, fw = 7.4, fh = 8.2;
// Bars are deliberately thicker than a true Dannebrog (which would be ~1/7 of
// the height): at 48 px a thinner cross greys out into the red.
const barW = 1.75;
const vx = fx + fw * 0.33;   // cross left of centre = Danish, not Swiss
const hy = fy + fh * 0.40;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path d="${GAS_STATION}" fill="${DARK}"/>
  <rect x="6" y="5" width="6" height="5" fill="${WHITE}"/>
  <rect x="${fx}" y="${fy}" width="${fw}" height="${fh}" rx="0.45" fill="${RED}"/>
  <rect x="${vx}" y="${fy}" width="${barW}" height="${fh}" fill="${WHITE}"/>
  <rect x="${fx}" y="${hy}" width="${fw}" height="${barW}" fill="${WHITE}"/>
</svg>`;

fs.writeFileSync("tankpriser-icon.svg", svg);

(async () => {
  // Render big, trim the surrounding transparency, then centre it in a square
  // with even padding — MDI's 24x24 grid has uneven margins around this glyph.
  const big = await sharp(Buffer.from(svg), { density: 2048 })
    .resize(1600, 1600, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  const art = await sharp(big).trim({ threshold: 1 }).png().toBuffer();

  for (const size of [512, 256, 48]) {
    const inner = Math.round(size * 0.88);
    const resized = await sharp(art)
      .resize(inner, inner, { fit: "inside" })
      .toBuffer();
    const meta = await sharp(resized).metadata();
    await sharp({
      create: {
        width: size, height: size, channels: 4,
        background: { r: 0, g: 0, b: 0, alpha: 0 },
      },
    })
      .composite([{
        input: resized,
        left: Math.round((size - meta.width) / 2),
        top: Math.round((size - meta.height) / 2),
      }])
      .png()
      .toFile(`fin_${size}.png`);
  }
  console.log("wrote fin_512/256/48.png + tankpriser-icon.svg");
})();
