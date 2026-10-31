from typing import List, Tuple
from shapely.geometry import Polygon, MultiPolygon, LinearRing
from shapely.validation import make_valid
from path_optimizer import PathLoop, PathOptimizer


class KerfCompensator:
    """
    Kompensator szerokości rzazu wiązki lasera (Kerf Offset).
    - Kontury zewnętrzne (parzysta głębokość lub depth=0 w zależności od hierarchii wysp) -> bufor +offset.
    - Otwory wewnętrzne (nieparzysta głębokość) -> bufor -offset.
    - Używa stylu narożników 'mitre' (z limitem docięcia) lub 'round', by zachować ostrość krawędzi.
    """

    @staticmethod
    def _extract_ring_coords(ring: LinearRing) -> List[Tuple[float, float]]:
        coords = list(ring.coords)
        # Zapewnienie domknięcia pętli
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])
        return [(round(x, 4), round(y, 4)) for x, y in coords]

    @classmethod
    def apply_kerf_to_polygon(
        cls, 
        poly: Polygon, 
        is_hole: bool, 
        kerf_width: float,
        join_style: str = "mitre",
        mitre_limit: float = 3.0
    ) -> List[List[Tuple[float, float]]]:
        """
        Aplikuje offset w zależności od tego, czy geometria jest zewnętrznym konturem, czy otworem.
        Offset = kerf_width / 2.0.
        Dla zewnętrznego: bufor dodatni (+).
        Dla otworu: bufor ujemny (-).
        """
        offset = abs(kerf_width) / 2.0
        if offset < 1e-4:
            return [cls._extract_ring_coords(poly.exterior)]

        # Znak przesunięcia: Zewnętrzny -> powiększ, Otwór -> pomniejsz
        signed_dist = -offset if is_hole else offset

        # Parametry łączenia narożników: 'mitre' zachowuje kąty proste i ostre
        buffered = poly.buffer(
            distance=signed_dist,
            join_style=join_style,
            mitre_limit=mitre_limit
        )

        if buffered.is_empty:
            # Jeśli otwór był mniejszy niż średnica plamki lasera, bufor ujemny go wygasza
            return []

        if not buffered.is_valid:
            buffered = make_valid(buffered)

        result_paths = []
        if isinstance(buffered, Polygon):
            result_paths.append(cls._extract_ring_coords(buffered.exterior))
        elif isinstance(buffered, MultiPolygon):
            for sub_poly in buffered.geoms:
                if not sub_poly.is_empty:
                    result_paths.append(cls._extract_ring_coords(sub_poly.exterior))

        return result_paths

    @classmethod
    def compensate_paths(
        cls, 
        raw_paths: List[List[Tuple[float, float]]], 
        kerf_width_mm: float
    ) -> List[List[Tuple[float, float]]]:
        """
        Przetwarza całą warstwę ścieżek:
        1. Rozpoznaje hierarchię containment tree (rodzic / dziecko).
        2. Ustala, które pętle są otworami (depth % 2 == 1), a które wyspami/zewnętrzem.
        3. Aplikuje kompensację rzazu dla pętli zamkniętych.
        4. Zwraca skompensowaną listę ścieżek gotowych do optymalizacji TSP.
        """
        if abs(kerf_width_mm) < 1e-4:
            return raw_paths

        # Budowa pętli i wykrycie zagnieżdżeń
        loops: List[PathLoop] = []
        for idx, path in enumerate(raw_paths):
            if len(path) < 2:
                continue
            is_closed = (
                (path[0][0] - path[-1][0]) ** 2 + (path[0][1] - path[-1][1]) ** 2 < 0.01 
                and len(path) >= 3
            )
            loops.append(PathLoop(idx, path, is_closed=is_closed))

        if not loops:
            return []

        # Analiza drzewa zagnieżdżenia
        PathOptimizer.build_containment_tree(loops)

        compensated_paths: List[List[Tuple[float, float]]] = []

        for loop in loops:
            # Ścieżki otwarte (linie grawerowane) zostają bez zmian
            if not loop.is_closed:
                compensated_paths.append(loop.coords)
                continue

            # Reguła parzystości głębokości (Even-Odd rule dla topologii CAD):
            # depth 0 = kontur zewnętrzny detalu (Outer) -> +kerf/2
            # depth 1 = otwór w detalu (Hole)            -> -kerf/2
            # depth 2 = wyspa wewnątrz otworu (Outer)    -> +kerf/2
            # depth 3 = otwór w wyspie (Hole)            -> -kerf/2
            is_hole = (loop.depth % 2 == 1)

            poly = loop.geom
            if isinstance(poly, Polygon):
                offset_rings = cls.apply_kerf_to_polygon(poly, is_hole=is_hole, kerf_width=kerf_width_mm)
                compensated_paths.extend(offset_rings)

        return compensated_paths