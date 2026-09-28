"""Erzeugt die Browser-Symbole von HimbeerePi: weisse Wolke mit Himbeer-Zweig auf himbeerroter Kachel.

    python3 tools/make_icons.py        # braucht Pillow

Schreibt nach app/static/icons/:
  favicon.svg            Vektor-Symbol (moderne Browser, gestochen scharf)
  favicon-16/32.png      Rueckfall fuer aeltere Browser
  icon-192/512.png       grosse Fassungen (z.B. Lesezeichen, Startbildschirm unter Android)
  apple-touch-icon.png   iPhone/iPad-Startbildschirm (ohne Rundung, iOS rundet selbst)

Die Form steht nur einmal in SHAPES (Koordinaten wie im SVG, Flaeche 64x64) und gilt fuer alle Dateien.
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ICONS = os.path.join(ROOT, 'app', 'static', 'icons')

RASPBERRY = '#C2185B'
CLOUD = '#FFFFFF'
LEAF = '#43A047'
LEAF_DARK = '#2E7D32'
CORNER_RADIUS = 14            # Rundung der Kachel (von 64)

CLOUD_TRANSFORM = (4, 5, 0.87)                 # Wolke: verschieben x, y, skalieren
CLOUD_CIRCLES = [(19, 37, 10), (31, 30, 13), (45, 35, 10.5)]
CLOUD_BASE = (5, 35, 54, 16, 8)                # x, y, Breite, Hoehe, Eckenradius
SPRIG = (41, 19.5, 25, 0.85)                   # Zweig: x, y, Drehung (Grad), skalieren
SPRIG_STEM = (-0.9, -9, 1.8, 7, 0.9)           # x, y, Breite, Hoehe, Eckenradius
SPRIG_LEAVES = [(-4.2, -4.5, 2.8, 5.6, -50), (4.2, -4.5, 2.8, 5.6, 50)]   # cx, cy, rx, ry, Drehung


# ------------------------------------------------------------------ SVG

def svg():
    tx, ty, s = CLOUD_TRANSFORM
    sx, sy, sa, ss = SPRIG
    x, y, w, h, r = CLOUD_BASE
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">',
             f'<rect width="64" height="64" rx="{CORNER_RADIUS}" fill="{RASPBERRY}"/>',
             f'<g transform="translate({tx},{ty}) scale({s})" fill="{CLOUD}">']
    parts += [f'<circle cx="{cx}" cy="{cy}" r="{cr}"/>' for cx, cy, cr in CLOUD_CIRCLES]
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>')
    parts.append(f'<g transform="translate({sx},{sy}) rotate({sa}) scale({ss})">')
    stx, sty, stw, sth, str_ = SPRIG_STEM
    parts.append(f'<rect x="{stx}" y="{sty}" width="{stw}" height="{sth}" rx="{str_}" fill="{LEAF_DARK}"/>')
    for cx, cy, rx, ry, a in SPRIG_LEAVES:
        parts.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({a} {cx} {cy})" '
                     f'fill="{LEAF}" stroke="{LEAF_DARK}" stroke-width="0.8"/>')
    parts.append('</g></g></svg>')
    return ''.join(parts) + '\n'


# ------------------------------------------------------------------ PNG (gleiche Geometrie)

def _ellipse(cx, cy, rx, ry, angle=0, n=96):
    a = math.radians(angle)
    return [(cx + rx * math.cos(t) * math.cos(a) - ry * math.sin(t) * math.sin(a),
             cy + rx * math.cos(t) * math.sin(a) + ry * math.sin(t) * math.cos(a))
            for t in (2 * math.pi * i / n for i in range(n))]


def _rounded_rect(x, y, w, h, r, n=16):
    pts = []
    for cx, cy, start in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        pts += [(cx + r * math.cos(math.radians(start + 90 * i / n)), cy + r * math.sin(math.radians(start + 90 * i / n)))
                for i in range(n + 1)]
    return pts


def _transform(points, tx, ty, scale=1.0, angle=0):
    a = math.radians(angle)
    return [(tx + scale * (px * math.cos(a) - py * math.sin(a)), ty + scale * (px * math.sin(a) + py * math.cos(a)))
            for px, py in points]


def png(size, path, rounded=True):
    big = size * 8                                    # 8-fach ueberabgetastet fuer glatte Kanten
    k = big / 64
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(CORNER_RADIUS * k), fill=RASPBERRY)
    else:
        d.rectangle((0, 0, big, big), fill=RASPBERRY)

    tx, ty, s = CLOUD_TRANSFORM

    def draw(points, fill, outline=None, width=0):
        pts = [(px * k, py * k) for px, py in _transform(points, tx, ty, s)]
        d.polygon(pts, fill=fill, outline=outline, width=int(round(width * k)) if outline else 0)

    for cx, cy, cr in CLOUD_CIRCLES:
        draw(_ellipse(cx, cy, cr, cr), CLOUD)
    draw(_rounded_rect(*CLOUD_BASE), CLOUD)

    sx, sy, sa, ss = SPRIG
    draw(_transform(_rounded_rect(*SPRIG_STEM), sx, sy, ss, sa), LEAF_DARK)
    for cx, cy, rx, ry, a in SPRIG_LEAVES:
        leaf = _transform(_ellipse(cx, cy, rx, ry, a), sx, sy, ss, sa)
        draw(leaf, LEAF, outline=LEAF_DARK, width=0.8 * ss * s)

    img = img.resize((size, size), Image.LANCZOS)
    if not rounded:
        img = img.convert('RGB')
    img.save(path, optimize=True)


def main():
    with open(os.path.join(ICONS, 'favicon.svg'), 'w', encoding='utf-8') as f:
        f.write(svg())
    for size, name in ((16, 'favicon-16.png'), (32, 'favicon-32.png'), (192, 'icon-192.png'), (512, 'icon-512.png')):
        png(size, os.path.join(ICONS, name))
    png(180, os.path.join(ICONS, 'apple-touch-icon.png'), rounded=False)
    print('Symbole erzeugt.')


if __name__ == '__main__':
    main()
