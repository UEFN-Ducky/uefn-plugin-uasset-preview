// Niagara VM: classic bytecode decoding and execution on hand-made programs.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

global.window = {};
eval(fs.readFileSync(path.join(__dirname, "..", "ui", "niagara_sim.js"), "utf8"));
const { decode, create } = global.window.UassetNiagaraSim;

const OP = { done: 0, add: 1, cmpgt: 39, select: 43, inputdata_float: 77, outputdata_float: 83, acquireindex: 86, external_func_call: 87, exec_index: 88, i2f: 72 };
const u16 = (v) => [v & 255, v >> 8];
const b64 = (bytes) => Buffer.from(bytes).toString("base64");
const floats = (list) => b64(new Uint8Array(new Float32Array(list).buffer));

test("operands: a type byte marks constants (byte offsets), registers otherwise", () => {
  const code = [OP.add, 0b10, ...u16(3), ...u16(8), ...u16(5), OP.done];
  const ins = decode(Uint8Array.from(code));
  assert.deepStrictEqual(ins[0], { op: "add", types: 2, srcs: [3, 8], dest: 5 });
  assert.strictEqual(ins[1].op, "done");
});

test("an unknown external function refuses the emitter with its name", () => {
  const code = [OP.external_func_call, ...u16(0), ...u16(0x8001), ...u16(2), OP.done];
  assert.throws(() => decode(Uint8Array.from(code), [{ name: "SampleField", owner: "x", inputs: [false], outputs: 1 }]), /SampleField/);
});

test("a spawn script runs per instance: index, constant, curve, kept particles written out", () => {
  // r0 = exec_index; r1 = float(r0); r2 = r1 + C[1] (=10); r3 = curve(r2 / 20 ~ via LUT on 0..20); keep = r2 > C[2] (=10.5)
  const code = [
    OP.exec_index, ...u16(0),
    OP.i2f, 0, ...u16(0), ...u16(1),
    OP.add, 0b10, ...u16(1), ...u16(4), ...u16(2),
    OP.external_func_call, ...u16(0), ...u16(0x8002), ...u16(3),
    OP.cmpgt, 0b10, ...u16(2), ...u16(8), ...u16(4),
    OP.acquireindex, 0, ...u16(0), ...u16(4), ...u16(5),
    OP.outputdata_float, 0, ...u16(0), ...u16(5), ...u16(2), ...u16(0),
    OP.outputdata_float, 0, ...u16(0), ...u16(5), ...u16(3), ...u16(1),
    OP.done,
  ];
  const curve = { name: "SampleCurve", owner: "c", inputs: [false], outputs: 1, lut: [0, 2], samples: 2, min: 0, max: 20 };
  const script = (bytes) => ({ code: b64(bytes), temps: 6, constants: floats([0, 10, 10.5]), half: 3, interpolated: false, params: {}, engine: 0, externals: [curve] });
  const layout = [{ name: "Value", f: 0, nf: 1, i: 0, ni: 0, offset: 0, size: 1, int: false }, { name: "Curve", f: 1, nf: 1, i: 0, ni: 0, offset: 1, size: 1, int: false }];
  const sim = create({}, [{ name: "E", spawn: script(code), update: script([OP.done]), layout, rate: 0, burst: 3, burst_time: 0, loop: 0 }]);
  assert.strictEqual(sim.skipped.length, 0);
  sim.advance(1 / 60);
  const data = sim.particles("E");
  // instances 0, 1, 2 -> values 10, 11, 12; only 11 and 12 pass "> 10.5"; the curve maps 0..20 to 0..2
  assert.strictEqual(data.count, 2);
  assert.deepStrictEqual(Array.from(data.f[0].subarray(0, 2)), [11, 12]);
  assert.ok(Math.abs(data.f[1][0] - 1.1) < 1e-5 && Math.abs(data.f[1][1] - 1.2) < 1e-5);
});
