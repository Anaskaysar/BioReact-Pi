"""Simulated pH indicator — reads real camera color, interprets it as phenol red.

Real E. coli cultures don't change color enough to see on camera. Instead of
inventing a fake signal, this simulates having dosed the medium with phenol
red — the actual colorimetric pH indicator used in cell-culture media
(DMEM, RPMI, etc.): yellow at low pH, red/pink near neutral, magenta/purple
at high pH. So the *color extraction* below is real image analysis of
whatever the camera ROI is actually pointed at; only the pH *interpretation*
of that color is simulated (there's no real dye in the flask).

Color -> pH mapping (phenol red in standard culture media): yellow ~ low pH,
red/pink ~ neutral, magenta/purple ~ high pH.

Status rule for THIS E. coli culture (see _status_for_ph / PH_GOOD_MAX):
  pH <= 6.8  -> GOOD: the culture is acidifying its medium as growing E. coli
                does (mixed-acid fermentation), which is the healthy sign here.
  pH >  6.8  -> BAD:  out of range -> triggers a dashboard alert.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment,misc]

# Fractional ROI box (matches the yellow "COLOR ROI" box drawn on the mock
# frame / shown in the dashboard) — center-ish region of the frame, avoiding
# edges/glare.
ROI_BOX = (0.30, 0.25, 0.70, 0.65)  # (left, top, right, bottom) as fractions

# Phenol red hue reference points, in "unwrapped" degrees: the real hue path
# (yellow -> red -> magenta -> purple) crosses the 0/360 wraparound point, so
# raw HSV hue isn't monotonic along it (355deg red is numerically far from
# 10deg red-orange despite being right next to it on the color wheel).
# Unwrapping (subtracting 360 from anything > 180deg) makes it a clean
# decreasing sequence: yellow=50 -> red=~0 (355 -> -5) -> purple=~-60 (300).
_HUE_PH_POINTS = [
    (50.0, 6.2),   # yellow -> acidic floor
    (30.0, 6.8),
    (10.0, 7.2),
    (-5.0, 7.4),   # red/pink (355deg unwrapped) -> optimal center
    (-25.0, 7.8),  # 335deg unwrapped
    (-60.0, 8.4),  # magenta/purple (300deg unwrapped) -> alkaline ceiling
]


def _unwrap_hue(hue_deg: float) -> float:
    """Map 0-360deg hue onto a continuous scale centered on red (0deg),
    so magenta/purple (~270-360deg) comes out as small negative numbers
    instead of jumping back up near 360."""
    return hue_deg - 360.0 if hue_deg > 180.0 else hue_deg


def _interpolate_hue_to_ph(hue_deg: float) -> float:
    """Piecewise-linear hue -> pH, walking the reference points high-to-low hue."""
    x = _unwrap_hue(hue_deg)
    pts = _HUE_PH_POINTS
    if x >= pts[0][0]:
        return pts[0][1]
    if x <= pts[-1][0]:
        return pts[-1][1]
    for (h0, p0), (h1, p1) in zip(pts, pts[1:]):
        if h1 <= x <= h0:
            t = (x - h0) / (h1 - h0)
            return p0 + t * (p1 - p0)
    return pts[-1][1]


@dataclass
class PhReading:
    ph: float
    status: str  # "good" | "bad"
    label: str
    rgb_avg: tuple[int, int, int]
    hue_deg: float


# pH threshold for this E. coli culture: at or below 6.8 is good (the culture
# is actively fermenting and acidifying its medium, as growing E. coli does);
# above 6.8 is out of range and should trigger an alert.
PH_GOOD_MAX = 6.8


def _status_for_ph(ph: float) -> tuple[str, str]:
    if ph <= PH_GOOD_MAX:
        return "good", f"pH {ph:.2f} — within range for E. coli (<= {PH_GOOD_MAX})"
    return "bad", f"pH {ph:.2f} above {PH_GOOD_MAX} — out of range for E. coli, check the culture"


def analyze_frame(jpeg_bytes: bytes) -> PhReading | None:
    """Extract the ROI's average color from a real camera frame and map it
    to a simulated phenol-red pH reading. Returns None if Pillow is missing
    or the bytes aren't a decodable image."""
    if Image is None or not jpeg_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 — any decode failure just means "no reading"
        return None

    w, h = img.size
    left, top, right, bottom = ROI_BOX
    box = (int(w * left), int(h * top), int(w * right), int(h * bottom))
    roi = img.crop(box)

    # Downsample to a few pixels for a fast, noise-resistant average.
    roi_small = roi.resize((16, 16))
    pixels = list(roi_small.getdata())
    r = sum(p[0] for p in pixels) / len(pixels)
    g = sum(p[1] for p in pixels) / len(pixels)
    b = sum(p[2] for p in pixels) / len(pixels)

    hue_frac, _sat, _val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue_deg = hue_frac * 360.0

    ph = round(_interpolate_hue_to_ph(hue_deg), 2)
    status, label = _status_for_ph(ph)

    return PhReading(
        ph=ph,
        status=status,
        label=label,
        rgb_avg=(int(r), int(g), int(b)),
        hue_deg=round(hue_deg, 1),
    )
