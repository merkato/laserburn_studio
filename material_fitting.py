from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from shapely import affinity
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.ops import polylabel
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem


@dataclass
class FitResult:
    scale: float
    angle_deg: float
    center: Tuple[float, float]   # docelowy środek grupy w scenie (mm)
    reference: Tuple[float, float]  # środek odniesienia grupy przed dopasowaniem

    def transform(self) -> QTransform:
        """Transformacja sceny: punkt odniesienia -> skala -> obrót -> środek docelowy."""
        rx, ry = self.reference
        cx, cy = self.center
        return (QTransform.fromTranslate(-rx, -ry)
                * QTransform.fromScale(self.scale, self.scale)
                * QTransform().rotate(self.angle_deg)
                * QTransform.fromTranslate(cx, cy))


class MaterialFittingOptimizer:
    """
    Dopasowanie grupy obiektów CAD do nieregularnego obrysu materiału (plaster drewna).

    - Geometria grupy: otoczka wypukła rzeczywistych punktów obiektów w scenie
      (z uwzględnieniem ich bieżących transformacji), a nie lokalne obwiednie.
    - Położenie: biegun niedostępności (polylabel) bezpiecznego obszaru – punkt najdalszy
      od krawędzi, także dla wklęsłych kształtów – oraz kandydaci wokół niego.
    - Skala: wyszukiwanie binarne; domyślnie tylko zmniejszanie (max_scale = 1.0).
    - Obrót: opcjonalny; przy remisie wygrywa najmniejszy kąt, więc projekt mieszczący się
      bez obrotu nie zostaje obrócony.
    """

    # ------------------------------------------------------------------ geometria
    @staticmethod
    def items_hull(items: Sequence[QGraphicsItem]) -> Optional[Polygon]:
        pts: List[Tuple[float, float]] = []
        for it in items:
            st = it.sceneTransform()
            if isinstance(it, QGraphicsPathItem):
                for poly in it.path().toSubpathPolygons():
                    for i in range(poly.count()):
                        p = st.map(poly.at(i))
                        pts.append((p.x(), p.y()))
            elif isinstance(it, QGraphicsPixmapItem):
                pix = it.pixmap()
                for x, y in ((0, 0), (pix.width(), 0), (pix.width(), pix.height()), (0, pix.height())):
                    p = st.map(QPointF(x, y))
                    pts.append((p.x(), p.y()))
            else:
                r = st.mapRect(it.boundingRect())
                pts += [(r.left(), r.top()), (r.right(), r.top()), (r.right(), r.bottom()), (r.left(), r.bottom())]
        if len(pts) < 3:
            return None
        hull = MultiPoint(pts).convex_hull
        return hull if isinstance(hull, Polygon) and hull.area > 1e-9 else None

    @staticmethod
    def safe_area(material_poly: Polygon, margin_mm: float) -> Optional[Polygon]:
        if material_poly is None or material_poly.is_empty:
            return None
        safe = material_poly.buffer(-margin_mm) if margin_mm > 0 else material_poly
        if safe.is_empty:
            return None
        if safe.geom_type == "MultiPolygon":
            safe = max(safe.geoms, key=lambda p: p.area)
        return safe if safe.is_valid else safe.buffer(0)

    @staticmethod
    def _candidates(safe: Polygon) -> List[Tuple[float, float]]:
        minx, miny, maxx, maxy = safe.bounds
        tol = max(0.05, min(maxx - minx, maxy - miny) / 200.0)
        pole = polylabel(safe, tolerance=tol)
        cands = [(pole.x, pole.y)]
        c = safe.centroid
        if safe.contains(c):
            cands.append((c.x, c.y))
        dx, dy = (maxx - minx) * 0.08, (maxy - miny) * 0.08
        for ox in (-dx, 0.0, dx):
            for oy in (-dy, 0.0, dy):
                if ox or oy:
                    p = Point(pole.x + ox, pole.y + oy)
                    if safe.contains(p):
                        cands.append((p.x, p.y))
        return cands

    # ------------------------------------------------------------------ wyszukiwanie
    @classmethod
    def fit_items(
        cls,
        items: Sequence[QGraphicsItem],
        material_poly: Polygon,
        margin_mm: float = 5.0,
        allow_rotation: bool = True,
        angle_step_deg: float = 5.0,
        max_scale: float = 1.0,
        min_scale: float = 0.01,
    ) -> Optional[FitResult]:
        """Zwraca najlepsze dopasowanie grupy lub None, gdy nie mieści się nawet w min_scale."""
        hull = cls.items_hull(items)
        safe = cls.safe_area(material_poly, margin_mm)
        if hull is None or safe is None:
            return None

        # Rastry nie mogą być obracane (G-code rastra skanuje wzdłuż osi X)
        if any(isinstance(it, QGraphicsPixmapItem) for it in items):
            allow_rotation = False

        ref = hull.centroid
        base = affinity.translate(hull, -ref.x, -ref.y)
        step = max(0.5, float(angle_step_deg))
        angles = [0.0]
        if allow_rotation:
            # Pełny obrót (otoczka nie musi być symetryczna); najpierw najmniejsze obroty w obie strony,
            # aby przy remisie skali wygrał kąt najbliższy 0°.
            angles = [i * step for i in range(int(math.floor(360.0 / step)))]
            angles.sort(key=lambda a: min(a, 360.0 - a))

        candidates = cls._candidates(safe)
        best: Optional[FitResult] = None

        for angle in angles:
            rotated = affinity.rotate(base, angle, origin=(0, 0))
            for cx, cy in candidates:
                placed = lambda s: affinity.translate(affinity.scale(rotated, s, s, origin=(0, 0)), cx, cy)
                if not placed(min_scale).within(safe):
                    continue
                if placed(max_scale).within(safe):
                    s_ok = max_scale
                else:
                    lo, hi = min_scale, max_scale
                    for _ in range(22):
                        mid = (lo + hi) / 2.0
                        if placed(mid).within(safe):
                            lo = mid
                        else:
                            hi = mid
                    s_ok = lo
                if best is None or s_ok > best.scale + 1e-6:
                    best = FitResult(s_ok, angle, (cx, cy), (ref.x, ref.y))
            if best is not None and best.scale >= max_scale - 1e-9:
                break  # pełny rozmiar przy najmniejszym możliwym obrocie
        return best

    @staticmethod
    def apply(items: Sequence[QGraphicsItem], result: FitResult) -> None:
        """Nakłada wspólną transformację grupy na obiekty (pozycje względne zachowane)."""
        g = result.transform()
        for it in items:
            m = it.sceneTransform() * g
            it.setRotation(0.0)
            it.setScale(1.0)
            it.setPos(0.0, 0.0)
            it.setTransform(m)

    # ------------------------------------------------------------------ zgodność wstecz
    @classmethod
    def fit_item_into_polygon(
        cls,
        target_item: QGraphicsItem,
        material_poly: Polygon,
        margin_mm: float = 10.0,
        allow_rotation: bool = True,
        angle_step_deg: float = 5.0
    ) -> Optional[Tuple[float, float, float, float]]:
        """Stare API: (skala, kąt, cx, cy) dla pojedynczego obiektu, bez limitu powiększenia."""
        res = cls.fit_items([target_item], material_poly, margin_mm, allow_rotation, angle_step_deg, max_scale=50.0)
        if res is None:
            return None
        return res.scale, res.angle_deg, res.center[0], res.center[1]
