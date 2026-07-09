"""Unit tests for scripts/compile_slide_images_to_pdf.py."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from PIL import Image

import compile_slide_images_to_pdf as mod


def make_image(path: Path, size: tuple[int, int] = (192, 108), color=(10, 20, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


# ---------------------------------------------------------------------------
# natural_key
# ---------------------------------------------------------------------------


def test_natural_key_orders_numerically_not_lexically():
    names = ["slide-10.png", "slide-2.png", "slide-1.png"]
    paths = [Path(n) for n in names]
    ordered = sorted(paths, key=mod.natural_key)
    assert [p.name for p in ordered] == ["slide-1.png", "slide-2.png", "slide-10.png"]


def test_natural_key_is_case_insensitive():
    assert mod.natural_key(Path("Slide-1.PNG")) == mod.natural_key(Path("slide-1.png"))


# ---------------------------------------------------------------------------
# parse_size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("1920x1080", (1920, 1080)), ("960X540", (960, 540)), (" 100x200 ", (100, 200))],
)
def test_parse_size_valid(value, expected):
    assert mod.parse_size(value) == expected


@pytest.mark.parametrize("value", ["1920", "1920*1080", "axb", "1920x", ""])
def test_parse_size_malformed_raises(value):
    with pytest.raises(argparse.ArgumentTypeError):
        mod.parse_size(value)


@pytest.mark.parametrize("value", ["0x100", "100x0"])
def test_parse_size_nonpositive_raises(value):
    with pytest.raises(argparse.ArgumentTypeError):
        mod.parse_size(value)


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------


def test_load_manifest_list_of_strings(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(["a.png", "sub/b.png"]), encoding="utf-8")
    result = mod.load_manifest(manifest)
    assert result == [tmp_path / "a.png", tmp_path / "sub" / "b.png"]


def test_load_manifest_slides_object_with_dicts(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"slides": [{"image": "a.png"}, {"path": "b.png"}, {"file": "c.png"}]}),
        encoding="utf-8",
    )
    result = mod.load_manifest(manifest)
    assert [p.name for p in result] == ["a.png", "b.png", "c.png"]


def test_load_manifest_absolute_path_preserved(tmp_path):
    absolute = (tmp_path / "abs.png").resolve()
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([str(absolute)]), encoding="utf-8")
    assert mod.load_manifest(manifest) == [absolute]


def test_load_manifest_bad_top_level_type_raises(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"not_slides": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest must be"):
        mod.load_manifest(manifest)


def test_load_manifest_item_without_path_raises(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([{"caption": "no image key"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks an image path"):
        mod.load_manifest(manifest)


# ---------------------------------------------------------------------------
# collect_images
# ---------------------------------------------------------------------------


def test_collect_images_directory_sorted_and_filtered(tmp_path):
    make_image(tmp_path / "slide-2.png")
    make_image(tmp_path / "slide-10.png")
    make_image(tmp_path / "slide-1.png")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    result = mod.collect_images(tmp_path)
    assert [p.name for p in result] == ["slide-1.png", "slide-2.png", "slide-10.png"]


def test_collect_images_single_image_file(tmp_path):
    img = make_image(tmp_path / "only.png")
    assert mod.collect_images(img) == [img]


def test_collect_images_json_manifest(tmp_path):
    make_image(tmp_path / "a.png")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(["a.png"]), encoding="utf-8")
    assert mod.collect_images(manifest) == [tmp_path / "a.png"]


def test_collect_images_unsupported_target_raises(tmp_path):
    bad = tmp_path / "file.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        mod.collect_images(bad)


def test_collect_images_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod.collect_images(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# aspect_error
# ---------------------------------------------------------------------------


def test_aspect_error_zero_for_matching_ratio():
    assert mod.aspect_error((1920, 1080), (1920, 1080)) == pytest.approx(0.0)
    assert mod.aspect_error((640, 360), (1920, 1080)) == pytest.approx(0.0)


def test_aspect_error_positive_for_square():
    assert mod.aspect_error((1000, 1000), (1920, 1080)) > 0.4


# ---------------------------------------------------------------------------
# normalize_image
# ---------------------------------------------------------------------------


def test_normalize_image_stretch():
    src = Image.new("RGB", (100, 100), (5, 5, 5))
    out = mod.normalize_image(src, (200, 100), "stretch", (0, 0, 0))
    assert out.size == (200, 100)


def test_normalize_image_cover_crops_to_target():
    src = Image.new("RGB", (100, 100), (5, 5, 5))
    out = mod.normalize_image(src, (200, 100), "cover", (0, 0, 0))
    assert out.size == (200, 100)


def test_normalize_image_contain_pads_with_background():
    src = Image.new("RGB", (100, 100), (5, 5, 5))
    background = (255, 0, 0)
    out = mod.normalize_image(src, (200, 100), "contain", background)
    assert out.size == (200, 100)
    # Corners fall outside the centered thumbnail, so they show the padding color.
    assert out.getpixel((0, 0)) == background
    assert out.getpixel((199, 0)) == background


# ---------------------------------------------------------------------------
# make_parser / argument parsing
# ---------------------------------------------------------------------------


def test_make_parser_defaults():
    parser = mod.make_parser()
    args = parser.parse_args(["in_dir", "out.pdf"])
    assert args.target_size == (1920, 1080)
    assert args.fit == "contain"
    assert args.strict is False
    assert args.tolerance == 0.01
    assert args.background == "#FFFFFF"


def test_make_parser_custom_values():
    parser = mod.make_parser()
    args = parser.parse_args(
        ["in", "out.pdf", "--target-size", "960x540", "--fit", "cover", "--strict"]
    )
    assert args.target_size == (960, 540)
    assert args.fit == "cover"
    assert args.strict is True


# ---------------------------------------------------------------------------
# build_pdf (integration through the public helper)
# ---------------------------------------------------------------------------


def _args(**overrides):
    base = dict(
        input="",
        output_pdf="",
        target_size=(192, 108),
        fit="contain",
        strict=False,
        tolerance=0.01,
        background="#FFFFFF",
        dpi=72.0,
        pages_dir=None,
        report=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_pdf_writes_pdf_pages_and_report(tmp_path):
    src = tmp_path / "slides"
    make_image(src / "slide-1.png")
    make_image(src / "slide-2.png")
    out_pdf = tmp_path / "out" / "deck.pdf"
    pages_dir = tmp_path / "pages"
    report = tmp_path / "report.json"

    rc = mod.build_pdf(
        _args(
            input=str(src),
            output_pdf=str(out_pdf),
            pages_dir=str(pages_dir),
            report=str(report),
        )
    )

    assert rc == 0
    assert out_pdf.is_file() and out_pdf.stat().st_size > 0
    assert (pages_dir / "slide-01.png").is_file()
    assert (pages_dir / "slide-02.png").is_file()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert len(data["slides"]) == 2
    assert data["slides"][0]["index"] == 1


def test_build_pdf_no_images_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="No supported images"):
        mod.build_pdf(_args(input=str(empty), output_pdf=str(tmp_path / "o.pdf")))


def test_build_pdf_strict_rejects_wrong_aspect(tmp_path):
    src = tmp_path / "slides"
    make_image(src / "square.png", size=(500, 500))
    with pytest.raises(ValueError, match="not within"):
        mod.build_pdf(
            _args(input=str(src), output_pdf=str(tmp_path / "o.pdf"), strict=True)
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_returns_error_code_on_failure(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["prog", str(Path("nope")), "out.pdf"])
    rc = mod.main()
    assert rc == 1
    assert "error:" in capsys.readouterr().err
