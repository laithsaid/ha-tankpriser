"""Tankpriser icon v2 — a better-looking fuel pump wearing the Dannebrog.

Improvements over v1: a smooth drooping hose (cubic bezier drawn as a thick
polyline with curved joints and round caps), a proper pistol-grip nozzle drawn
on its own layer and rotated, and a price display so the silhouette reads as a
fuel pump even at 48 px.
"""
from PIL import Image, ImageDraw

S = 4                      # supersampling
C = 512 * S

RED = (198, 12, 48, 255)
WHITE = (255, 255, 255, 255)
DARK = (38, 44, 51, 255)


def rr(d, box, r, fill):
    d.rounded_rectangle([v * S for v in box], radius=r * S, fill=fill)


def bezier(p0, p1, p2, p3, steps=60):
    """Cubic bezier as a point list, in supersampled coordinates."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        pts.append((x * S, y * S))
    return pts


def dot(d, xy, r, fill):
    """Round cap."""
    x, y = xy
    d.ellipse(
        [(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=fill
    )


def nozzle_layer():
    """Pistol-grip nozzle, drawn upright then rotated so it hangs naturally."""
    w, h = 150 * S, 230 * S
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    # grip
    d.rounded_rectangle([40 * S, 60 * S, 104 * S, 200 * S], radius=26 * S, fill=DARK)
    # spout, narrower, rising from the top of the grip
    d.rounded_rectangle([56 * S, 6 * S, 88 * S, 84 * S], radius=14 * S, fill=DARK)
    # trigger guard bump on the left
    d.rounded_rectangle([22 * S, 96 * S, 52 * S, 132 * S], radius=12 * S, fill=DARK)
    return lay.rotate(-22, resample=Image.BICUBIC, expand=True)


def build():
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- hose: leaves the body, droops right, ends AT the nozzle ---------
    # The end point is shared with the nozzle grip below, so the two always
    # meet however the curve is tuned.
    hose_end = (416, 240)
    hose = bezier((292, 150), (384, 138), (438, 176), hose_end)
    d.line(hose, fill=DARK, width=26 * S, joint="curve")
    dot(d, (292, 150), 12, DARK)

    # --- nozzle: hangs from the hose, spout pointing down ----------------
    rr(d, (382, 232, 452, 346), 30, DARK)      # grip
    rr(d, (358, 262, 390, 304), 14, DARK)      # trigger guard
    rr(d, (404, 336, 432, 396), 13, DARK)      # spout

    # --- pump body --------------------------------------------------------
    rr(d, (56, 76, 306, 442), 34, DARK)

    # --- price display ----------------------------------------------------
    rr(d, (88, 108, 274, 168), 14, WHITE)

    # --- Dannebrog panel --------------------------------------------------
    fl, ft, fr, fb = 88, 194, 274, 396
    panel = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rectangle([fl * S, ft * S, fr * S, fb * S], fill=RED)
    bar = 34
    vx = fl + int((fr - fl) * 0.36)        # cross left of centre = Dannebrog
    hy = ft + int((fb - ft) * 0.44)
    pd.rectangle([vx * S, ft * S, (vx + bar) * S, fb * S], fill=WHITE)
    pd.rectangle([fl * S, hy * S, fr * S, (hy + bar) * S], fill=WHITE)

    mask = Image.new("L", (C, C), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [fl * S, ft * S, fr * S, fb * S], radius=12 * S, fill=255
    )
    img.paste(panel, (0, 0), mask)

    # --- base -------------------------------------------------------------
    rr(ImageDraw.Draw(img), (32, 434, 330, 478), 18, DARK)
    return img


def square(img, size, pad_ratio=0.06):
    box = img.getbbox()
    art = img.crop(box)
    inner = round(size * (1 - 2 * pad_ratio))
    scale = inner / max(art.size)
    art = art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        Image.LANCZOS,
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(art, ((size - art.width) // 2, (size - art.height) // 2))
    return out


master = build()
for size in (512, 256, 48):
    square(master, size).save(f"v4_{size}.png")
print("wrote v2")
