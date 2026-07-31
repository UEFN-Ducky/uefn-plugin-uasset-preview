#!/usr/bin/env node
/** Bundle ui/src/viewer.js → ui/viewer.bundle.js and copy Draco decoders. */
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "ui", "viewer.bundle.js");
const dracoSrc = join(root, "node_modules", "three", "examples", "jsm", "libs", "draco", "gltf");
const dracoDest = join(root, "ui", "draco");

await esbuild.build({
  entryPoints: [join(root, "ui", "src", "viewer.js")],
  bundle: true,
  outfile: out,
  format: "iife",
  platform: "browser",
  target: ["chrome110"],
  minify: true,
  sourcemap: false,
  logLevel: "info",
});

mkdirSync(dracoDest, { recursive: true });
if (!existsSync(dracoSrc)) {
  console.warn("Draco decoder source missing — run npm install first");
} else {
  for (const name of ["draco_decoder.js", "draco_decoder.wasm", "draco_wasm_wrapper.js"]) {
    cpSync(join(dracoSrc, name), join(dracoDest, name));
  }
  console.log("copied Draco → ui/draco/");
}
console.log("wrote", out);
