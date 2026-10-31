import math
from typing import List, Tuple, Dict
import numpy as np
from shapely.geometry import Polygon, LineString, Point, MultiLineString
from shapely.ops import linemerge


class PathLoop:
    """Reprezentacja pojedynczej ścieżki lub zamkniętej pętli cięcia."""
    def __init__(self, loop_id: int, coords: List[Tuple[float, float]], is_closed: bool = True):
        self.loop_id = loop_id
        self.coords = coords
        self.is_closed = is_closed
        self.children: List[int] = []  # Pętle bezpośrednio zawarte wewnątrz tej pętli
        self.depth: int = 0            # Głębokość zagnieżdżenia w drzewie (im większa, tym głębszy otwór)

        # Geometria Shapely do testów przestrzennych
        if self.is_closed and len(coords) >= 3:
            pts = list(coords)
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            poly = Polygon(pts)
            self.geom = poly if poly.is_valid else poly.buffer(0)
        else:
            self.geom = LineString(coords)


class PathOptimizer:
    """
    Optymalizator kolejności cięcia:
    - Scalanie sąsiadujących mikrosegmentów w ciągłe polilinie (linemerge).
    - Sortowanie topologiczne: od najgłębszych otworów wewnętrznych do konturów zewnętrznych (Inside-Out).
    - Heurystyka TSP (Nearest Neighbor): minimalizacja dystansu jałowego G0.
    - Optymalizacja punktu wkłucia: wybór wierzchołka pętli najbliższego aktualnej pozycji głowicy.
    """

    @staticmethod
    def _dist_sq(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2

    @classmethod
    def build_containment_tree(cls, loops: List[PathLoop]) -> None:
        """
        Buduje relacje rodzic-dziecko i ustala głębokość zagnieżdżenia (depth).
        Zewnętrzny kontur = depth 0, otwory wewnątrz = depth 1, wyspy w otworach = depth 2 itd.
        """
        n = len(loops)
        parent_map: Dict[int, int] = {}

        indices_by_area = sorted(
            range(n),
            key=lambda i: loops[i].geom.area if loops[i].is_closed else 0.0
        )

        for i_pos, i in enumerate(indices_by_area):
            child = loops[i]
            if not child.is_closed:
                continue

            for j in indices_by_area[i_pos + 1:]:
                parent = loops[j]
                if parent.is_closed and parent.geom.contains(child.geom):
                    parent_map[child.loop_id] = parent.loop_id
                    parent.children.append(child.loop_id)
                    break

        for loop in loops:
            d = 0
            curr_id = loop.loop_id
            while curr_id in parent_map:
                d += 1
                curr_id = parent_map[curr_id]
            loop.depth = d

    @classmethod
    def reorder_loop_start(
        cls, 
        coords: List[Tuple[float, float]], 
        current_pos: Tuple[float, float], 
        is_closed: bool
    ) -> List[Tuple[float, float]]:
        """
        Wybiera optymalny punkt startowy cięcia (punkt wkłucia).
        Dla pętli zamkniętych: przesuwa początek pętli do wierzchołka najbliższego głowicy.
        Dla linii otwartych: decyduje, czy zacząć od początku, czy od końca (odwrócenie).
        """
        if not coords:
            return coords

        if not is_closed:
            d_start = cls._dist_sq(current_pos, coords[0])
            d_end = cls._dist_sq(current_pos, coords[-1])
            return coords if d_start <= d_end else coords[::-1]

        pts = list(coords)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()

        best_idx = 0
        min_d = float('inf')
        for i, pt in enumerate(pts):
            d = cls._dist_sq(current_pos, pt)
            if d < min_d:
                min_d = d
                best_idx = i

        # Przesunięcie cykliczne tak, aby punkt najbliższy był na indeksie 0
        reordered = pts[best_idx:] + pts[:best_idx]
        reordered.append(reordered[0])  # Zamknięcie pętli
        return reordered

    @classmethod
    def optimize(
        cls, 
        input_paths: List[LineString], 
        start_head_pos: Tuple[float, float] = (0.0, 0.0)
    ) -> List[List[Tuple[float, float]]]:
        """
        Główna funkcja wykonawcza:
        1. Scalanie mikrosegmentów przez linemerge.
        2. Podział na pętle zamknięte i otwarte.
        3. Budowa drzewa zagnieżdżeń (Inside-Out).
        4. Sortowanie wg głębokości (depth malejąco -> najpierw otwory).
        5. Heurystyka TSP w ramach tej samej głębokości z rotacją punktu startu.
        """
        if not input_paths:
            return []

        # 1. Scalanie przylegających segmentów ze starych wektorów
        merged = linemerge(input_paths)
        raw_geoms = []
        if isinstance(merged, LineString):
            raw_geoms = [merged]
        elif isinstance(merged, MultiLineString):
            raw_geoms = list(merged.geoms)
        else:
            raw_geoms = input_paths

        # 2. Tworzenie obiektów PathLoop
        loops: List[PathLoop] = []
        for idx, g in enumerate(raw_geoms):
            c = list(g.coords)
            if len(c) < 2:
                continue
            is_closed = (c[0] == c[-1]) and len(c) >= 4
            loops.append(PathLoop(idx, c, is_closed=is_closed))

        if not loops:
            return []

        # 3. Wyznaczenie głębokości (Inside-Out)
        cls.build_containment_tree(loops)

        # 4. Grupowanie według poziomów głębokości
        depth_groups: Dict[int, List[PathLoop]] = {}
        for l in loops:
            depth_groups.setdefault(l.depth, []).append(l)

        # Kolejność: od największej głębokości (najgłębsze otwory) do poziomu 0 (obrys zewnętrzny)
        sorted_depths = sorted(depth_groups.keys(), reverse=True)

        final_paths: List[List[Tuple[float, float]]] = []
        curr_pos = start_head_pos

        # 5. Wybór kolejności w ramach każdej głębokości (Nearest Neighbor)
        for d in sorted_depths:
            remaining = depth_groups[d]

            while remaining:
                best_idx = 0
                best_dist = float('inf')

                for i, cand in enumerate(remaining):
                    # Szacujemy odległość od aktualnej głowicy do najbliższego punktu pętli
                    for pt in cand.coords:
                        dist = cls._dist_sq(curr_pos, pt)
                        if dist < best_dist:
                            best_dist = dist
                            best_idx = i

                chosen_loop = remaining.pop(best_idx)
                
                # Zoptymalizuj punkt startu pętli względem głowicy
                opt_coords = cls.reorder_loop_start(chosen_loop.coords, curr_pos, chosen_loop.is_closed)
                final_paths.append(opt_coords)
                curr_pos = opt_coords[-1]

        return final_paths