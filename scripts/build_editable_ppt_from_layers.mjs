#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { readJson, writeJson } from "./lib/json_io.mjs";

const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "-h") {
      args.help = true;
      continue;
    }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key.slice(2)] = true;
    } else {
      args[key.slice(2)] = next;
      i += 1;
    }
  }
  return args;
}

function printHelp() {
  console.log(`Usage:
  node scripts/build_editable_ppt_from_layers.mjs \\
    --layers-root /path/to/visual-layers \\
    --text-json /path/to/text-layer.json \\
    --out /path/to/editable.pptx \\
    [--workspace /path/to/workspace] \\
    [--preview-dir /path/to/preview] \\
    [--layout-dir /path/to/layout] \\
    [--slide-size 960x540] \\
    [--backend auto|artifact|pptxgenjs] \\
    [--fail-on-text-fill]

Notes:
  - backend=auto tries the Codex Presentations artifact runtime first, then falls back to pptxgenjs.
  - backend=artifact renders PPTX, preview PNGs, and layout JSON through the Codex Presentations runtime.
  - backend=pptxgenjs generates an editable PPTX without preview rendering; run npm install in the skill folder first.
  - Set PRESENTATIONS_ARTIFACT_UTILS to artifact_tool_utils.mjs when using backend=artifact outside the bundled Codex environment.
`);
}

function requireArg(args, key) {
  if (!args[key]) throw new Error(`Missing required --${key}`);
  return String(args[key]);
}

function slideSize(value) {
  const match = String(value || "960x540").match(/^(\d+)x(\d+)$/);
  if (!match) throw new Error(`Expected --slide-size WIDTHxHEIGHT, got ${value}`);
  return { width: Number(match[1]), height: Number(match[2]) };
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function visibleStyleValue(value) {
  if (value === undefined || value === null || value === false) return false;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return !["", "none", "transparent", "#00000000", "rgba(0,0,0,0)"].includes(normalized);
  }
  if (typeof value === "object") {
    if ("fill" in value && visibleStyleValue(value.fill)) return Number(value.width ?? 1) !== 0;
    if ("color" in value && visibleStyleValue(value.color)) return true;
    return Object.values(value).some((entry) => visibleStyleValue(entry));
  }
  return Boolean(value);
}

function lineFillValue(value, transparent) {
  if (value && typeof value === "object") return value.fill || value.color || transparent;
  return value || transparent;
}

function lineWidthValue(item, preserveTextFill) {
  if (!preserveTextFill) return 0;
  if (!item.line) return 0;
  if (item.line && typeof item.line === "object" && item.line.width !== undefined) return Number(item.line.width);
  return Number(item.lineWidth ?? 1);
}

function hasVisibleTextBoxStyle(item) {
  return visibleStyleValue(item.fill) || visibleStyleValue(item.line);
}

function collectTextFillViolations(slideSpecs) {
  const violations = [];
  for (const slideSpec of slideSpecs) {
    slideSpec.texts.forEach((item, index) => {
      if (!hasVisibleTextBoxStyle(item)) return;
      violations.push({
        slide: slideSpec.slide,
        index: index + 1,
        name: item.name || `text-${index + 1}`,
        text: String(item.text ?? "").slice(0, 80),
        fill: item.fill ?? null,
        line: item.line ?? null,
      });
    });
  }
  return violations;
}

async function importUtils() {
  const explicit = process.env.PRESENTATIONS_ARTIFACT_UTILS;
  const candidates = [explicit].filter(Boolean);
  const presentationsRoot = path.join(os.homedir(), ".codex/plugins/cache/openai-primary-runtime/presentations");
  try {
    const entries = await fs.readdir(presentationsRoot, { withFileTypes: true });
    const versions = entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
      .reverse();
    for (const version of versions) {
      candidates.push(path.join(presentationsRoot, version, "skills/presentations/scripts/artifact_tool_utils.mjs"));
    }
  } catch {
    // Running outside a Codex Presentations runtime is supported only when PRESENTATIONS_ARTIFACT_UTILS is set.
  }
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return import(pathToFileURL(candidate).href);
    } catch {
      // try next
    }
  }
  throw new Error("Could not locate Presentations artifact_tool_utils.mjs. Set PRESENTATIONS_ARTIFACT_UTILS.");
}

function isArtifactRuntimeMissing(error) {
  return String(error?.message || error || "").includes("Could not locate Presentations artifact_tool_utils.mjs");
}

function importPptxGen() {
  try {
    const mod = require("pptxgenjs");
    return mod.default || mod;
  } catch (error) {
    throw new Error(
      `Could not load pptxgenjs fallback backend. Run "npm install" in the codex-ppt skill folder first. Original error: ${error.message}`,
    );
  }
}

async function findPageDir(root, slideNo) {
  const names = [
    `第${pad(slideNo)}页`,
    `slide-${pad(slideNo)}`,
    `slide_${pad(slideNo)}`,
    pad(slideNo),
    String(slideNo),
  ];
  for (const name of names) {
    const dir = path.join(root, name);
    try {
      const stat = await fs.stat(dir);
      if (stat.isDirectory()) return dir;
    } catch {
      // continue
    }
  }
  throw new Error(`Could not find page folder for slide ${slideNo} under ${root}`);
}

function slidesFromTextSpec(spec) {
  if (!Array.isArray(spec.slides)) throw new Error("Text JSON must contain a slides array.");
  return spec.slides.map((slide, index) => ({
    slide: Number(slide.slide ?? index + 1),
    texts: Array.isArray(slide.texts) ? slide.texts : [],
  }));
}

function assetPosition(asset, size) {
  const position = Array.isArray(asset.position) ? asset.position : null;
  if (!position || position.length !== 4) {
    return { left: 0, top: 0, width: size.width, height: size.height };
  }
  const canvas = Array.isArray(asset.canvas) && asset.canvas.length === 2
    ? asset.canvas
    : (Array.isArray(asset.sourceCanvas) && asset.sourceCanvas.length === 2 ? asset.sourceCanvas : null);
  if (!canvas) {
    return {
      left: Number(position[0]),
      top: Number(position[1]),
      width: Math.max(1, Number(position[2])),
      height: Math.max(1, Number(position[3])),
    };
  }
  const [canvasWidth, canvasHeight] = canvas.map(Number);
  return {
    left: Number(position[0]) / canvasWidth * size.width,
    top: Number(position[1]) / canvasHeight * size.height,
    width: Math.max(1, Number(position[2]) / canvasWidth * size.width),
    height: Math.max(1, Number(position[3]) / canvasHeight * size.height),
  };
}

function normalizePptxColor(value, fallback = "333333") {
  if (!value) return fallback;
  const text = String(value).trim();
  if (text.startsWith("#")) return text.slice(1).slice(0, 6) || fallback;
  return text.slice(0, 6) || fallback;
}

function pptxScale(size) {
  const width = 10;
  return { x: width / size.width, y: (width * size.height / size.width) / size.height, width, height: width * size.height / size.width };
}

function toPptxPosition(position, scale) {
  return {
    x: Number(position.left) * scale.x,
    y: Number(position.top) * scale.y,
    w: Number(position.width) * scale.x,
    h: Number(position.height) * scale.y,
  };
}

async function addImageLayer(slide, artifactUtils, filePath, size, name, asset = {}) {
  const image = slide.images.add({
    blob: await artifactUtils.readImageBlob(filePath),
    fit: "contain",
    alt: name,
    name,
  });
  image.position = assetPosition(asset, size);
  return image;
}

function addTextBox(slide, item, index, options = {}) {
  const transparent = "#00000000";
  const preserveTextFill = Boolean(options.preserveTextFill);
  const position = {
    left: Number(item.x),
    top: Number(item.y),
    width: Math.max(1, Number(item.w)),
    height: Math.max(1, Number(item.h)),
  };
  if (item.rotation !== undefined && item.rotation !== null) {
    position.rotation = Number(item.rotation);
  }
  const shape = slide.shapes.add({
    geometry: "rect",
    name: item.name || `text-${index}`,
    position,
    fill: preserveTextFill ? item.fill || transparent : transparent,
    line: {
      fill: preserveTextFill ? lineFillValue(item.line, transparent) : transparent,
      width: lineWidthValue(item, preserveTextFill),
    },
  });
  shape.text = String(item.text ?? "");
  shape.text.fontSize = Number(item.size ?? 14);
  shape.text.typeface = item.typeface || "PingFang SC";
  shape.text.color = item.color || "#333333";
  shape.text.bold = Boolean(item.bold);
  shape.text.alignment = item.align || "left";
  shape.text.verticalAlignment = item.valign || "top";
  shape.text.insets = item.insets || { left: 0, right: 0, top: 0, bottom: 0 };
  shape.text.wrap = item.wrap || "square";
  if (item.autoFit) shape.text.autoFit = item.autoFit;
  return shape;
}

function addTextBoxPptxGen(slide, item, index, scale, options = {}) {
  const preserveTextFill = Boolean(options.preserveTextFill);
  const position = toPptxPosition({
    left: Number(item.x),
    top: Number(item.y),
    width: Math.max(1, Number(item.w)),
    height: Math.max(1, Number(item.h)),
  }, scale);
  const pptxOptions = {
    ...position,
    name: item.name || `text-${index}`,
    margin: 0,
    fontFace: item.typeface || "PingFang SC",
    fontSize: Number(item.size ?? 14),
    color: normalizePptxColor(item.color),
    bold: Boolean(item.bold),
    align: item.align || "left",
    valign: item.valign || "top",
    fit: item.autoFit === "shrink" ? "shrink" : undefined,
    rotate: item.rotation !== undefined && item.rotation !== null ? Number(item.rotation) : undefined,
    breakLine: false,
  };
  if (preserveTextFill && visibleStyleValue(item.fill)) {
    pptxOptions.fill = { color: normalizePptxColor(item.fill, "FFFFFF"), transparency: 0 };
  }
  if (preserveTextFill && visibleStyleValue(item.line)) {
    pptxOptions.line = { color: normalizePptxColor(lineFillValue(item.line, "#000000"), "000000"), width: lineWidthValue(item, true) };
  } else {
    pptxOptions.line = { color: "FFFFFF", transparency: 100 };
  }
  slide.addText(String(item.text ?? ""), pptxOptions);
}

async function addVisualLayers(slide, artifactUtils, pageDir, size, slideNo) {
  const manifestPath = path.join(pageDir, "manifest.json");
  const manifest = await readJson(manifestPath);
  const assets = Array.isArray(manifest.assets) ? manifest.assets : [];
  for (const asset of assets) {
    await addImageLayer(slide, artifactUtils, path.join(pageDir, asset.file), size, `slide-${pad(slideNo)}-${asset.file}`, asset);
  }
  return assets.length;
}

async function addVisualLayersPptxGen(slide, pageDir, size, slideNo, scale) {
  const manifestPath = path.join(pageDir, "manifest.json");
  const manifest = await readJson(manifestPath);
  const assets = Array.isArray(manifest.assets) ? manifest.assets : [];
  for (const asset of assets) {
    const position = assetPosition(asset, size);
    slide.addImage({
      path: path.join(pageDir, asset.file),
      ...toPptxPosition(position, scale),
      altText: asset.name || `slide-${pad(slideNo)}-${asset.file}`,
    });
  }
  return assets.length;
}

async function buildWithPptxGen({
  layersRoot,
  out,
  workspace,
  previewDir,
  layoutDir,
  manifestOut,
  size,
  preserveTextFill,
  textSpec,
  slideSpecs,
  textFillViolations,
  textJson,
}) {
  const PptxGenJS = importPptxGen();
  const pptx = new PptxGenJS();
  const scale = pptxScale(size);
  pptx.author = "Codex PPT";
  pptx.subject = "Editable PPTX generated from slide visual layers";
  pptx.title = "Codex PPT editable output";
  pptx.company = "Codex PPT";
  pptx.lang = "zh-CN";
  pptx.defineLayout({ name: "IMAGE_PPT_KING_CUSTOM", width: scale.width, height: scale.height });
  pptx.layout = "IMAGE_PPT_KING_CUSTOM";
  pptx.theme = {
    headFontFace: "PingFang SC",
    bodyFontFace: "PingFang SC",
    lang: "zh-CN",
  };

  const records = [];
  for (const slideSpec of slideSpecs) {
    const slide = pptx.addSlide();
    slide.background = { color: normalizePptxColor(textSpec.background, "F7F8F7") };
    const pageDir = await findPageDir(layersRoot, slideSpec.slide);
    const visualLayerCount = await addVisualLayersPptxGen(slide, pageDir, size, slideSpec.slide, scale);
    slideSpec.texts.forEach((textItem, index) => addTextBoxPptxGen(slide, textItem, index + 1, scale, { preserveTextFill }));
    records.push({
      slideNumber: slideSpec.slide,
      pageDir,
      visualLayerCount,
      textCount: slideSpec.texts.length,
    });
  }

  await fs.mkdir(path.dirname(out), { recursive: true });
  await pptx.writeFile({ fileName: out });
  const stat = await fs.stat(out);
  if (stat.size <= 0) throw new Error(`Empty PPTX exported: ${out}`);

  await fs.mkdir(previewDir, { recursive: true });
  await fs.mkdir(layoutDir, { recursive: true });
  for (let i = 0; i < records.length; i += 1) {
    await writeJson(path.join(layoutDir, `slide-${pad(i + 1)}.layout.json`), {
      backend: "pptxgenjs",
      slide: records[i].slideNumber,
      note: "Layout JSON is a lightweight object summary. Pixel preview rendering requires backend=artifact.",
      pageDir: records[i].pageDir,
      visualLayerCount: records[i].visualLayerCount,
      textCount: records[i].textCount,
    });
  }

  const buildManifest = {
    output: out,
    bytes: stat.size,
    backend: "pptxgenjs",
    slideCount: records.length,
    slideSize: size,
    layersRoot,
    textJson,
    workspace,
    previewDir,
    layoutDir,
    previewPaths: [],
    previewNote: "backend=pptxgenjs does not render preview PNGs. Use backend=artifact in Codex Presentations runtime for previews.",
    textFillPolicy: preserveTextFill ? "preserve" : "strip",
    textFillViolationCount: textFillViolations.length,
    textFillViolations,
    slides: records,
  };
  await writeJson(manifestOut, buildManifest);
  console.log(JSON.stringify(buildManifest, null, 2));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }
  const layersRoot = path.resolve(requireArg(args, "layers-root"));
  const textJson = path.resolve(requireArg(args, "text-json"));
  const out = path.resolve(requireArg(args, "out"));
  const workspace = path.resolve(args.workspace || path.join(path.dirname(out), "codex-ppt-editable-workspace"));
  const previewDir = args["preview-dir"] ? path.resolve(args["preview-dir"]) : path.join(workspace, "preview");
  const layoutDir = args["layout-dir"] ? path.resolve(args["layout-dir"]) : path.join(workspace, "layout");
  const manifestOut = args.manifest ? path.resolve(args.manifest) : path.join(workspace, "build-manifest.json");
  const size = slideSize(args["slide-size"]);
  const preserveTextFill = Boolean(args["preserve-text-fill"]);
  const failOnTextFill = Boolean(args["fail-on-text-fill"]);
  const backend = String(args.backend || "auto");
  if (!["auto", "artifact", "pptxgenjs"].includes(backend)) {
    throw new Error(`Unsupported --backend ${backend}; expected auto, artifact, or pptxgenjs.`);
  }

  const textSpec = await readJson(textJson);
  const slideSpecs = slidesFromTextSpec(textSpec);
  const textFillViolations = collectTextFillViolations(slideSpecs);
  if (failOnTextFill && textFillViolations.length > 0) {
    throw new Error(`Text JSON contains ${textFillViolations.length} text boxes with fill/line. Remove them or build without --fail-on-text-fill.`);
  }

  let utils = null;
  if (backend !== "pptxgenjs") {
    try {
      utils = await importUtils();
    } catch (error) {
      if (backend === "artifact" || !isArtifactRuntimeMissing(error)) throw error;
    }
  }

  if (!utils) {
    await buildWithPptxGen({
      layersRoot,
      out,
      workspace,
      previewDir,
      layoutDir,
      manifestOut,
      size,
      preserveTextFill,
      textSpec,
      slideSpecs,
      textFillViolations,
      textJson,
    });
    return;
  }

  await utils.ensureArtifactToolWorkspace(workspace);
  const artifact = await utils.importArtifactTool(workspace);
  const { Presentation, PresentationFile } = artifact;
  const presentation = Presentation.create({ slideSize: size });
  const records = [];

  for (const slideSpec of slideSpecs) {
    const slide = presentation.slides.add();
    const bg = slide.shapes.add({
      geometry: "rect",
      name: `slide-${pad(slideSpec.slide)}-background`,
      position: { left: 0, top: 0, width: size.width, height: size.height },
      fill: textSpec.background || "#F7F8F7",
      line: { fill: textSpec.background || "#F7F8F7", width: 0 },
    });
    bg.text = "";
    const pageDir = await findPageDir(layersRoot, slideSpec.slide);
    const visualLayerCount = await addVisualLayers(slide, utils, pageDir, size, slideSpec.slide);
    slideSpec.texts.forEach((textItem, index) => addTextBox(slide, textItem, index + 1, { preserveTextFill }));
    records.push({
      slideNumber: slideSpec.slide,
      pageDir,
      visualLayerCount,
      textCount: slideSpec.texts.length,
      slideObject: slide,
    });
  }

  await fs.mkdir(path.dirname(out), { recursive: true });
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(out);
  const stat = await fs.stat(out);
  if (stat.size <= 0) throw new Error(`Empty PPTX exported: ${out}`);

  await fs.mkdir(previewDir, { recursive: true });
  await fs.mkdir(layoutDir, { recursive: true });
  const previewPaths = [];
  for (let i = 0; i < records.length; i += 1) {
    const n = pad(i + 1);
    const previewPath = path.join(previewDir, `slide-${n}.png`);
    await utils.saveBlobToFile(await presentation.export({ slide: records[i].slideObject, format: "png", scale: 1 }), previewPath);
    previewPaths.push(previewPath);
    await utils.saveBlobToFile(await presentation.export({ slide: records[i].slideObject, format: "layout" }), path.join(layoutDir, `slide-${n}.layout.json`));
  }

  const buildManifest = {
    output: out,
    bytes: stat.size,
    backend: "artifact",
    slideCount: records.length,
    slideSize: size,
    layersRoot,
    textJson,
    previewDir,
    layoutDir,
    textFillPolicy: preserveTextFill ? "preserve" : "strip",
    textFillViolationCount: textFillViolations.length,
    textFillViolations,
    slides: records.map(({ slideNumber, pageDir, visualLayerCount, textCount }) => ({ slide: slideNumber, pageDir, visualLayerCount, textCount })),
    previewPaths,
  };
  await writeJson(manifestOut, buildManifest);
  console.log(JSON.stringify(buildManifest, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
