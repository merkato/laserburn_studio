"""
Proste kształty geometryczne i symbole do wstawiania na kanwę.

Wszystkie funkcje zwracają QPainterPath w milimetrach, wyśrodkowany w (0, 0),
w układzie sceny z osią Y skierowaną w górę (wierzchołek gwiazdy i łuki serca u góry).
Obiekt na kanwie dostaje pozycję = środek, więc kształt łatwo ustawić liczbowo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

SHAPES = ("line", "rect", "ellipse", "polygon", "star", "heart")
SHAPE_LABELS: Dict[str, str] = {
    "line": "Linia",
    "rect": "Prostokąt",
    "ellipse": "Elipsa / okrąg",
    "polygon": "Wielokąt foremny",
    "star": "Gwiazda",
    "heart": "Serce",
}
SHAPE_ICONS: Dict[str, str] = {
    "line": "╱", "rect": "▭", "ellipse": "◯", "polygon": "⬡", "star": "★", "heart": "♥",
}


@dataclass
class ShapeSpec:
    """Parametry kształtu. Szerokość/wysokość to wymiary obrysu (bounding box) w mm."""
    kind: str = "rect"
    width: float = 40.0
    height: float = 40.0
    corner_radius: float = 0.0     # prostokąt
    sides: int = 6                 # wielokąt
    points: int = 5                # gwiazda
    inner_ratio: float = 0.5       # gwiazda: promień wewnętrzny / zewnętrzny
    length: float = 50.0           # linia
    angle: float = 0.0             # linia: kąt [°] od osi X; wielokąt/gwiazda: obrót


def _fit_polygon(pts: List[Tuple[float, float]], width: float, height: float) -> QPainterPath:
    """Skaluje zamknięty wielokąt do obrysu width x height i centruje obrys w (0, 0)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = width / (x1 - x0) if x1 > x0 else 1.0
    sy = height / (y1 - y0) if y1 > y0 else 1.0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    path = QPainterPath()
    path.moveTo((pts[0][0] - cx) * sx, (pts[0][1] - cy) * sy)
    for x, y in pts[1:]:
        path.lineTo((x - cx) * sx, (y - cy) * sy)
    path.closeSubpath()
    return path


def line_path(length: float, angle_deg: float = 0.0) -> QPainterPath:
    a = math.radians(angle_deg)
    dx, dy = math.cos(a) * length / 2.0, math.sin(a) * length / 2.0
    path = QPainterPath()
    path.moveTo(-dx, -dy)
    path.lineTo(dx, dy)
    return path


def rect_path(width: float, height: float, corner_radius: float = 0.0) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, min(corner_radius, width / 2.0, height / 2.0))
    rect = QRectF(-width / 2.0, -height / 2.0, width, height)
    if r > 0:
        path.addRoundedRect(rect, r, r)
    else:
        # Jawnie od lewego dolnego rogu – przewidywalny punkt startu wypalania
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
    return path


def ellipse_path(width: float, height: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(0.0, 0.0), width / 2.0, height / 2.0)
    return path


def polygon_path(width: float, height: float, sides: int = 6, rotation_deg: float = 0.0) -> QPainterPath:
    n = max(3, int(sides))
    base = math.pi / 2.0 + math.radians(rotation_deg)  # pierwszy wierzchołek u góry
    pts = [(math.cos(base + 2 * math.pi * i / n), math.sin(base + 2 * math.pi * i / n)) for i in range(n)]
    return _fit_polygon(pts, width, height)


def star_path(width: float, height: float, points: int = 5, inner_ratio: float = 0.5,
              rotation_deg: float = 0.0) -> QPainterPath:
    n = max(3, int(points))
    r_in = min(0.95, max(0.05, inner_ratio))
    base = math.pi / 2.0 + math.radians(rotation_deg)
    pts = []
    for i in range(2 * n):
        r = 1.0 if i % 2 == 0 else r_in
        a = base + math.pi * i / n
        pts.append((r * math.cos(a), r * math.sin(a)))
    return _fit_polygon(pts, width, height)


def heart_path(width: float, height: float, samples: int = 240) -> QPainterPath:
    """Klasyczna krzywa serca (x = 16 sin³t, y = 13cos t − 5cos 2t − 2cos 3t − cos 4t), czubek na dole."""
    n = max(48, int(samples))
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x, y))
    return _fit_polygon(pts, width, height)


def build_path(spec: ShapeSpec) -> QPainterPath:
    w, h = max(0.1, spec.width), max(0.1, spec.height)
    if spec.kind == "line":
        return line_path(max(0.1, spec.length), spec.angle)
    if spec.kind == "rect":
        return rect_path(w, h, spec.corner_radius)
    if spec.kind == "ellipse":
        return ellipse_path(w, h)
    if spec.kind == "polygon":
        return polygon_path(w, h, spec.sides, spec.angle)
    if spec.kind == "star":
        return star_path(w, h, spec.points, spec.inner_ratio, spec.angle)
    if spec.kind == "heart":
        return heart_path(w, h)
    raise ValueError(f"Nieznany kształt: {spec.kind}")
