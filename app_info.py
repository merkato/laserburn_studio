"""Nazwa, wersja i ścieżki programu – wspólne dla aplikacji, instalatora i dokumentacji."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LaserBurn Studio"
APP_VERSION = "1.0.0"
APP_PUBLISHER = "Tomasz Nycz"


def resource_path(relative: str) -> Path:
    """Plik dołączony do programu – działa ze źródeł i z wersji spakowanej PyInstallerem."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def user_data_dir() -> Path:
    """Katalog danych użytkownika (baza materiałów, log błędów)."""
    return Path(os.path.expanduser("~")) / ".laserburn_studio"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
