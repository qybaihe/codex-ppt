#!/usr/bin/env python3
"""Merge per-slide editable reconstruction outputs into one builder input."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any


def _debug_enabled() -> bool:
    return os.environ.get("CODEX_PPT_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def natural_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def slide_number(path: Path, fallback: int) -> int:
    match = re.search(r"(\d+)", path.name)
    return int(match.group(1)) if match else fallback


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_text_layer(slide_dir: Path) -> Path:
    candidates = [
        slide_dir / "text-layer.json",
        slide_dir / "editable" / "text-layer.json",
        slide_dir / "output" / "text-layer.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    matches = sorted(slide_dir.glob("**/text-layer.json"), key=natural_key)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No text-layer.json found under {slide_dir}")


def extract_texts(text_layer: Path, slide_no: int) -> list[dict[str, Any]]:
    data = load_json(text_layer)
    if isinstance(data.get("texts"), list):
        return data["texts"]

    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError(f"Text layer must contain texts or slides: {text_layer}")

    for slide in slides:
        if int(slide.get("slide", slide_no)) == slide_no and isinstance(slide.get("texts"), list):
            return slide["texts"]

    first = slides[0]
    if isinstance(first.get("texts"), list):
        return first["texts"]
    raise ValueError(f"No texts array found in {text_layer}")


def find_page_dir(slide_dir: Path, slide_no: int) -> Path:
    candidates = [
        slide_dir / "visual-layers" / f"slide-{slide_no:02d}",
        slide_dir / "visual-layers" / f"{slide_no:02d}",
        slide_dir / "visual-layers" / "slide-01",
        slide_dir / "visual-layers",
        slide_dir / "layers" / f"slide-{slide_no:02d}",
        slide_dir / "layers" / "slide-01",
    ]
    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate

    roots = [slide_dir / "visual-layers", slide_dir / "layers", slide_dir]
    for root in roots:
        if not root.is_dir():
            continue
        matches = sorted(
            [path.parent for path in root.glob("**/manifest.json")],
            key=natural_key,
        )
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No visual layer manifest found under {slide_dir}")


def collect_slide_dirs(slides_root: Path, pattern: str) -> list[Path]:
    if not slides_root.is_dir():
        raise NotADirectoryError(f"Slides root is not a directory: {slides_root}")
    dirs = sorted(
        [path for path in slides_root.glob(pattern) if path.is_dir()],
        key=natural_key,
    )
    if not dirs:
        dirs = sorted([path for path in slides_root.iterdir() if path.is_dir()], key=natural_key)
    if not dirs:
        raise ValueError(f"No slide directories found under {slides_root}")
    return dirs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge one editable reconstruction folder per slide into a consolidated visual-layers root and text-layer.json."
    )
    parser.add_argument("slides_root", help="Folder containing per-slide reconstruction folders.")
    parser.add_argument("--slide-glob", default="slide-*", help="Glob for slide folders, default: slide-*.")
    parser.add_argument("--out-layers-root", required=True, help="Output visual-layers folder.")
    parser.add_argument("--out-text-json", required=True, help="Output consolidated text-layer.json.")
    parser.add_argument("--background", default="#FFFFFF", help="Deck background color metadata.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output page folders.")
    args = parser.parse_args()

    slides_root = Path(args.slides_root).expanduser()
    out_layers_root = Path(args.out_layers_root).expanduser()
    out_text_json = Path(args.out_text_json).expanduser()
    slide_dirs = collect_slide_dirs(slides_root, args.slide_glob)

    out_layers_root.mkdir(parents=True, exist_ok=True)
    out_text_json.parent.mkdir(parents=True, exist_ok=True)

    slides: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for index, slide_dir in enumerate(slide_dirs, start=1):
        number = slide_number(slide_dir, index)
        page_name = f"slide-{number:02d}"
        text_layer = find_text_layer(slide_dir)
        page_dir = find_page_dir(slide_dir, number)
        dest = out_layers_root / page_name
        if dest.exists():
            if not args.force:
                raise FileExistsError(f"Output page folder already exists: {dest}")
            shutil.rmtree(dest)
        shutil.copytree(page_dir, dest)
        texts = extract_texts(text_layer, number)
        slides.append({"slide": number, "texts": texts})
        report.append(
            {
                "slide": number,
                "source_dir": str(slide_dir),
                "visual_layers": str(dest),
                "text_layer": str(text_layer),
                "text_count": len(texts),
            }
        )

    deck = {"background": args.background, "slides": slides}
    out_text_json.write_text(json.dumps(deck, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"slides": report, "text_json": str(out_text_json), "layers_root": str(out_layers_root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        if _debug_enabled():
            traceback.print_exc()
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
