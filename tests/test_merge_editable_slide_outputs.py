"""Unit tests for scripts/merge_editable_slide_outputs.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import merge_editable_slide_outputs as mod


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# natural_key / slide_number
# ---------------------------------------------------------------------------


def test_natural_key_sorts_numerically():
    paths = [Path("slide-10"), Path("slide-2"), Path("slide-1")]
    assert [p.name for p in sorted(paths, key=mod.natural_key)] == [
        "slide-1",
        "slide-2",
        "slide-10",
    ]


def test_slide_number_extracts_first_integer():
    assert mod.slide_number(Path("slide-07"), fallback=99) == 7


def test_slide_number_uses_fallback_when_no_digits():
    assert mod.slide_number(Path("cover"), fallback=3) == 3


# ---------------------------------------------------------------------------
# find_text_layer
# ---------------------------------------------------------------------------


def test_find_text_layer_prefers_top_level(tmp_path):
    top = write_json(tmp_path / "text-layer.json", {"texts": []})
    write_json(tmp_path / "editable" / "text-layer.json", {"texts": []})
    assert mod.find_text_layer(tmp_path) == top


def test_find_text_layer_editable_subdir(tmp_path):
    editable = write_json(tmp_path / "editable" / "text-layer.json", {"texts": []})
    assert mod.find_text_layer(tmp_path) == editable


def test_find_text_layer_glob_fallback(tmp_path):
    nested = write_json(tmp_path / "deep" / "nest" / "text-layer.json", {"texts": []})
    assert mod.find_text_layer(tmp_path) == nested


def test_find_text_layer_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod.find_text_layer(tmp_path)


# ---------------------------------------------------------------------------
# extract_texts
# ---------------------------------------------------------------------------


def test_extract_texts_top_level_texts(tmp_path):
    layer = write_json(tmp_path / "t.json", {"texts": [{"text": "hi"}]})
    assert mod.extract_texts(layer, slide_no=1) == [{"text": "hi"}]


def test_extract_texts_matches_slide_number(tmp_path):
    layer = write_json(
        tmp_path / "t.json",
        {"slides": [{"slide": 1, "texts": [{"text": "a"}]}, {"slide": 2, "texts": [{"text": "b"}]}]},
    )
    assert mod.extract_texts(layer, slide_no=2) == [{"text": "b"}]


def test_extract_texts_falls_back_to_first_slide(tmp_path):
    layer = write_json(
        tmp_path / "t.json",
        {"slides": [{"slide": 5, "texts": [{"text": "only"}]}]},
    )
    assert mod.extract_texts(layer, slide_no=1) == [{"text": "only"}]


def test_extract_texts_no_texts_or_slides_raises(tmp_path):
    layer = write_json(tmp_path / "t.json", {"background": "#fff"})
    with pytest.raises(ValueError, match="must contain texts or slides"):
        mod.extract_texts(layer, slide_no=1)


def test_extract_texts_first_slide_without_texts_raises(tmp_path):
    layer = write_json(tmp_path / "t.json", {"slides": [{"slide": 9}]})
    with pytest.raises(ValueError, match="No texts array"):
        mod.extract_texts(layer, slide_no=1)


# ---------------------------------------------------------------------------
# find_page_dir
# ---------------------------------------------------------------------------


def test_find_page_dir_preferred_candidate(tmp_path):
    page = tmp_path / "visual-layers" / "slide-03"
    write_json(page / "manifest.json", {"assets": []})
    assert mod.find_page_dir(tmp_path, slide_no=3) == page


def test_find_page_dir_glob_fallback(tmp_path):
    page = tmp_path / "layers" / "weird-name"
    write_json(page / "manifest.json", {"assets": []})
    assert mod.find_page_dir(tmp_path, slide_no=4) == page


def test_find_page_dir_missing_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        mod.find_page_dir(tmp_path, slide_no=1)


# ---------------------------------------------------------------------------
# collect_slide_dirs
# ---------------------------------------------------------------------------


def test_collect_slide_dirs_matches_pattern_sorted(tmp_path):
    for name in ["slide-2", "slide-10", "slide-1"]:
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    dirs = mod.collect_slide_dirs(tmp_path, "slide-*")
    assert [p.name for p in dirs] == ["slide-1", "slide-2", "slide-10"]


def test_collect_slide_dirs_falls_back_to_all_dirs(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    dirs = mod.collect_slide_dirs(tmp_path, "slide-*")
    assert [p.name for p in dirs] == ["alpha", "beta"]


def test_collect_slide_dirs_not_a_directory_raises(tmp_path):
    f = tmp_path / "file"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        mod.collect_slide_dirs(f, "slide-*")


def test_collect_slide_dirs_empty_raises(tmp_path):
    with pytest.raises(ValueError, match="No slide directories"):
        mod.collect_slide_dirs(tmp_path, "slide-*")


# ---------------------------------------------------------------------------
# main (end-to-end)
# ---------------------------------------------------------------------------


def _build_slide(root: Path, number: int) -> None:
    slide_dir = root / f"slide-{number:02d}"
    write_json(slide_dir / "text-layer.json", {"texts": [{"text": f"slide {number}"}]})
    page = slide_dir / "visual-layers" / f"slide-{number:02d}"
    write_json(page / "manifest.json", {"assets": [{"file": "bg.png"}]})
    (page / "bg.png").write_bytes(b"\x89PNG\r\n")


def test_main_merges_slides(tmp_path, monkeypatch, capsys):
    slides_root = tmp_path / "slides"
    _build_slide(slides_root, 1)
    _build_slide(slides_root, 2)
    out_layers = tmp_path / "out" / "visual-layers"
    out_text = tmp_path / "out" / "text-layer.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            str(slides_root),
            "--out-layers-root",
            str(out_layers),
            "--out-text-json",
            str(out_text),
        ],
    )
    rc = mod.main()
    assert rc == 0

    deck = json.loads(out_text.read_text(encoding="utf-8"))
    assert deck["background"] == "#FFFFFF"
    assert [s["slide"] for s in deck["slides"]] == [1, 2]
    assert (out_layers / "slide-01" / "manifest.json").is_file()
    assert (out_layers / "slide-02" / "bg.png").is_file()
    # report is echoed to stdout as JSON
    report = json.loads(capsys.readouterr().out)
    assert len(report["slides"]) == 2


def test_main_existing_output_without_force_raises(tmp_path, monkeypatch):
    slides_root = tmp_path / "slides"
    _build_slide(slides_root, 1)
    out_layers = tmp_path / "out" / "visual-layers"
    (out_layers / "slide-01").mkdir(parents=True)
    out_text = tmp_path / "out" / "text-layer.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            str(slides_root),
            "--out-layers-root",
            str(out_layers),
            "--out-text-json",
            str(out_text),
        ],
    )
    with pytest.raises(FileExistsError):
        mod.main()


def test_main_force_overwrites_existing(tmp_path, monkeypatch):
    slides_root = tmp_path / "slides"
    _build_slide(slides_root, 1)
    out_layers = tmp_path / "out" / "visual-layers"
    stale = out_layers / "slide-01"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("old", encoding="utf-8")
    out_text = tmp_path / "out" / "text-layer.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            str(slides_root),
            "--out-layers-root",
            str(out_layers),
            "--out-text-json",
            str(out_text),
            "--force",
        ],
    )
    assert mod.main() == 0
    assert not (stale / "stale.txt").exists()
    assert (stale / "manifest.json").is_file()
