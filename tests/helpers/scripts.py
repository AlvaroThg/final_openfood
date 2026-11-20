"""Carga los scripts de ml/scripts como módulos (no son un paquete importable)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "ml" / "scripts"


def load_script(name: str) -> ModuleType:
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scripts_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
