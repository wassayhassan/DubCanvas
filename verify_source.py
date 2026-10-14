#!/usr/bin/env python3
"""Fast syntax/static preflight using only the Python standard library."""
from __future__ import annotations

import ast
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = [ROOT / "anime_dubber", ROOT / "dubcanvas", ROOT / "tests", ROOT / "tools"]
errors = []
count = 0

for base in TARGETS:
    for path in sorted(base.rglob("*.py")):
        count += 1
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(path))
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")

if errors:
    print("SOURCE PREFLIGHT FAILED", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    raise SystemExit(1)

print(f"Source preflight: OK ({count} Python files parsed and byte-compiled)")
