import math
from typing import Optional, Tuple
from shapely.geometry import Polygon, box
from shapely import affinity
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QGraphicsItem


class MaterialFittingOptimizer:
    """
    Optymalizator dopasowania geometrii CAD/CAM wewnątrz nieregularnego obrysu materiału.
    """

    @staticmethod
    def fit_item_into_polygon(
        target_item: QGraphicsItem,
        material_poly: Polygon,
        margin_mm: float = 10.0,
        allow_rotation: bool = True,
        angle_step_deg: float = 5.0
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        Dopasowuje target_item do material_poly.
        Zwraca: (scale_factor, best_rotation_deg, center_x, center_y) lub None, gdy brak miejsca.
        """
        if not material_poly or material_poly.is_empty:
            return None

        # 1. Zastosowanie wewnętrznego marginesu bezpieczeństwa
        safe_poly = material_poly.buffer(-margin_mm)
        if safe_poly.is_empty or not safe_poly.is_valid:
            return None

        # Jeśli bufor rozpadł się na MultiPolygon (np. przewężenia), bierzemy największą część
        if safe_poly.geom_type == 'MultiPolygon':
            safe_poly = max(safe_poly.geoms, key=lambda p: p.area)

        # 2. Pobranie bazowych wymiarów elementu w jego lokalnym układzie
        item_bounds: QRectF = target_item.boundingRect()
        w = item_bounds.width()
        h = item_bounds.height()
        if w <= 0 or h <= 0:
            return None

        # Reprezentacja geometrii wycentrowana w (0, 0)
        base_box = box(-w / 2.0, -h / 2.0, w / 2.0, h / 2.0)
        center_target = safe_poly.centroid
        cx, cy = center_target.x, center_target.y

        # 3. Kąty do przeszukania
        angles = range(0, 180, int(angle_step_deg)) if allow_rotation else [0.0]

        best_scale = 0.0
        best_angle = 0.0

        # Wymiary bezpiecznego obszaru do ustalenia górnego limitu wyszukiwania
        minx, miny, maxx, maxy = safe_poly.bounds
        max_possible_scale = max(maxx - minx, maxy - miny) / min(w, h) * 1.5

        for angle in angles:
            rotated_box = affinity.rotate(base_box, angle, origin=(0, 0))

            # Wyszukiwanie binarne optymalnej skali dla danego kąta
            low = 0.001
            high = max_possible_scale
            opt_scale_for_angle = 0.0

            for _ in range(16):  # 16 iteracji daje dokładność sub-milimetrową
                mid = (low + high) / 2.0
                test_geom = affinity.scale(rotated_box, xfact=mid, yfact=mid, origin=(0, 0))
                test_geom = affinity.translate(test_geom, xoff=cx, yoff=cy)

                if test_geom.within(safe_poly):
                    opt_scale_for_angle = mid
                    low = mid  # Można spróbować powiększyć
                else:
                    high = mid  # Za duże, trzeba zmniejszyć

            if opt_scale_for_angle > best_scale:
                best_scale = opt_scale_for_angle
                best_angle = angle

        if best_scale <= 0.005:
            return None

        return best_scale, float(best_angle), cx, cy

    @staticmethod
    def apply_fit_transform(
        target_item: QGraphicsItem,
        scale: float,
        angle_deg: float,
        cx: float,
        cy: float
    ):
        """Aplikuje wyznaczone parametry transformacji na instancję QGraphicsItem."""
        r = target_item.boundingRect()
        local_cx = r.center().x()
        local_cy = r.center().y()

        # Konstrukcja macierzy: lokalny środek w (0,0) -> skala -> rotacja
        t = QTransform()
        t.translate(local_cx, local_cy)
        t.scale(scale, scale)
        t.rotate(angle_deg)
        t.translate(-local_cx, -local_cy)

        target_item.setTransform(t)
        # Przesunięcie środka geometrycznego w punkt (cx, cy) sceny
        target_item.setPos(cx - local_cx, cy - local_cy)