import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  parseArgs,
  requireArg,
  slideSize,
  pad,
  visibleStyleValue,
  lineFillValue,
  lineWidthValue,
  hasVisibleTextBoxStyle,
  collectTextFillViolations,
  findPageDir,
  slidesFromTextSpec,
  assetPosition,
  normalizePptxColor,
  pptxScale,
  toPptxPosition,
} from "../scripts/build_editable_ppt_from_layers.mjs";

// ---------------------------------------------------------------------------
// parseArgs
// ---------------------------------------------------------------------------

test("parseArgs parses key/value pairs and boolean flags", () => {
  const args = parseArgs(["--layers-root", "/a", "--strict", "--out", "/b.pptx"]);
  assert.equal(args["layers-root"], "/a");
  assert.equal(args.out, "/b.pptx");
  assert.equal(args.strict, true);
});

test("parseArgs treats a flag followed by another flag as boolean", () => {
  const args = parseArgs(["--fail-on-text-fill", "--backend", "artifact"]);
  assert.equal(args["fail-on-text-fill"], true);
  assert.equal(args.backend, "artifact");
});

test("parseArgs recognizes -h as help", () => {
  assert.equal(parseArgs(["-h"]).help, true);
});

test("parseArgs throws on a positional argument", () => {
  assert.throws(() => parseArgs(["oops"]), /Unexpected argument/);
});

// ---------------------------------------------------------------------------
// requireArg
// ---------------------------------------------------------------------------

test("requireArg returns present value as string", () => {
  assert.equal(requireArg({ out: 42 }, "out"), "42");
});

test("requireArg throws when missing", () => {
  assert.throws(() => requireArg({}, "out"), /Missing required --out/);
});

// ---------------------------------------------------------------------------
// slideSize
// ---------------------------------------------------------------------------

test("slideSize parses WIDTHxHEIGHT", () => {
  assert.deepEqual(slideSize("1280x720"), { width: 1280, height: 720 });
});

test("slideSize defaults when value is falsy", () => {
  assert.deepEqual(slideSize(undefined), { width: 960, height: 540 });
});

test("slideSize throws on malformed value", () => {
  assert.throws(() => slideSize("1280*720"), /Expected --slide-size/);
});

// ---------------------------------------------------------------------------
// pad
// ---------------------------------------------------------------------------

test("pad zero-pads to two digits", () => {
  assert.equal(pad(3), "03");
  assert.equal(pad(12), "12");
});

// ---------------------------------------------------------------------------
// visibleStyleValue
// ---------------------------------------------------------------------------

test("visibleStyleValue treats empty/none/transparent as invisible", () => {
  for (const v of [undefined, null, false, "", "none", "transparent", "#00000000", "rgba(0,0,0,0)"]) {
    assert.equal(visibleStyleValue(v), false, `expected ${JSON.stringify(v)} invisible`);
  }
});

test("visibleStyleValue detects a real color string", () => {
  assert.equal(visibleStyleValue("#ff0000"), true);
});

test("visibleStyleValue inspects fill objects with width", () => {
  assert.equal(visibleStyleValue({ fill: "#fff", width: 2 }), true);
  assert.equal(visibleStyleValue({ fill: "#fff", width: 0 }), false);
});

test("visibleStyleValue inspects nested color objects", () => {
  assert.equal(visibleStyleValue({ color: "#123456" }), true);
  assert.equal(visibleStyleValue({ color: "none" }), false);
});

// ---------------------------------------------------------------------------
// lineFillValue / lineWidthValue
// ---------------------------------------------------------------------------

test("lineFillValue reads object fill/color then falls back", () => {
  assert.equal(lineFillValue({ fill: "#111" }, "#000"), "#111");
  assert.equal(lineFillValue({ color: "#222" }, "#000"), "#222");
  assert.equal(lineFillValue("#333", "#000"), "#333");
  assert.equal(lineFillValue(null, "#000"), "#000");
});

test("lineWidthValue returns 0 when not preserving text fill", () => {
  assert.equal(lineWidthValue({ line: { width: 5 } }, false), 0);
});

test("lineWidthValue reads explicit object width and lineWidth fallback", () => {
  assert.equal(lineWidthValue({ line: { width: 5 } }, true), 5);
  assert.equal(lineWidthValue({ line: "#000", lineWidth: 3 }, true), 3);
  assert.equal(lineWidthValue({ line: "#000" }, true), 1);
  assert.equal(lineWidthValue({}, true), 0);
});

// ---------------------------------------------------------------------------
// hasVisibleTextBoxStyle / collectTextFillViolations
// ---------------------------------------------------------------------------

test("hasVisibleTextBoxStyle true when fill or line is visible", () => {
  assert.equal(hasVisibleTextBoxStyle({ fill: "#fff" }), true);
  assert.equal(hasVisibleTextBoxStyle({ line: "#000" }), true);
  assert.equal(hasVisibleTextBoxStyle({ fill: "none", line: "transparent" }), false);
});

test("collectTextFillViolations reports styled text boxes only", () => {
  const specs = [
    {
      slide: 1,
      texts: [
        { name: "clean", text: "ok" },
        { name: "styled", text: "bad", fill: "#fff" },
      ],
    },
    { slide: 2, texts: [{ text: "also-clean" }] },
  ];
  const violations = collectTextFillViolations(specs);
  assert.equal(violations.length, 1);
  assert.equal(violations[0].slide, 1);
  assert.equal(violations[0].index, 2);
  assert.equal(violations[0].name, "styled");
  assert.equal(violations[0].fill, "#fff");
});

// ---------------------------------------------------------------------------
// slidesFromTextSpec
// ---------------------------------------------------------------------------

test("slidesFromTextSpec normalizes slide numbers and texts", () => {
  const result = slidesFromTextSpec({
    slides: [{ texts: [{ text: "a" }] }, { slide: 7 }],
  });
  assert.deepEqual(result, [
    { slide: 1, texts: [{ text: "a" }] },
    { slide: 7, texts: [] },
  ]);
});

test("slidesFromTextSpec throws without a slides array", () => {
  assert.throws(() => slidesFromTextSpec({}), /must contain a slides array/);
});

// ---------------------------------------------------------------------------
// assetPosition
// ---------------------------------------------------------------------------

const SIZE = { width: 960, height: 540 };

test("assetPosition fills the slide when no position given", () => {
  assert.deepEqual(assetPosition({}, SIZE), { left: 0, top: 0, width: 960, height: 540 });
});

test("assetPosition uses raw position without canvas", () => {
  assert.deepEqual(
    assetPosition({ position: [10, 20, 30, 40] }, SIZE),
    { left: 10, top: 20, width: 30, height: 40 },
  );
});

test("assetPosition scales by canvas dimensions", () => {
  const pos = assetPosition({ position: [480, 270, 480, 270], canvas: [960, 540] }, SIZE);
  assert.deepEqual(pos, { left: 480, top: 270, width: 480, height: 270 });
});

test("assetPosition enforces a minimum width and height of 1", () => {
  const pos = assetPosition({ position: [0, 0, 0, 0] }, SIZE);
  assert.equal(pos.width, 1);
  assert.equal(pos.height, 1);
});

// ---------------------------------------------------------------------------
// normalizePptxColor
// ---------------------------------------------------------------------------

test("normalizePptxColor strips leading hash and clamps to 6 chars", () => {
  assert.equal(normalizePptxColor("#abcdef"), "abcdef");
  assert.equal(normalizePptxColor("112233445566"), "112233");
});

test("normalizePptxColor returns fallback for empty input", () => {
  assert.equal(normalizePptxColor(undefined), "333333");
  assert.equal(normalizePptxColor(null, "FFFFFF"), "FFFFFF");
});

// ---------------------------------------------------------------------------
// pptxScale / toPptxPosition
// ---------------------------------------------------------------------------

test("pptxScale produces a 10-inch-wide layout preserving aspect ratio", () => {
  const scale = pptxScale(SIZE);
  assert.equal(scale.width, 10);
  assert.ok(Math.abs(scale.height - 5.625) < 1e-9);
});

test("toPptxPosition converts pixel box to scaled inches", () => {
  const scale = pptxScale(SIZE);
  const box = toPptxPosition({ left: 96, top: 54, width: 192, height: 108 }, scale);
  assert.ok(Math.abs(box.x - 1) < 1e-9);
  assert.ok(Math.abs(box.w - 2) < 1e-9);
});

// ---------------------------------------------------------------------------
// findPageDir (filesystem)
// ---------------------------------------------------------------------------

test("findPageDir locates a slide folder by naming convention", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "codex-ppt-"));
  try {
    await fs.mkdir(path.join(root, "slide-02"));
    const found = await findPageDir(root, 2);
    assert.equal(found, path.join(root, "slide-02"));
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("findPageDir throws when no folder matches", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "codex-ppt-"));
  try {
    await assert.rejects(() => findPageDir(root, 5), /Could not find page folder/);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
