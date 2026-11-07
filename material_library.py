"""
Baza materiałów LaserBurn Studio.

Przechowywanie: jeden plik JSON (biblioteka standardowa, bez zależności), domyślnie
~/.laserburn_studio/materials.json – ścieżkę można nadpisać zmienną LASERBURN_MATERIALS.
Plik jest czytelny i może być edytowany ręcznie lub kopiowany między komputerami.

Zapis atomowy (plik tymczasowy + podmiana) z kopią poprzedniej wersji (*.bak).
Uszkodzony plik nie jest nadpisywany: odczyt zgłasza błąd, a kopia trafia do *.corrupt-<data>.

Materiał przechowuje parametry dla operacji technologicznych warstw:
  cut   – cięcie (Line): speed, power, passes, kerf
  line  – grawer linii (Line): speed, power, passes
  fill  – wypełnienie (Fill): speed, power, passes, hatch_interval, hatch_angle
  image – raster (Image): speed, power
"""

from __future__ import annotations
import json
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LIBRARY_FORMAT = "LaserBurn_Materials"
LIBRARY_VERSION = 1

OPERATION_MODE = {"cut": "Line", "line": "Line", "fill": "Fill", "image": "Image"}
MODE_OPERATION = {"Line": "line", "Fill": "fill", "Image": "image"}
OPERATION_LABELS = {"cut": "Cięcie", "line": "Linia", "fill": "Fill", "image": "Raster"}
OPERATIONS = tuple(OPERATION_MODE)

# Pola parametrów: (nazwa, typ, min, max, domyślna)
_FIELD_SPECS = {
    "speed": (float, 10.0, 20000.0, 1000.0),
    "power": (float, 0.0, 100.0, 50.0),
    "passes": (int, 1, 50, 1),
    "kerf": (float, 0.0, 2.0, 0.0),
    "hatch_interval": (float, 0.01, 2.0, 0.1),
    "hatch_angle": (float, 0.0, 179.0, 0.0),
}
OPERATION_FIELDS: Dict[str, Tuple[str, ...]] = {
    "cut": ("speed", "power", "passes", "kerf"),
    "line": ("speed", "power", "passes"),
    "fill": ("speed", "power", "passes", "hatch_interval", "hatch_angle"),
    "image": ("speed", "power"),
}


class MaterialLibraryError(Exception):
    """Błąd odczytu/zapisu bazy materiałów."""


def default_library_path() -> Path:
    env = os.environ.get("LASERBURN_MATERIALS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".laserburn_studio" / "materials.json"


def _clean_value(name: str, value: Any) -> Any:
    typ, lo, hi, default = _FIELD_SPECS[name]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    v = min(hi, max(lo, v))
    return int(round(v)) if typ is int else round(v, 4)


def clean_operation(op: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Zostawia tylko pola właściwe dla operacji, z zakresami jak w tabeli warstw."""
    return {f: _clean_value(f, params[f]) for f in OPERATION_FIELDS[op] if f in params}


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "material"


@dataclass
class Material:
    id: str
    name: str
    thickness_mm: float = 0.0
    notes: str = ""
    operations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    updated: str = ""

    @property
    def label(self) -> str:
        t = f" {self.thickness_mm:g} mm" if self.thickness_mm else ""
        return f"{self.name}{t}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "thickness_mm": float(self.thickness_mm),
            "notes": self.notes,
            "operations": {op: dict(self.operations[op]) for op in OPERATIONS if op in self.operations},
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Material":
        name = str(d.get("name") or "Bez nazwy").strip()
        try:
            thickness = max(0.0, float(d.get("thickness_mm", 0.0)))
        except (TypeError, ValueError):
            thickness = 0.0
        ops = {}
        for op, params in (d.get("operations") or {}).items():
            if op in OPERATION_FIELDS and isinstance(params, dict):
                cleaned = clean_operation(op, params)
                if cleaned:
                    ops[op] = cleaned
        mid = str(d.get("id") or slugify(f"{name} {thickness:g}mm"))
        return cls(id=mid, name=name, thickness_mm=thickness, notes=str(d.get("notes", "")),
                   operations=ops, updated=str(d.get("updated", "")))

    def copy(self) -> "Material":
        return Material.from_dict(self.to_dict())


class MaterialLibrary:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else default_library_path()
        self.materials: Dict[str, Material] = {}

    # ------------------------------------------------------------------ pliki
    def load(self) -> "MaterialLibrary":
        self.materials = {}
        if not self.path.exists():
            return self
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("format") != LIBRARY_FORMAT:
                raise ValueError("to nie jest plik bazy materiałów LaserBurn")
            if int(data.get("version", 1)) > LIBRARY_VERSION:
                raise ValueError(f"plik z nowszej wersji programu (v{data.get('version')})")
            for entry in data.get("materials") or []:
                if isinstance(entry, dict):
                    m = Material.from_dict(entry)
                    m.id = self._unique_id(m.id)
                    self.materials[m.id] = m
        except (OSError, ValueError, json.JSONDecodeError) as e:
            backup = self._preserve_corrupt()
            note = f" Kopia: {backup.name}" if backup else ""
            self.materials = {}
            raise MaterialLibraryError(f"Nie można odczytać bazy materiałów {self.path}: {e}.{note}") from e
        return self

    def _preserve_corrupt(self) -> Optional[Path]:
        try:
            target = self.path.with_name(f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(self.path, target)
            return target
        except OSError:
            return None

    def save(self) -> None:
        data = {
            "format": LIBRARY_FORMAT,
            "version": LIBRARY_VERSION,
            "materials": [m.to_dict() for m in self.list()],
        }
        text = json.dumps(data, indent=2, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            fd, tmp = tempfile.mkstemp(prefix=".materials_", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise MaterialLibraryError(f"Nie można zapisać bazy materiałów {self.path}: {e}") from e

    # ------------------------------------------------------------------ CRUD
    def list(self) -> List[Material]:
        return sorted(self.materials.values(), key=lambda m: (m.name.lower(), m.thickness_mm))

    def get(self, material_id: Optional[str]) -> Optional[Material]:
        return self.materials.get(material_id) if material_id else None

    def _unique_id(self, base: str, exclude: Optional[str] = None) -> str:
        candidate, n = base, 2
        while candidate in self.materials and candidate != exclude:
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def add(self, material: Material) -> Material:
        material = material.copy()
        material.id = self._unique_id(slugify(f"{material.name} {material.thickness_mm:g}mm"))
        material.updated = time.strftime("%Y-%m-%d")
        self.materials[material.id] = material
        return material

    def update(self, material: Material) -> Material:
        if material.id not in self.materials:
            return self.add(material)
        stored = material.copy()
        stored.updated = time.strftime("%Y-%m-%d")
        self.materials[stored.id] = stored
        return stored

    def remove(self, material_id: str) -> None:
        self.materials.pop(material_id, None)


# ==============================================================================
# POWIĄZANIE Z WARSTWAMI TECHNOLOGICZNYMI
# ==============================================================================
def apply_to_layers(material: Material, layers: Iterable[Any]) -> List[Tuple[Any, str]]:
    """
    Ustawia parametry warstw wg ich operacji (cut/line/fill/image).
    Warstwy, dla których materiał nie definiuje operacji, pozostają bez zmian.
    Zwraca listę (warstwa, operacja) zmienionych warstw.
    """
    changed = []
    for layer in layers:
        op = getattr(layer, "operation", None) or MODE_OPERATION.get(getattr(layer, "mode", ""), "line")
        params = material.operations.get(op)
        if not params:
            continue
        for name, value in params.items():
            setattr(layer, name, value)
        changed.append((layer, op))
    return changed


def operations_from_layers(layers: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Parametry z warstw (pierwsza warstwa danej operacji; najpierw warstwy z wyjściem)."""
    ordered = sorted(layers, key=lambda l: not getattr(l, "output", True))
    ops: Dict[str, Dict[str, Any]] = {}
    for layer in ordered:
        op = getattr(layer, "operation", None) or MODE_OPERATION.get(getattr(layer, "mode", ""), "line")
        if op in ops or op not in OPERATION_FIELDS:
            continue
        ops[op] = clean_operation(op, {f: getattr(layer, f) for f in OPERATION_FIELDS[op] if hasattr(layer, f)})
    return ops
