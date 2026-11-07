"""
Test uruchomienia wersji spakowanej (LaserBurnStudio.exe --smoke-test), wywoływany przez build.py.
Ćwiczy wszystkie biblioteki, które PyInstaller musi poprawnie dołączyć: shapely (fill), svgelements
(import SVG), numpy + Pillow (raster), Qt (scena, zapis/odczyt projektu). Kod wyjścia 0 = OK.
"""
from __future__ import annotations

import os
import re
import tempfile
import traceback

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="30mm" viewBox="0 0 50 30">
<rect x="5" y="5" width="20" height="10" fill="none" stroke="#ff0000"/>
<circle cx="35" cy="15" r="8" fill="#0000ff"/></svg>"""


def _same_gcode(a, b, tol: float = 0.0015) -> bool:
    """Równość G-code z tolerancją zaokrągleń współrzędnych (projekt zapisuje liczby w skróconej postaci)."""
    if len(a) != len(b):
        return False
    num = re.compile(r"([A-Z])(-?\d+(?:\.\d+)?)")
    for la, lb in zip(a, b):
        if la == lb:
            continue
        ta, tb = num.findall(la), num.findall(lb)
        if num.sub("", la) != num.sub("", lb) or [k for k, _ in ta] != [k for k, _ in tb]:
            return False
        if any(abs(float(x) - float(y)) > tol for (_, x), (_, y) in zip(ta, tb)):
            return False
    return True


def run(window) -> int:
    from PIL import Image
    import numpy as np
    from shapes import ShapeSpec
    from svg_engine import SvgDocumentImporter
    from raster_engine import RasterImageProcessor
    from project_manager import ProjectSerializer
    from PySide6.QtCore import QPointF

    log = []
    tmp = tempfile.mkdtemp(prefix="lb_smoke_")
    try:
        fill = next((i for i, l in enumerate(window.layers) if l.operation == "fill"), 0)
        cut = next((i for i, l in enumerate(window.layers) if l.operation == "cut"), 0)
        window.add_shape(ShapeSpec(kind="star", width=30, height=30), (60, 60), fill)
        window.add_shape(ShapeSpec(kind="heart", width=30, height=27), (120, 60), cut)

        svg_path = os.path.join(tmp, "t.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(_SVG)
        items = SvgDocumentImporter().load_svg(svg_path, keep_document_position=True)
        assert len(items) == 2, f"SVG: {len(items)} obiektów"
        for it in items:
            window.scene.addItem(it)

        png = os.path.join(tmp, "t.png")
        Image.fromarray((np.indices((40, 40)).sum(axis=0) % 2 * 255).astype("uint8")).save(png)
        item, _, grid = RasterImageProcessor(default_interval_mm=0.1).load_raster(
            png, initial_pos=QPointF(150, 150), apply_dither=True, fit_within_mm=(300, 300))
        window.scene.addItem(item)

        gcode = window._generate_gcode()
        assert sum(1 for l in gcode if l.startswith("G1")) > 100, "za mało ruchów roboczych"
        proj = os.path.join(tmp, "t.lcam")
        n_items = len(window._user_items())
        ProjectSerializer.save_to_file(window, proj)
        # Regresja: zapis nie może usuwać obiektów ze sceny (PySide6 parentItem() → utrata własności)
        assert len(window._user_items()) == n_items, "zapis projektu usunął obiekty ze sceny"
        ProjectSerializer.load_from_file(window, proj)
        ProjectSerializer.save_to_file(window, proj)
        assert len(window._user_items()) == n_items, "ponowny zapis wczytanego projektu usunął obiekty"
        assert _same_gcode(window._generate_gcode(), gcode), "G-code po odczycie projektu różni się"
        log.append(f"OK: {len(gcode)} linii G-code")
        return 0
    except Exception:
        log.append(traceback.format_exc())
        return 1
    finally:
        out = os.environ.get("LASERBURN_SMOKE_LOG")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
