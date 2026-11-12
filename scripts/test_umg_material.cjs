// Shader builder: known graphs compile to GLSL, unknown nodes are refused (no guessing).
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

global.window = {};
eval(fs.readFileSync(path.join(__dirname, "..", "ui", "umg", "material.js"), "utf8"));
const { compile } = global.window.UmgMaterial;
const wire = (node, mask = "", output = 0) => ({ node, output, mask });
const graph = (nodes, pins, extra = {}) => ({ domain: "MD_UI", blend: "BLEND_Translucent", nodes, pins, overrides: { scalar: {}, vector: {}, texture: {} }, ...extra });

test("parameters become uniforms and instance overrides replace defaults", () => {
  const g = graph({
    1: { op: "VectorParameter", p: { ParameterName: "Tint", DefaultValue: [1, 0, 0, 1] } },
    2: { op: "ScalarParameter", p: { ParameterName: "Fade", DefaultValue: 0.5 } },
    3: { op: "Multiply", p: { A: wire(1), B: wire(2) } },
  }, { EmissiveColor: wire(3), Opacity: wire(2) }, { overrides: { scalar: { Fade: 0.9 }, vector: {}, texture: {} } });
  const out = compile(g);
  assert.ok(out.ok, out.error);
  const fade = out.uniforms.find((u) => u.key === "Fade");
  assert.strictEqual(fade.value, 0.9);
  assert.match(out.source, /uniform vec4 u\d;/);
  assert.strictEqual(out.usesTime, false);
});

test("absent constants use Unreal's node defaults", () => {
  const g = graph({ 1: { op: "TextureCoordinate", p: {} }, 2: { op: "Divide", p: { A: wire(1) } } }, { EmissiveColor: wire(2) });
  const out = compile(g);
  assert.ok(out.ok, out.error);
  assert.match(out.source, /\/ 2\.0\)/); // Divide: ConstB defaults to 2
});

test("time marks the material as animated", () => {
  const out = compile(graph({ 1: { op: "Time", p: {} }, 2: { op: "Sine", p: { Input: wire(1) } } }, { EmissiveColor: wire(2) }));
  assert.ok(out.ok && out.usesTime);
});

test("an unknown node refuses the whole material", () => {
  const out = compile(graph({ 1: { op: "SceneTexture", p: {} } }, { EmissiveColor: wire(1) }));
  assert.strictEqual(out.ok, false);
  assert.match(out.error, /SceneTexture/);
});

test("a wire loop is an error, not a hang", () => {
  const out = compile(graph({ 1: { op: "Abs", p: { Input: wire(2) } }, 2: { op: "Abs", p: { Input: wire(1) } } }, { EmissiveColor: wire(1) }));
  assert.strictEqual(out.ok, false);
});

test("non-UI materials are refused", () => {
  assert.strictEqual(compile(graph({}, {}, { domain: "MD_Surface" })).ok, false);
});

test("Rotator turns the coordinates around the centre by Speed * Time", () => {
  const out = compile(graph({
    1: { op: "ScalarParameter", p: { ParameterName: "Rotation", DefaultValue: 0 } },
    2: { op: "Rotator", p: { Time: wire(1), Speed: 0.5 } },
    3: { op: "TextureCoordinate", p: {} },
    4: { op: "Distance", p: { A: wire(2), B: wire(3) } },
  }, { EmissiveColor: wire(4) }));
  assert.ok(out.ok, out.error);
  assert.match(out.source, /0\.5 \* u\d/);          // Speed * Time (Time wired to the parameter)
  assert.match(out.source, /vec2\(0\.5,0\.5\)/);   // default centre
  assert.strictEqual(out.usesTime, false);             // its Time is the parameter, not the clock
  const clock = compile(graph({ 1: { op: "Rotator", p: {} } }, { EmissiveColor: wire(1) }));
  assert.ok(clock.ok && clock.usesTime);               // unwired Time: the game clock
});
