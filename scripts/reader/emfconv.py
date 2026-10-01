"""Convert Word VML EMF previews to SVG and PNG.

Word.Picture.8 stores formula glyphs as separate EMR_EXTTEXTOUTW records.
Their anchors come from the current position (TA_UPDATECP) or the reference
point, mapped through the world transform and the window/viewport, with
per-character Dx advances. External EMF importers tested on these previews
either stack those glyphs or drop the layout, so playback is done here and
then rasterized by a PATH-discovered tool (rsvg-convert, else Inkscape).

A file on disk is not success. Fidelity checks have to pass, or the caller
keeps an unresolved issue and the original EMF bytes.
"""
from __future__ import annotations

import hashlib
import io
import math
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

# Adobe Symbol / Windows Symbol font byte → Unicode. Private-use U+F0xx uses the low byte.
_SYMBOL = {
    0x20: 0x20, 0x21: 0x21, 0x22: 0x2200, 0x23: 0x23, 0x24: 0x2203, 0x25: 0x25, 0x26: 0x26, 0x27: 0x220D,
    0x28: 0x28, 0x29: 0x29, 0x2A: 0x2217, 0x2B: 0x2B, 0x2C: 0x2C, 0x2D: 0x2212, 0x2E: 0x2E, 0x2F: 0x2F,
    0x30: 0x30, 0x31: 0x31, 0x32: 0x32, 0x33: 0x33, 0x34: 0x34, 0x35: 0x35, 0x36: 0x36, 0x37: 0x37,
    0x38: 0x38, 0x39: 0x39, 0x3A: 0x3A, 0x3B: 0x3B, 0x3C: 0x3C, 0x3D: 0x3D, 0x3E: 0x3E, 0x3F: 0x3F,
    0x40: 0x2245, 0x41: 0x391, 0x42: 0x392, 0x43: 0x3A7, 0x44: 0x394, 0x45: 0x395, 0x46: 0x3A6, 0x47: 0x393,
    0x48: 0x397, 0x49: 0x399, 0x4A: 0x3D1, 0x4B: 0x39A, 0x4C: 0x39B, 0x4D: 0x39C, 0x4E: 0x39D, 0x4F: 0x39F,
    0x50: 0x3A0, 0x51: 0x398, 0x52: 0x3A1, 0x53: 0x3A3, 0x54: 0x3A4, 0x55: 0x3A5, 0x56: 0x3C2, 0x57: 0x3A9,
    0x58: 0x39E, 0x59: 0x3A8, 0x5A: 0x396, 0x5B: 0x5B, 0x5C: 0x2234, 0x5D: 0x5D, 0x5E: 0x22A5, 0x5F: 0x5F,
    0x61: 0x3B1, 0x62: 0x3B2, 0x63: 0x3C7, 0x64: 0x3B4, 0x65: 0x3B5, 0x66: 0x3D5, 0x67: 0x3B3,
    0x68: 0x3B7, 0x69: 0x3B9, 0x6A: 0x3C6, 0x6B: 0x3BA, 0x6C: 0x3BB, 0x6D: 0x3BC, 0x6E: 0x3BD, 0x6F: 0x3BF,
    0x70: 0x3C0, 0x71: 0x3B8, 0x72: 0x3C1, 0x73: 0x3C3, 0x74: 0x3C4, 0x75: 0x3C5, 0x76: 0x3D6, 0x77: 0x3C9,
    0x78: 0x3BE, 0x79: 0x3C8, 0x7A: 0x3B6, 0x7B: 0x7B, 0x7C: 0x7C, 0x7D: 0x7D, 0x7E: 0x223C,
    0xA0: 0x25A1, 0xA1: 0x3D2, 0xA2: 0x2032, 0xA3: 0x2264, 0xA4: 0x2044, 0xA5: 0x221E, 0xA6: 0x192,
    0xA7: 0x2663, 0xA8: 0x2666, 0xA9: 0x2665, 0xAA: 0x2660, 0xAB: 0x2194, 0xAC: 0x2190, 0xAD: 0x2191,
    0xAE: 0x2192, 0xAF: 0x2193, 0xB0: 0xB0, 0xB1: 0xB1, 0xB2: 0x2033, 0xB3: 0x2265, 0xB4: 0xD7,
    0xB5: 0x221D, 0xB6: 0x2202, 0xB7: 0x2022, 0xB8: 0xF7, 0xB9: 0x2260, 0xBA: 0x2261, 0xBB: 0x2248,
    0xBC: 0x2026, 0xBD: 0x23D0, 0xBE: 0x23AF, 0xBF: 0x21B5, 0xC0: 0x2135, 0xC1: 0x2111, 0xC2: 0x211C,
    0xC3: 0x2118, 0xC4: 0x2297, 0xC5: 0x2295, 0xC6: 0x2205, 0xC7: 0x2229, 0xC8: 0x222A, 0xC9: 0x2283,
    0xCA: 0x2287, 0xCB: 0x2284, 0xCC: 0x2282, 0xCD: 0x2286, 0xCE: 0x2208, 0xCF: 0x2209, 0xD0: 0x2220,
    0xD1: 0x2207, 0xD2: 0xAE, 0xD3: 0xA9, 0xD4: 0x2122, 0xD5: 0x220F, 0xD6: 0x221A, 0xD7: 0x22C5,
    0xD8: 0xAC, 0xD9: 0x2227, 0xDA: 0x2228, 0xDB: 0x21D4, 0xDC: 0x21D0, 0xDD: 0x21D1, 0xDE: 0x21D2,
    0xDF: 0x21D3, 0xE0: 0x25CA, 0xE1: 0x2329, 0xE2: 0xAE, 0xE3: 0xA9, 0xE4: 0x2122, 0xE5: 0x2211,
    0xE6: 0x239B, 0xE7: 0x239C, 0xE8: 0x239D, 0xE9: 0x23A1, 0xEA: 0x23A2, 0xEB: 0x23A3, 0xEC: 0x23A7,
    0xED: 0x23A8, 0xEE: 0x23A9, 0xEF: 0x23AA, 0xF0: 0x20AC, 0xF1: 0x232A, 0xF2: 0x222B, 0xF3: 0x2320,
    0xF4: 0x23AE, 0xF5: 0x2321, 0xF6: 0x239E, 0xF7: 0x239F, 0xF8: 0x23A0, 0xF9: 0x23A4, 0xFA: 0x23A5,
    0xFB: 0x23A6, 0xFC: 0x23AB, 0xFD: 0x23AC, 0xFE: 0x23AD,
}

_TA_UPDATECP = 1
_TA_LEFT = 0
_TA_RIGHT = 2
_TA_CENTER = 6
_TA_BOTTOM = 8
_TA_BASELINE = 24
_PS_GEOMETRIC = 0x00010000
_STOCK = 0x80000000
_BLACK = '#000000'
_WHITE = '#ffffff'

_RASTER_CANDIDATES = ('rsvg-convert', 'inkscape')

# MS-EMF records that do not emit pixels. State we already apply is handled in
# _record; these remaining types are safe to skip (comments, palettes, ICM).
_IGNORABLE_RECORDS = frozenset({
    1, 13, 14, 16, 20, 21, 23, 48, 49, 50, 51, 52, 57, 58, 65, 66, 68, 70,
    98, 99, 100, 101, 104, 105, 106, 109, 110, 111, 112, 113, 115, 119,
    121, 122,
})
# EMR_BITBLT raster op "D": destination unchanged. Word previews use this with
# no source bits as a no-op; it must not be treated as missing picture content.
_BITBLT_NOP_ROP = 0x00AA0029

# MS-EMF drawing records that share one header (Bounds + Count + aPoints) but
# not one point width. PointL is signed 32-bit (8 bytes); PointS is signed
# 16-bit (4 bytes). close, bezier, and to-current-position stay per family.
# (close, bezier, to, wide)
_POLY_KIND = {
    2: (False, True, False, True),    # POLYBEZIER
    85: (False, True, False, False),   # POLYBEZIER16
    3: (True, False, False, True),     # POLYGON
    86: (True, False, False, False),    # POLYGON16
    4: (False, False, False, True),    # POLYLINE
    87: (False, False, False, False),   # POLYLINE16
    5: (False, True, True, True),      # POLYBEZIERTO
    88: (False, True, True, False),     # POLYBEZIERTO16
    6: (False, False, True, True),     # POLYLINETO
    89: (False, False, True, False),    # POLYLINETO16
}
_POLY_NAME = {
    2: 'POLYBEZIER', 85: 'POLYBEZIER16',
    3: 'POLYGON', 86: 'POLYGON16',
    4: 'POLYLINE', 87: 'POLYLINE16',
    5: 'POLYBEZIERTO', 88: 'POLYBEZIERTO16',
    6: 'POLYLINETO', 89: 'POLYLINETO16',
}
# EMR_POLYLINE allows at most 16K points. A larger count is not drawn in part.
_POLY_MAX_POINTS = 16384

# fc-match always returns some face. Only these families prove the requested coverage.
_TIMES_FAMILIES = frozenset({
    'times new roman', 'times', 'liberation serif', 'tinos',
    'nimbus roman', 'nimbus roman no9 l', 'texgyre termes', 'tex gyre termes',
    'free serif',
})
_SYMBOL_FAMILIES = frozenset({
    'opensymbol', 'star symbol', 'starsymbol', 'standard symbols l',
})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _which(name: str) -> str | None:
    found = shutil.which(name)
    return found if found else None


def _fc_family(request: str) -> str | None:
    """Matched family name only. Never returns a filesystem path."""
    fc = _which('fc-match')
    if not fc:
        return None
    try:
        out = subprocess.run(
            [fc, '-f', '%{family}', request],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    raw = (out.stdout or '').strip()
    if not raw:
        return None
    return raw.split(',')[0].strip() or None


def _family_allowed(family: str | None, allowed: frozenset[str]) -> bool:
    if not family:
        return False
    name = ' '.join(family.split()).casefold()
    if name in allowed:
        return True
    return any(name.startswith(item + ' ') for item in allowed)


def emf_env_status() -> dict:
    """PATH and fontconfig discovery for EMF preview conversion. No absolute tool or font paths are stored."""
    raster = next((name for name in _RASTER_CANDIDATES if _which(name)), None)
    times_family = _fc_family('Times New Roman')
    symbol_family = _fc_family('OpenSymbol')
    times_ok = _family_allowed(times_family, _TIMES_FAMILIES)
    symbol_ok = _family_allowed(symbol_family, _SYMBOL_FAMILIES)
    checks = {
        'rasterizer': raster,
        'times_font': times_ok,
        'times_family': times_family,
        'symbol_font': symbol_ok,
        'symbol_family': symbol_family,
    }
    checks['ready'] = bool(raster and times_ok and symbol_ok)
    missing = []
    if not raster:
        missing.append('rsvg-convert (librsvg2-bin) or inkscape')
    if not times_ok:
        detail = 'a fontconfig match for Times New Roman (fonts-liberation or fonts-croscore)'
        if times_family:
            detail += f'; fc-match returned {times_family}'
        missing.append(detail)
    if not symbol_ok:
        detail = 'OpenSymbol (fonts-opensymbol)'
        if symbol_family:
            detail += f'; fc-match returned {symbol_family}'
        missing.append(detail)
    checks['missing'] = missing
    return checks


def _colorref(value: int) -> str:
    return f'#{value & 255:02x}{(value >> 8) & 255:02x}{(value >> 16) & 255:02x}'


def _xml(text: str) -> str:
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _symbol_char(cp: int) -> str:
    if 0xF000 <= cp <= 0xF0FF:
        cp = cp & 0xFF
    mapped = _SYMBOL.get(cp)
    if mapped is None:
        return chr(cp) if cp >= 32 else ''
    return chr(mapped)


class _Pen:
    def __init__(self, color=_BLACK, width=1.0, style=0, geometric=False, null=False):
        self.color = color
        self.width = width
        self.style = style
        self.geometric = geometric
        self.null = null


class _Brush:
    def __init__(self, color=_WHITE, null=False):
        self.color = color
        self.null = null


class _Font:
    def __init__(self, name='Times New Roman', height=-16, italic=False, weight=400, charset=0, escapement=0):
        self.name = name
        self.height = height
        self.italic = italic
        self.weight = weight
        self.charset = charset
        self.escapement = escapement

    @property
    def symbol(self) -> bool:
        return self.charset == 2 or self.name.casefold() == 'symbol'


def _stock_object(index: int):
    key = index & 0xFFFF
    if key == 0:  # WHITE_BRUSH
        return _Brush(_WHITE)
    if key == 1:  # LTGRAY
        return _Brush('#c0c0c0')
    if key == 2:
        return _Brush('#808080')
    if key == 3:
        return _Brush('#404040')
    if key == 4:  # BLACK_BRUSH
        return _Brush(_BLACK)
    if key == 5:  # NULL_BRUSH
        return _Brush(null=True)
    if key == 6:  # WHITE_PEN
        return _Pen(_WHITE)
    if key == 7:  # BLACK_PEN
        return _Pen(_BLACK, 1.0, 0, False)
    if key == 8:  # NULL_PEN
        return _Pen(null=True)
    if key in (10, 11, 12, 13, 14, 16, 17):
        return _Font('Times New Roman', -16)
    return None


class EmfPlay:
    def __init__(self, blob: bytes):
        self.data = blob
        self.bounds = (0, 0, 1, 1)
        self.elements: list[str] = []
        self.glyphs: list[dict] = []
        self.labels: list[str] = []
        self.draw_ops = 0
        self.text_records = 0
        self.errors: list[str] = []
        self._init_state()

    def _init_state(self):
        self.objects: dict[int, object] = {}
        self.stack: list[dict] = []
        self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
        self.wnd_org = [0.0, 0.0]
        self.wnd_ext = [1.0, 1.0]
        self.vp_org = [0.0, 0.0]
        self.vp_ext = [1.0, 1.0]
        self.map_mode = 1  # MM_TEXT: window extent is stored but not applied
        self.cx = 0.0
        self.cy = 0.0
        self.align = 0
        self.text_color = _BLACK
        self.bk_color = _WHITE
        self.bk_mode = 2  # OPAQUE
        self.fill_mode = 'nonzero'
        self.pen = _Pen()
        self.brush = _Brush()
        self.font = _Font()
        self.in_path = False
        self.path: list[list[str]] = []
        self.sub: list[str] | None = None
        self.clips: list[str] = []
        self.clip_serial = 0

    def _snapshot(self) -> dict:
        return {
            'world': (self.m11, self.m12, self.m21, self.m22, self.dx, self.dy),
            'wnd_org': self.wnd_org[:], 'wnd_ext': self.wnd_ext[:],
            'vp_org': self.vp_org[:], 'vp_ext': self.vp_ext[:],
            'map_mode': self.map_mode,
            'cp': (self.cx, self.cy), 'align': self.align,
            'text_color': self.text_color, 'bk_color': self.bk_color, 'bk_mode': self.bk_mode,
            'fill_mode': self.fill_mode, 'pen': self.pen, 'brush': self.brush, 'font': self.font,
            'clips': self.clips[:],
        }

    def _restore_snap(self, snap: dict):
        self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = snap['world']
        self.wnd_org, self.wnd_ext = snap['wnd_org'], snap['wnd_ext']
        self.vp_org, self.vp_ext = snap['vp_org'], snap['vp_ext']
        self.map_mode = snap['map_mode']
        self.cx, self.cy = snap['cp']
        self.align = snap['align']
        self.text_color, self.bk_color, self.bk_mode = snap['text_color'], snap['bk_color'], snap['bk_mode']
        self.fill_mode = snap['fill_mode']
        self.pen, self.brush, self.font = snap['pen'], snap['brush'], snap['font']
        self.clips = snap['clips']

    def map_point(self, x: float, y: float) -> tuple[float, float]:
        wx = self.m11 * x + self.m21 * y + self.dx
        wy = self.m12 * x + self.m22 * y + self.dy
        # MM_TEXT maps one logical unit to one device pixel. Window origin and
        # extent are remembered but not applied until the mode is isotropic or
        # anisotropic. Equation objects set those modes around their own viewport.
        if self.map_mode == 1:
            return wx + self.vp_org[0], wy + self.vp_org[1]
        if self.wnd_ext[0] == 0 or self.wnd_ext[1] == 0:
            return wx, wy
        dx = (wx - self.wnd_org[0]) * self.vp_ext[0] / self.wnd_ext[0] + self.vp_org[0]
        dy = (wy - self.wnd_org[1]) * self.vp_ext[1] / self.wnd_ext[1] + self.vp_org[1]
        return dx, dy

    def _scale(self) -> tuple[float, float]:
        world_x = math.hypot(self.m11, self.m12) or 1.0
        world_y = math.hypot(self.m21, self.m22) or 1.0
        if self.map_mode == 1:
            return world_x, world_y
        sx = abs(self.vp_ext[0] / self.wnd_ext[0]) if self.wnd_ext[0] else 1.0
        sy = abs(self.vp_ext[1] / self.wnd_ext[1]) if self.wnd_ext[1] else 1.0
        return world_x * sx, world_y * sy

    def _clip_attr(self) -> str:
        if not self.clips:
            return ''
        return f' clip-path="url(#{self.clips[-1]})"'

    def _pen_width(self) -> float:
        if self.pen.null:
            return 0.0
        sx, sy = self._scale()
        if self.pen.geometric:
            return max(0.6, abs(self.pen.width) * max(sx, sy))
        return max(1.0, abs(self.pen.width) or 1.0)

    def _dash(self, width: float) -> str:
        kind = self.pen.style & 0xF
        w = max(width, 1.0)
        if kind == 1:
            return f' stroke-dasharray="{4*w:.1f} {2*w:.1f}"'
        if kind == 2:
            return f' stroke-dasharray="{w:.1f} {2.2*w:.1f}"'
        if kind == 3:
            return f' stroke-dasharray="{4*w:.1f} {2*w:.1f} {w:.1f} {2*w:.1f}"'
        if kind == 4:
            return f' stroke-dasharray="{4*w:.1f} {2*w:.1f} {w:.1f} {2*w:.1f} {w:.1f} {2*w:.1f}"'
        return ''

    def _stroke_attrs(self) -> str:
        if self.pen.null or (self.pen.style & 0xF) == 5:
            return ' fill="none" stroke="none"'
        width = self._pen_width()
        return f' fill="none" stroke="{self.pen.color}" stroke-width="{width:.2f}" stroke-linejoin="round" stroke-linecap="round"{self._dash(width)}'

    def _emit(self, svg: str):
        self.elements.append(svg)
        self.draw_ops += 1

    def _path_d(self, subs: list[list[str]] | None = None) -> str:
        parts = []
        for sub in (subs if subs is not None else self.path):
            if sub:
                parts.append(' '.join(sub))
        return ' '.join(parts)

    def _start_sub(self, x: float, y: float):
        dx, dy = self.map_point(x, y)
        self.sub = [f'M {dx:.2f} {dy:.2f}']
        self.path.append(self.sub)

    def _line_to(self, x: float, y: float):
        dx, dy = self.map_point(x, y)
        if self.sub is None:
            ox, oy = self.map_point(self.cx, self.cy)
            self.sub = [f'M {ox:.2f} {oy:.2f}']
            self.path.append(self.sub)
        self.sub.append(f'L {dx:.2f} {dy:.2f}')

    def _curve_to(self, pts: list[tuple[float, float]]):
        if self.sub is None:
            ox, oy = self.map_point(self.cx, self.cy)
            self.sub = [f'M {ox:.2f} {oy:.2f}']
            self.path.append(self.sub)
        for i in range(0, len(pts), 3):
            chunk = pts[i:i + 3]
            if len(chunk) < 3:
                break
            mapped = [self.map_point(px, py) for px, py in chunk]
            a, b, c = mapped
            self.sub.append(f'C {a[0]:.2f} {a[1]:.2f} {b[0]:.2f} {b[1]:.2f} {c[0]:.2f} {c[1]:.2f}')

    def _close_sub(self):
        if self.sub:
            self.sub.append('Z')
        self.sub = None

    def _draw_path(self, fill: bool, stroke: bool):
        d = self._path_d()
        self.path = []
        self.sub = None
        self.in_path = False
        if not d:
            return
        attrs = []
        if fill and not self.brush.null:
            attrs.append(f'fill="{self.brush.color}" fill-rule="{self.fill_mode}"')
        else:
            attrs.append('fill="none"')
        if stroke and not self.pen.null and (self.pen.style & 0xF) != 5:
            width = self._pen_width()
            attrs.append(
                f'stroke="{self.pen.color}" stroke-width="{width:.2f}" '
                f'stroke-linejoin="round" stroke-linecap="round"{self._dash(width)}'
            )
        else:
            attrs.append('stroke="none"')
        self._emit(f'<path d="{d}" {" ".join(attrs)}{self._clip_attr()}/>')

    def _immediate_path(self, fill: bool, stroke: bool):
        if self.in_path:
            return
        self._draw_path(fill, stroke)

    def _select(self, index: int):
        obj = _stock_object(index) if index & _STOCK else self.objects.get(index)
        if isinstance(obj, _Pen):
            self.pen = obj
        elif isinstance(obj, _Brush):
            self.brush = obj
        elif isinstance(obj, _Font):
            self.font = obj

    def _points16(self, off: int, count: int) -> list[tuple[float, float]]:
        return self._read_points(off, count, wide=False)

    def _read_points(self, off: int, count: int, wide: bool) -> list[tuple[float, float]]:
        fmt = '<ii' if wide else '<hh'
        stride = 8 if wide else 4
        pts = []
        for i in range(count):
            x, y = struct.unpack_from(fmt, self.data, off + i * stride)
            pts.append((float(x), float(y)))
        return pts

    def _poly(self, off: int, size: int, close: bool, bezier: bool, to: bool, wide: bool, name: str):
        # Header is Type+Size+Bounds(RectL, 16)+Count. aPoints follow at +28.
        # Word previews leave aPoints outside that rectangle, so only its presence is required.
        if size < 28 or off + 28 > len(self.data):
            self.errors.append(f'truncated EMR_{name} (size {size})')
            return
        count = struct.unpack_from('<I', self.data, off + 24)[0]
        if count > _POLY_MAX_POINTS:
            self.errors.append(f'EMR_{name} point count {count} exceeds {_POLY_MAX_POINTS}')
            return
        stride = 8 if wide else 4
        need = 28 + count * stride
        if size < need or off + need > len(self.data):
            self.errors.append(f'truncated EMR_{name} (size {size}, count {count})')
            return
        if bezier and count:
            complete = (count % 3 == 0) if to else (count >= 4 and (count - 1) % 3 == 0)
            if not complete:
                self.errors.append(f'EMR_{name} point count {count} is incomplete')
                return
        pts = self._read_points(off + 28, count, wide)
        if not pts:
            return
        if bezier:
            if to:
                self._curve_to(pts)
                self.cx, self.cy = pts[-1]
            else:
                self._start_sub(pts[0][0], pts[0][1])
                self.cx, self.cy = pts[0]
                self._curve_to(pts[1:])
                if pts:
                    self.cx, self.cy = pts[-1]
        else:
            if to:
                for x, y in pts:
                    self._line_to(x, y)
                    self.cx, self.cy = x, y
            else:
                self._start_sub(pts[0][0], pts[0][1])
                for x, y in pts[1:]:
                    self._line_to(x, y)
                self.cx, self.cy = pts[-1]
            if close:
                self._close_sub()
        if not self.in_path:
            self._immediate_path(fill=close, stroke=True)

    def _text(self, off: int, size: int):
        self.text_records += 1
        if size < 76:
            return
        gm = struct.unpack_from('<I', self.data, off + 24)[0]
        refx, refy, nchars, offstr, options = struct.unpack_from('<iiIII', self.data, off + 36)
        offdx = struct.unpack_from('<I', self.data, off + 72)[0] if size >= 76 else 0
        if nchars <= 0 or nchars > 4000 or off + offstr + nchars * 2 > off + size:
            return
        raw = self.data[off + offstr:off + offstr + nchars * 2]
        chars = raw.decode('utf-16le', 'replace')
        dxs: list[int] = []
        if offdx and off + offdx + nchars * 4 <= off + size:
            dxs = list(struct.unpack_from('<' + 'i' * nchars, self.data, off + offdx))
        use_cp = bool(self.align & _TA_UPDATECP)
        x, y = (self.cx, self.cy) if use_cp else (float(refx), float(refy))
        # GM_COMPATIBLE ignores the world transform. Word equation records are advanced;
        # an identity world makes the two modes agree, which is the common case here.
        saved_world = None
        if gm == 1:
            saved_world = (self.m11, self.m12, self.m21, self.m22, self.dx, self.dy)
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
        sx, sy = self._scale()
        font_px = max(1.0, abs(self.font.height) * sy)
        symbol = self.font.symbol
        family = 'OpenSymbol, DejaVu Sans, sans-serif' if symbol else 'Times New Roman, Liberation Serif, Tinos, serif'
        style = 'italic' if self.font.italic else 'normal'
        weight = '700' if self.font.weight >= 600 else '400'
        pieces = []
        cursor = x
        placed = []
        for i, ch in enumerate(chars):
            shown = _symbol_char(ord(ch)) if symbol else ch
            px, py = self.map_point(cursor, y)
            anchor_x, anchor_y = px, py
            if self.align & _TA_BASELINE:
                pass
            elif self.align & _TA_BOTTOM:
                anchor_y = py - 0.2 * font_px
            else:
                anchor_y = py + 0.8 * font_px
            if shown and shown != ' ':
                self.glyphs.append({'x': anchor_x, 'y': anchor_y, 'size': font_px, 'ch': shown})
            placed.append((anchor_x, anchor_y, shown))
            advance = dxs[i] if i < len(dxs) else int(abs(self.font.height) * 0.5)
            cursor += advance
        if saved_world is not None:
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = saved_world
        if use_cp:
            self.cx, self.cy = cursor, y
        visible = ''.join(p[2] for p in placed)
        if visible.strip():
            self.labels.append(visible)
        tspans = []
        for px, py, shown in placed:
            if shown == '':
                continue
            tspans.append(f'<tspan x="{px:.2f}" y="{py:.2f}">{_xml(shown)}</tspan>')
        if not tspans:
            return
        self._emit(
            f'<text font-family="{family}" font-size="{font_px:.2f}" font-style="{style}" '
            f'font-weight="{weight}" fill="{self.text_color}" xml:space="preserve"{self._clip_attr()}>'
            + ''.join(tspans) + '</text>'
        )

    def _set_world(self, vals, mode: int | None):
        a, b, c, d, e, f = vals
        if mode == 1:  # identity
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = 1, 0, 0, 1, 0, 0
            return
        if mode == 2:  # left multiply: Xnew = X * Xcurrent
            nm11 = a * self.m11 + b * self.m21
            nm12 = a * self.m12 + b * self.m22
            nm21 = c * self.m11 + d * self.m21
            nm22 = c * self.m12 + d * self.m22
            ndx = e * self.m11 + f * self.m21 + self.dx
            ndy = e * self.m12 + f * self.m22 + self.dy
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = nm11, nm12, nm21, nm22, ndx, ndy
            return
        if mode == 3:  # right multiply
            nm11 = self.m11 * a + self.m12 * c
            nm12 = self.m11 * b + self.m12 * d
            nm21 = self.m21 * a + self.m22 * c
            nm22 = self.m21 * b + self.m22 * d
            ndx = self.dx * a + self.dy * c + e
            ndy = self.dx * b + self.dy * d + f
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = nm11, nm12, nm21, nm22, ndx, ndy
            return
        # SETWORLDTRANSFORM
        self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = a, b, c, d, e, f

    def play(self) -> bool:
        data = self.data
        if len(data) < 88 or data[40:44] != b' EMF':
            self.errors.append('not an EMF')
            return False
        left, top, right, bottom = struct.unpack_from('<iiii', data, 8)
        self.bounds = (left, top, right, bottom)
        dev_w = float(right - left) or 1.0
        dev_h = float(bottom - top) or 1.0
        # Logical units already match the header bounds. szlDevice is the screen the
        # metafile was recorded on, so the initial viewport is the picture, not that screen.
        self.wnd_ext = [dev_w, dev_h]
        self.vp_ext = [dev_w, dev_h]
        self.vp_org = [0.0, 0.0]
        self.wnd_org = [0.0, 0.0]
        nrec = struct.unpack_from('<I', data, 52)[0]
        off = struct.unpack_from('<I', data, 4)[0]
        if off < 8 or off > len(data):
            off = 0
        seen = 0
        while off + 8 <= len(data) and seen < nrec + 5:
            typ, size = struct.unpack_from('<II', data, off)
            if size < 8 or off + size > len(data):
                self.errors.append(f'truncated record at {off}')
                break
            self._record(typ, off, size)
            if typ == 14:
                break
            off += size
            seen += 1
        if self.in_path and self.path:
            self._draw_path(False, True)
        return not self.errors and (self.draw_ops > 0)

    def _record(self, typ: int, off: int, size: int):
        data = self.data
        if typ == 33:  # SAVEDC
            self.stack.append(self._snapshot())
        elif typ == 34 and size >= 12:  # RESTOREDC
            rel = struct.unpack_from('<i', data, off + 8)[0]
            n = -rel if rel < 0 else 0
            snap = None
            for _ in range(min(n, len(self.stack))):
                snap = self.stack.pop()
            if snap:
                self._restore_snap(snap)
        elif typ == 35 and size >= 32:  # SETWORLDTRANSFORM
            self._set_world(struct.unpack_from('<ffffff', data, off + 8), None)
        elif typ == 36 and size >= 36:
            mode = struct.unpack_from('<I', data, off + 32)[0]
            self._set_world(struct.unpack_from('<ffffff', data, off + 8), mode)
        elif typ == 9 and size >= 16:
            self.wnd_ext = [float(v) for v in struct.unpack_from('<ii', data, off + 8)]
        elif typ == 10 and size >= 16:
            self.wnd_org = [float(v) for v in struct.unpack_from('<ii', data, off + 8)]
        elif typ == 11 and size >= 16:
            self.vp_ext = [float(v) for v in struct.unpack_from('<ii', data, off + 8)]
        elif typ == 12 and size >= 16:
            self.vp_org = [float(v) for v in struct.unpack_from('<ii', data, off + 8)]
        elif typ == 17 and size >= 12:
            self.map_mode = struct.unpack_from('<I', data, off + 8)[0]
        elif typ == 19 and size >= 12:
            mode = struct.unpack_from('<I', data, off + 8)[0]
            self.fill_mode = 'evenodd' if mode == 1 else 'nonzero'
        elif typ == 22 and size >= 12:
            self.align = struct.unpack_from('<I', data, off + 8)[0]
        elif typ == 24 and size >= 12:
            self.text_color = _colorref(struct.unpack_from('<I', data, off + 8)[0])
        elif typ == 25 and size >= 12:
            self.bk_color = _colorref(struct.unpack_from('<I', data, off + 8)[0])
        elif typ == 18 and size >= 12:
            self.bk_mode = struct.unpack_from('<I', data, off + 8)[0]
        elif typ == 27 and size >= 16:
            x, y = struct.unpack_from('<ii', data, off + 8)
            if self.in_path:
                self._start_sub(x, y)
            self.cx, self.cy = float(x), float(y)
        elif typ == 54 and size >= 16:  # LINETO
            x, y = struct.unpack_from('<ii', data, off + 8)
            self._line_to(x, y)
            self.cx, self.cy = float(x), float(y)
            if not self.in_path:
                self._immediate_path(False, True)
        elif typ == 37 and size >= 12:
            self._select(struct.unpack_from('<I', data, off + 8)[0])
        elif typ == 40 and size >= 12:
            self.objects.pop(struct.unpack_from('<I', data, off + 8)[0], None)
        elif typ == 38 and size >= 28:  # CREATEPEN
            ih, style = struct.unpack_from('<II', data, off + 8)
            width = struct.unpack_from('<i', data, off + 16)[0]
            color = _colorref(struct.unpack_from('<I', data, off + 24)[0])
            self.objects[ih] = _Pen(color, width, style, False, (style & 0xF) == 5)
        elif typ == 39 and size >= 24:  # CREATEBRUSHINDIRECT
            ih, bstyle, color = struct.unpack_from('<III', data, off + 8)
            self.objects[ih] = _Brush(null=True) if bstyle == 1 else _Brush(_colorref(color))
        elif typ == 95 and size >= 48:  # EXTCREATEPEN
            ih = struct.unpack_from('<I', data, off + 8)[0]
            style, width, bstyle, color = struct.unpack_from('<IIII', data, off + 28)
            self.objects[ih] = _Pen(
                _colorref(color), width, style, bool(style & _PS_GEOMETRIC), (style & 0xF) == 5,
            )
        elif typ == 84:
            self._text(off, size)
        elif typ == 82 and size >= 104:  # EXTCREATEFONTINDIRECTW
            ih = struct.unpack_from('<I', data, off + 8)[0]
            height, _width, esc, _ori, weight = struct.unpack_from('<iiiii', data, off + 12)
            italic = data[off + 32]
            charset = data[off + 35]
            name = data[off + 40:off + 40 + 64].decode('utf-16le', 'replace').split('\x00', 1)[0]
            self.objects[ih] = _Font(name or 'Times New Roman', height, bool(italic), weight, charset, esc)
        elif typ == 59:  # BEGINPATH
            self.in_path = True
            self.path = []
            self.sub = None
        elif typ == 60:  # ENDPATH
            self.in_path = False
        elif typ == 61:  # CLOSEFIGURE
            self._close_sub()
        elif typ == 62:  # FILLPATH
            self._draw_path(True, False)
        elif typ == 63:  # STROKEANDFILLPATH
            self._draw_path(True, True)
        elif typ == 64:  # STROKEPATH
            self._draw_path(False, True)
        elif typ in _POLY_KIND:
            close, bezier, to, wide = _POLY_KIND[typ]
            self._poly(off, size, close=close, bezier=bezier, to=to, wide=wide, name=_POLY_NAME[typ])
        elif typ == 8:  # POLYPOLYGON
            self._polypoly(off, close=True)
        elif typ == 7:
            self._polypoly(off, close=False)
        elif typ == 91:
            self._polypoly16(off, close=True)
        elif typ == 90:
            self._polypoly16(off, close=False)
        elif typ == 30 and size >= 24:  # INTERSECTCLIPRECT
            rect = struct.unpack_from('<iiii', data, off + 8)
            self._intersect_clip(rect)
        elif typ == 75 and size >= 16:  # EXTSELECTCLIPRGN
            mode = struct.unpack_from('<I', data, off + 8)[0]
            cb = struct.unpack_from('<I', data, off + 12)[0] if size >= 16 else 0
            if cb == 0 and mode == 5:
                self.clips = []
        elif typ == 67 and size >= 12:  # SELECTCLIPPATH
            d = self._path_d()
            self.path = []
            self.sub = None
            self.in_path = False
            if d:
                self.clip_serial += 1
                cid = f'c{self.clip_serial}'
                self.elements.append(f'<clipPath id="{cid}"><path d="{d}"/></clipPath>')
                self.clips.append(cid)
        elif typ == 43:  # RECTANGLE: Type(4)+Size(4)+Box(16)=24, Box at offset 8
            box = self._rectl(off, size, 'RECTANGLE')
            if box is None:
                return
            l, t, r, b = box
            self._start_sub(l, t)
            self._line_to(r, t)
            self._line_to(r, b)
            self._line_to(l, b)
            self._close_sub()
            if not self.in_path:
                self._immediate_path(True, True)
        elif typ == 42:  # ELLIPSE: same 24-byte layout as RECTANGLE
            box = self._rectl(off, size, 'ELLIPSE')
            if box is None:
                return
            l, t, r, b = box
            self._ellipse(l, t, r, b)
        elif typ == 76:  # BITBLT
            if self._noop_bitblt(off, size):
                return
            self.errors.append('unsupported drawing record EMR_BITBLT')
        elif typ in _IGNORABLE_RECORDS:
            return
        else:
            self.errors.append(f'unsupported drawing record type {typ}')

    def _rectl(self, off: int, size: int, name: str):
        if size < 24:
            self.errors.append(f'truncated EMR_{name} (size {size})')
            return None
        return struct.unpack_from('<iiii', self.data, off + 8)

    def _noop_bitblt(self, off: int, size: int) -> bool:
        if size < 100:
            return False
        rop = struct.unpack_from('<I', self.data, off + 40)[0]
        cb_bits = struct.unpack_from('<I', self.data, off + 96)[0]
        return rop == _BITBLT_NOP_ROP and cb_bits == 0

    def _ellipse(self, l, t, r, b):
        # Approximate with four cubics.
        x0, y0 = self.map_point(l, t)
        x1, y1 = self.map_point(r, b)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = abs(x1 - x0) / 2, abs(y1 - y0) / 2
        k = 0.55228475
        d = (
            f'M {cx + rx:.2f} {cy:.2f} '
            f'C {cx + rx:.2f} {cy + k * ry:.2f} {cx + k * rx:.2f} {cy + ry:.2f} {cx:.2f} {cy + ry:.2f} '
            f'C {cx - k * rx:.2f} {cy + ry:.2f} {cx - rx:.2f} {cy + k * ry:.2f} {cx - rx:.2f} {cy:.2f} '
            f'C {cx - rx:.2f} {cy - k * ry:.2f} {cx - k * rx:.2f} {cy - ry:.2f} {cx:.2f} {cy - ry:.2f} '
            f'C {cx + k * rx:.2f} {cy - ry:.2f} {cx + rx:.2f} {cy - k * ry:.2f} {cx + rx:.2f} {cy:.2f} Z'
        )
        if self.in_path:
            self.sub = [d]
            self.path.append(self.sub)
            self.sub = None
        else:
            fill = 'none' if self.brush.null else self.brush.color
            self._emit(
                f'<path d="{d}" fill="{fill}" fill-rule="{self.fill_mode}"{self._stroke_attrs().replace(" fill=\"none\"", "")}{self._clip_attr()}/>'
            )

    def _intersect_clip(self, rect):
        l, t, r, b = rect
        corners = [self.map_point(l, t), self.map_point(r, t), self.map_point(r, b), self.map_point(l, b)]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        self.clip_serial += 1
        cid = f'c{self.clip_serial}'
        self.elements.append(
            f'<clipPath id="{cid}"><rect x="{x0:.2f}" y="{y0:.2f}" width="{max(0.0, x1 - x0):.2f}" height="{max(0.0, y1 - y0):.2f}"/></clipPath>'
        )
        self.clips.append(cid)

    def _polypoly(self, off: int, close: bool):
        # 32-bit points. Bounds, nPolys, count, counts[], points[].
        if off + 32 > len(self.data):
            return
        npolys, total = struct.unpack_from('<II', self.data, off + 24)
        counts = list(struct.unpack_from('<' + 'I' * npolys, self.data, off + 32))
        pt = off + 32 + 4 * npolys
        for count in counts:
            pts = [struct.unpack_from('<ii', self.data, pt + i * 8) for i in range(count)]
            pt += count * 8
            if not pts:
                continue
            self._start_sub(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                self._line_to(x, y)
            if close:
                self._close_sub()
            self.cx, self.cy = float(pts[-1][0]), float(pts[-1][1])
        if not self.in_path:
            self._immediate_path(fill=close, stroke=True)

    def _polypoly16(self, off: int, close: bool):
        if off + 32 > len(self.data):
            return
        npolys = struct.unpack_from('<I', self.data, off + 24)[0]
        counts = list(struct.unpack_from('<' + 'I' * npolys, self.data, off + 32))
        pt = off + 32 + 4 * npolys
        for count in counts:
            pts = self._points16(pt, count)
            pt += count * 4
            if not pts:
                continue
            self._start_sub(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                self._line_to(x, y)
            if close:
                self._close_sub()
            self.cx, self.cy = pts[-1]
        if not self.in_path:
            self._immediate_path(fill=close, stroke=True)

    def to_svg(self) -> str:
        left, top, right, bottom = self.bounds
        width = max(1.0, float(right - left))
        height = max(1.0, float(bottom - top))
        body = '\n'.join(self.elements)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.2f}" height="{height:.2f}" '
            f'viewBox="{left} {top} {width:.2f} {height:.2f}">\n'
            f'<rect x="{left}" y="{top}" width="{width:.2f}" height="{height:.2f}" fill="#ffffff"/>\n'
            f'{body}\n</svg>\n'
        )


def _ascii_labels(labels: list[str]) -> list[str]:
    import re
    found = []
    for label in labels:
        for match in re.findall(r'[A-Za-z][A-Za-z ]{5,}', label):
            text = ' '.join(match.split())
            if len(text) >= 6:
                found.append(text)
    return found


def _svg_text(svg: str) -> str:
    """Character data only, so per-glyph tspans still form the original words."""
    import re
    return re.sub(r'<[^>]+>', '', svg)


def _glyph_fidelity(glyphs: list[dict], bounds) -> tuple[bool, str]:
    left, top, right, bottom = bounds
    width = float(right - left) or 1.0
    height = float(bottom - top) or 1.0
    pad_x, pad_y = width * 0.08, height * 0.08
    ink = [g for g in glyphs if g['ch'].strip()]
    if len(ink) < 4:
        return True, f'{len(ink)} glyphs'
    inside = 0
    for g in ink:
        if left - pad_x <= g['x'] <= right + pad_x and top - pad_y <= g['y'] <= bottom + pad_y:
            inside += 1
    if inside / len(ink) < 0.75:
        return False, f'only {inside}/{len(ink)} glyphs fall inside the EMF bounds'
    # Same-baseline stacks: another glyph covers most of this one and shares its baseline.
    bad = 0
    for i, g in enumerate(ink):
        box_w = max(1.0, 0.55 * g['size'])
        box_h = max(1.0, 0.9 * g['size'])
        gx0, gy0, gx1, gy1 = g['x'], g['y'] - 0.8 * g['size'], g['x'] + box_w, g['y'] + 0.15 * g['size']
        area = box_w * box_h
        for j, h in enumerate(ink):
            if i == j:
                continue
            if abs(g['y'] - h['y']) > 0.28 * max(g['size'], h['size']):
                continue
            hx0, hy0 = h['x'], h['y'] - 0.8 * h['size']
            hx1, hy1 = h['x'] + max(1.0, 0.55 * h['size']), h['y'] + 0.15 * h['size']
            ix0, iy0 = max(gx0, hx0), max(gy0, hy0)
            ix1, iy1 = min(gx1, hx1), min(gy1, hy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            if (ix1 - ix0) * (iy1 - iy0) / area > 0.55:
                bad += 1
                break
    ratio = bad / len(ink)
    if ratio > 0.12:
        return False, f'{bad}/{len(ink)} glyphs overlap on the same baseline ({ratio:.0%})'
    return True, f'{inside}/{len(ink)} glyphs inside, {bad} overlapped'


def fidelity_report(play: EmfPlay, svg: str, png: bytes | None) -> tuple[bool, list[dict]]:
    checks = []

    def add(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    add('emf_drawn', play.draw_ops > 0 and not play.errors, f'draw_ops={play.draw_ops} errors={play.errors[:3]}')
    left, top, right, bottom = play.bounds
    aspect = abs((right - left) / (bottom - top)) if bottom != top else 0
    add('aspect_known', aspect > 0.05, f'bounds={play.bounds} aspect={aspect:.3f}')
    labels = _ascii_labels(play.labels)
    visible = _svg_text(svg)
    missing = [label for label in labels if label not in visible]
    if play.text_records and not visible.strip():
        missing = missing or ['(no text emitted)']
    add('labels_in_svg', not missing, f'kept {labels[:8]} missing {missing[:6]}')
    ok_glyphs, glyph_detail = _glyph_fidelity(play.glyphs, play.bounds)
    if play.text_records and play.labels and len([g for g in play.glyphs if g['ch'].strip()]) < 4:
        ok_glyphs = False
        glyph_detail = 'EMF text was not placed into the SVG'
    add('glyph_layout', ok_glyphs, glyph_detail)
    if png is None:
        add('png', False, 'no raster')
    else:
        png_ok, png_detail = _png_fidelity(png, aspect)
        add('png', png_ok, png_detail)
    return all(item['ok'] for item in checks), checks


def _png_fidelity(png: bytes, aspect: float) -> tuple[bool, str]:
    if len(png) < 400 or png[:8] != b'\x89PNG\r\n\x1a\n':
        return False, 'not a PNG'
    try:
        from PIL import Image
    except Exception as exc:
        return False, f'Pillow cannot read the raster ({exc})'
    try:
        im = Image.open(io.BytesIO(png))
        im.load()
    except Exception as exc:
        return False, f'PNG decode failed ({exc})'
    w, h = im.size
    if w < 32 or h < 32:
        return False, f'raster too small {w}x{h}'
    got = w / h
    if aspect and abs(got - aspect) / aspect > 0.12:
        return False, f'aspect {got:.3f} does not match EMF {aspect:.3f}'
    sample = im.convert('RGB')
    if max(w, h) > 240:
        scale = 240 / max(w, h)
        sample = sample.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    raw = sample.tobytes()
    ink = 0
    total = len(raw) // 3
    for i in range(0, len(raw), 3):
        if raw[i] < 248 or raw[i + 1] < 248 or raw[i + 2] < 248:
            ink += 1
    ratio = ink / total if total else 0.0
    if ratio < 0.002:
        return False, f'raster is blank ({ratio:.4%} nonwhite)'
    if ratio > 0.85:
        return False, f'raster is nearly solid ({ratio:.0%} nonwhite)'
    return True, f'{w}x{h} nonwhite={ratio:.2%}'


def render_emf_svg(blob: bytes) -> tuple[EmfPlay | None, str | None]:
    play = EmfPlay(blob)
    if not play.play():
        return play, None
    return play, play.to_svg()


def rasterize_svg(svg: str, width: int) -> tuple[bytes | None, str | None, str]:
    """Rasterize SVG with the first PATH tool that succeeds. Returns png, tool basename, error."""
    env = emf_env_status()
    tool = env['rasterizer']
    if not tool:
        return None, None, 'no rsvg-convert or inkscape on PATH'
    path = _which(tool)
    if not path:
        return None, None, f'{tool} disappeared from PATH'
    with tempfile.TemporaryDirectory(prefix='emf-') as tmp:
        svg_path = Path(tmp) / 'preview.svg'
        png_path = Path(tmp) / 'preview.png'
        svg_path.write_text(svg, encoding='utf-8')
        if tool == 'rsvg-convert':
            cmd = [path, '-w', str(width), '-b', 'white', '-o', str(png_path), str(svg_path)]
        else:
            cmd = [path, str(svg_path), '--export-filename', str(png_path), '--export-width', str(width),
                   '--export-background', '#ffffff']
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, tool, str(exc)
        if proc.returncode != 0 or not png_path.is_file() or png_path.stat().st_size < 400:
            err = (proc.stderr or proc.stdout or 'rasterizer failed').strip().splitlines()
            return None, tool, err[-1][:300] if err else 'rasterizer failed'
        return png_path.read_bytes(), tool, ''


def convert_emf_preview(blob: bytes) -> dict:
    """Convert one EMF preview. ok is true only when fidelity checks pass."""
    env = emf_env_status()
    record = {
        'backend': 'emf-gdi-playback',
        'input_sha256': _sha256(blob),
        'environment': {k: env[k] for k in (
            'rasterizer', 'times_font', 'times_family', 'symbol_font', 'symbol_family', 'ready', 'missing',
        )},
        'fidelity': [],
        'ok': False,
    }
    play, svg = render_emf_svg(blob)
    if play is None or svg is None:
        record['error'] = '; '.join(play.errors) if play else 'empty'
        return {'ok': False, 'svg': None, 'png': None, 'record': record, 'reason': record['error']}
    if not env['ready']:
        missing = ', '.join(env['missing']) or 'converter environment incomplete'
        record['error'] = missing
        record['fidelity'] = [{'name': 'environment', 'ok': False, 'detail': missing}]
        return {'ok': False, 'svg': svg, 'png': None, 'record': record, 'reason': missing}
    left, top, right, bottom = play.bounds
    width = max(900, min(2400, int(abs(right - left) or 900)))
    png, tool, err = rasterize_svg(svg, width)
    record['rasterizer'] = tool
    if png is None:
        record['error'] = err or 'raster failed'
        record['fidelity'] = [{'name': 'png', 'ok': False, 'detail': record['error']}]
        return {'ok': False, 'svg': svg, 'png': None, 'record': record, 'reason': record['error']}
    ok, checks = fidelity_report(play, svg, png)
    record['fidelity'] = checks
    record['png_sha256'] = _sha256(png)
    record['svg_sha256'] = _sha256(svg.encode('utf-8'))
    record['bounds'] = list(play.bounds)
    record['text_records'] = play.text_records
    record['draw_ops'] = play.draw_ops
    record['ok'] = ok
    if not ok:
        failed = [c['name'] for c in checks if not c['ok']]
        reason = 'fidelity checks failed: ' + ', '.join(failed)
        record['error'] = reason
        return {'ok': False, 'svg': svg, 'png': png, 'record': record, 'reason': reason}
    return {'ok': True, 'svg': svg, 'png': png, 'record': record, 'reason': 'fidelity checks passed'}
