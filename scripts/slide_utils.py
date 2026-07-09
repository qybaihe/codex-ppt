"""Shared helpers for the Codex PPT slide scripts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


def natural_key(path: Path) -> list[Any]:
    """Sort key that orders paths by embedded numbers (slide-2 before slide-10)."""
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def load_json(path: Path) -> Any:
    """Read and parse a UTF-8 JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def run_main(func: Callable[[], int]) -> int:
    """Run a CLI entry point, reporting any error on stderr with exit code 1."""
    try:
        return func()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
