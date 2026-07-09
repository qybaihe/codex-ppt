#!/usr/bin/env python3
"""Compile ordered 16:9 slide images into a single PDF."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any


def _debug_enabled() -> bool:
    return os.environ.get("CODEX_PPT_DEBUG", "").strip().lower() not in ("", "0", "false", "no")

try:
    from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required. Install it with: python -m pip install Pillow"
    ) from exc


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def natural_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def parse_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)[xX](\d+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError("Size must look like 1920x1080")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Size values must be positive")
    return width, height


def load_manifest(path: Path) -> list[Path]:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("slides"), list):
        items = data["slides"]
    else:
        raise ValueError("Manifest must be a JSON list or an object with a slides list")

    images: list[Path] = []
    for item in items:
        if isinstance(item, str):
            raw = item
        elif isinstance(item, dict):
            raw = item.get("image") or item.get("path") or item.get("file")
        else:
            raw = None

        if not raw:
            raise ValueError(f"Manifest item lacks an image path: {item!r}")

        candidate = Path(raw).expanduser()
        images.append(candidate if candidate.is_absolute() else base / candidate)

    return images


def collect_images(source: Path) -> list[Path]:
    source = source.expanduser()
    if source.is_dir():
        images = [
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return sorted(images, key=natural_key)

    if source.is_file() and source.suffix.lower() == ".json":
        return load_manifest(source)

    if source.is_file() and source.suffix.lower() in SUPPORTED_EXTENSIONS:
        return [source]

    raise FileNotFoundError(
        f"Input must be a directory, JSON manifest, or image file: {source}"
    )


def aspect_error(size: tuple[int, int], target: tuple[int, int]) -> float:
    width, height = size
    target_width, target_height = target
    return abs((width / height) - (target_width / target_height)) / (
        target_width / target_height
    )


def normalize_image(
    image: Image.Image,
    target_size: tuple[int, int],
    fit: str,
    background: tuple[int, int, int],
) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    target_width, target_height = target_size

    if fit == "stretch":
        return image.resize(target_size, Image.Resampling.LANCZOS)

    source_width, source_height = image.size
    if fit == "cover":
        scale = max(target_width / source_width, target_height / source_height)
        resized = image.resize(
            (round(source_width * scale), round(source_height * scale)),
            Image.Resampling.LANCZOS,
        )
        left = (resized.width - target_width) // 2
        top = (resized.height - target_height) // 2
        return resized.crop((left, top, left + target_width, top + target_height))

    canvas = Image.new("RGB", target_size, background)
    image.thumbnail(target_size, Image.Resampling.LANCZOS)
    left = (target_width - image.width) // 2
    top = (target_height - image.height) // 2
    canvas.paste(image, (left, top))
    return canvas


def build_pdf(args: argparse.Namespace) -> int:
    source = Path(args.input)
    output_pdf = Path(args.output_pdf).expanduser()
    target_size = args.target_size
    background = ImageColor.getrgb(args.background)
    image_paths = collect_images(source)

    if not image_paths:
        raise ValueError(f"No supported images found in {source}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages_dir = Path(args.pages_dir).expanduser() if args.pages_dir else None
    if pages_dir:
        pages_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Image.Image] = []
    report: dict[str, Any] = {
        "input": str(source),
        "output_pdf": str(output_pdf),
        "target_size": f"{target_size[0]}x{target_size[1]}",
        "fit": args.fit,
        "strict": args.strict,
        "slides": [],
    }

    for index, image_path in enumerate(image_paths, start=1):
        image_path = image_path.expanduser()
        if not image_path.exists():
            raise FileNotFoundError(f"Slide image not found: {image_path}")

        try:
            with Image.open(image_path) as image:
                image = ImageOps.exif_transpose(image)
                original_size = image.size
                error = aspect_error(original_size, target_size)
                warnings: list[str] = []

                if error > args.tolerance:
                    warnings.append(
                        f"aspect ratio differs by {error:.2%}; normalized with {args.fit}"
                    )

                if args.strict and error > args.tolerance:
                    raise ValueError(
                        f"{image_path.name} is {original_size[0]}x{original_size[1]}, "
                        f"not within {args.tolerance:.2%} of 16:9"
                    )

                page = normalize_image(image, target_size, args.fit, background)
        except UnidentifiedImageError as exc:
            raise ValueError(f"Cannot read image file: {image_path}") from exc

        if pages_dir:
            normalized_name = f"slide-{index:02d}.png"
            page.save(pages_dir / normalized_name, "PNG")

        pages.append(page)
        report["slides"].append(
            {
                "index": index,
                "source": str(image_path),
                "original_size": f"{original_size[0]}x{original_size[1]}",
                "aspect_error": round(error, 6),
                "warnings": warnings,
            }
        )

    first, rest = pages[0], pages[1:]
    first.save(
        output_pdf,
        "PDF",
        save_all=True,
        append_images=rest,
        resolution=args.dpi,
        quality=95,
    )

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"Wrote {len(pages)} slides to {output_pdf}")
    if args.report:
        print(f"Wrote report to {args.report}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile ordered slide images into a 16:9 PDF."
    )
    parser.add_argument(
        "input",
        help="Directory of slide images, one image file, or JSON manifest.",
    )
    parser.add_argument("output_pdf", help="Output PDF path.")
    parser.add_argument(
        "--target-size",
        type=parse_size,
        default=(1920, 1080),
        help="Normalized slide size, default: 1920x1080.",
    )
    parser.add_argument(
        "--fit",
        choices=("contain", "cover", "stretch"),
        default="contain",
        help="How to normalize non-matching image sizes, default: contain.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when an image is not within the aspect-ratio tolerance.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        help="Allowed relative aspect-ratio difference, default: 0.01.",
    )
    parser.add_argument(
        "--background",
        default="#FFFFFF",
        help="Padding color for --fit contain, default: #FFFFFF.",
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=144.0,
        help="PDF image DPI, default: 144.",
    )
    parser.add_argument(
        "--pages-dir",
        help="Optional folder to write normalized PNG pages.",
    )
    parser.add_argument(
        "--report",
        help="Optional JSON report path.",
    )
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        return build_pdf(args)
    except Exception as exc:
        if _debug_enabled():
            traceback.print_exc()
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
