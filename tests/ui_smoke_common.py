# -----------------------------------------------------------------------------
# Role: Delegates block-local smoke tests to the repository shared helpers.
# File Name: ui_smoke_common.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-06-05
# -----------------------------------------------------------------------------

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_root_helpers():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tests" / "ui_smoke_common.py"
        if candidate.is_file() and candidate != Path(__file__).resolve():
            spec = importlib.util.spec_from_file_location("_bloxsmith_root_ui_smoke_common", candidate)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("Unable to locate root tests/ui_smoke_common.py")


_helpers = _load_root_helpers()

for _name in dir(_helpers):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_helpers, _name)
