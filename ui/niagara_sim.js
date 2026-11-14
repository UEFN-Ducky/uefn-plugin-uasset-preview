/* Experimental Niagara playback: runs an emitter's compiled particle scripts (the classic
 * VectorVM bytecode saved in the .uasset, see backend/preview/uasset/niagara_vm.py) and
 * draws the particles as glowing points. CPU emitters only; an emitter using an op or an
 * external function this file does not know is skipped and says why.
 * UassetNiagaraSim.create(canvas, emitters) -> { start(), stop(), restart(), state() } */
(function () {
  "use strict";

  var OPS = ("done add sub mul div mad lerp rcp rsq sqrt neg abs exp exp2 log log2 sin cos tan asin acos atan atan2 " +
    "ceil floor fmod frac trunc clamp min max pow round sign step random noise cmplt cmple cmpgt cmpge cmpeq " +
    "cmpneq select addi subi muli divi clampi mini maxi absi negi signi randomi cmplti cmplei cmpgti cmpgei " +
    "cmpeqi cmpneqi bit_and bit_or bit_xor bit_not bit_lshift bit_rshift logic_and logic_or logic_xor logic_not " +
    "f2i i2f f2b b2f i2b b2i inputdata_float inputdata_int32 inputdata_half inputdata_noadvance_float " +
    "inputdata_noadvance_int32 inputdata_noadvance_half outputdata_float outputdata_int32 outputdata_half " +
    "acquireindex external_func_call exec_index noise2D noise3D enter_stat_scope exit_stat_scope update_id " +
    "acquire_id half_to_float").split(" ");
  var SOURCES = {};
  ["rcp rsq sqrt neg abs exp exp2 log log2 sin cos tan asin acos atan ceil floor frac trunc round sign absi negi signi " +
   "bit_not logic_not f2i i2f f2b b2f i2b b2i random randomi",
   "add sub mul div atan2 fmod min max pow step cmplt cmple cmpgt cmpge cmpeq cmpneq addi subi muli divi mini maxi " +
   "cmplti cmplei cmpgti cmpgei cmpeqi cmpneqi bit_and bit_or bit_xor bit_lshift bit_rshift logic_and logic_or logic_xor",
   "mad lerp clamp select clampi"].forEach(function (names, i) { names.split(" ").forEach(function (n) { SOURCES[n] = i + 1; }); });
  var INT_OPS = /^(addi|subi|muli|divi|clampi|mini|maxi|absi|negi|signi|randomi|cmplti|cmplei|cmpgti|cmpgei|cmpeqi|cmpneqi|bit_|logic_|i2f|i2b)/;

  function b64(text) { var s = atob(text), out = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i); return out; }

  /* Bytecode -> instructions; throws on an op this file can't run. */
  /* External functions this file can run: (inputs, external entry) -> outputs. */
  function lutSample(ext, x, stride, k) {
    var n = ext.samples, span = (ext.max - ext.min) || 1;
    var t = Math.max(0, Math.min(1, (x - ext.min) / span)) * (n - 1), i = Math.floor(t), f = t - i, j = Math.min(n - 1, i + 1);
    return ext.lut[i * stride + k] * (1 - f) + ext.lut[j * stride + k] * f;
  }
  var EXTERNALS = {
    SampleCurve: function (a, ext) { return [lutSample(ext, a[0], 1, 0)]; },
    SampleVectorCurve: function (a, ext) { return [0, 1, 2].map(function (k) { return lutSample(ext, a[0], 3, k); }); },
    SampleColorCurve: function (a, ext) { return [0, 1, 2, 3].map(function (k) { return lutSample(ext, a[0], 4, k); }); },
    GetSubUVDetails: function (a, ext) { var s = ext.sub_image || [1, 1]; return [s[0], s[1], 0]; },
    FastMatrixToQuaternion: function (m) {
      // rows of a 3x3 rotation in the first 12 inputs (4 per row) -> x, y, z, w
      var m00 = m[0], m11 = m[5], m22 = m[10], tr = m00 + m11 + m22, q;
      if (tr > 0) { var s0 = 0.5 / Math.sqrt(tr + 1); q = [(m[6] - m[9]) * s0, (m[8] - m[2]) * s0, (m[1] - m[4]) * s0, 0.25 / s0]; }
      else if (m00 > m11 && m00 > m22) { var s1 = 2 * Math.sqrt(1 + m00 - m11 - m22); q = [0.25 * s1, (m[4] + m[1]) / s1, (m[8] + m[2]) / s1, (m[6] - m[9]) / s1]; }
      else if (m11 > m22) { var s2 = 2 * Math.sqrt(1 + m11 - m00 - m22); q = [(m[4] + m[1]) / s2, 0.25 * s2, (m[9] + m[6]) / s2, (m[8] - m[2]) / s2]; }
      else { var s3 = 2 * Math.sqrt(1 + m22 - m00 - m11); q = [(m[8] + m[2]) / s3, (m[9] + m[6]) / s3, 0.25 * s3, (m[1] - m[4]) / s3]; }
      return q;
    }
  };

  function decode(bytes, externals) {
    externals = externals || [];
    var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), pc = 0, out = [];
    function u16() { var v = dv.getUint16(pc, true); pc += 2; return v; }
    while (pc < bytes.length) {
      var name = OPS[bytes[pc++]];
      if (!name) throw new Error("unknown op " + bytes[pc - 1]);
      var ins = { op: name };
      if (name === "done") { out.push(ins); return out; }
      if (name === "exec_index") ins.dest = u16();
      else if (name === "enter_stat_scope") u16();
      else if (name === "exit_stat_scope") continue;
      else if (name === "inputdata_float" || name === "inputdata_int32" || name === "inputdata_noadvance_float" || name === "inputdata_noadvance_int32") {
        ins.set = u16(); ins.comp = u16(); ins.dest = u16();
      } else if (name === "outputdata_float" || name === "outputdata_int32") {
        ins.types = bytes[pc++]; ins.set = u16(); ins.index = u16(); ins.src = u16(); ins.comp = u16();
      } else if (name === "acquireindex") {
        ins.types = bytes[pc++]; ins.set = u16(); ins.src = u16(); ins.dest = u16();
      } else if (SOURCES[name]) {
        ins.types = bytes[pc++]; ins.srcs = [];
        for (var k = 0; k < SOURCES[name]; k++) ins.srcs.push(u16());
        ins.dest = u16();
      } else if (name === "external_func_call") {
        var ext = externals[u16()];
        if (!ext) throw new Error("it calls an external function the file does not list");
        if (!EXTERNALS[ext.name] || (/Curve$/.test(ext.name) && !ext.lut)) throw new Error("it calls " + ext.name + " (" + (ext.interface || ext.owner) + "), not supported yet");
        ins.fn = EXTERNALS[ext.name]; ins.ext = ext; ins.ins = []; ins.outs = [];
        for (var e = 0; e < ext.inputs.length; e++) ins.ins.push(u16());
        for (e = 0; e < ext.outputs; e++) ins.outs.push(u16());
      } else throw new Error("op " + name + " is not supported yet");
      if (name !== "exit_stat_scope" && name !== "enter_stat_scope") out.push(ins);
    }
    return out;
  }

  /* One compiled script, ready to run over a batch of instances. */
  function Program(script) {
    this.code = decode(b64(script.code), script.externals);
    var raw = b64(script.constants);
    this.constBytes = new ArrayBuffer(raw.length);
    new Uint8Array(this.constBytes).set(raw);
    this.cf = new Float32Array(this.constBytes);
    this.ci = new Int32Array(this.constBytes);
    this.half = script.half; this.interpolated = script.interpolated; this.params = script.params || {};
    this.engine = script.engine != null ? script.engine : 160; // particle scripts: 160 engine floats; system scripts: 8
    var regs = new ArrayBuffer(4 * Math.max(1, script.temps));
    this.rf = new Float32Array(regs); this.ri = new Int32Array(regs);
  }
  Program.prototype.setParam = function (suffix, value, isInt) {
    var copies = this.interpolated ? [0, this.half] : [0];
    for (var name in this.params) {
      if (name === suffix || name.slice(-suffix.length - 1) === "." + suffix) {
        var off = this.params[name][0];
        copies.forEach(function (c) { if (isInt) this.ci[off + c] = value; else this.cf[off + c] = value; }, this);
      }
    }
  };
  Program.prototype.setEngine = function (engine) {
    var copies = this.interpolated ? [0, this.half] : [0];
    for (var c = 0; c < copies.length; c++) {
      for (var i = 0; i < this.engine; i++) this.ci[copies[c] + i] = engine.i[i];
    }
  };
  /* Runs the script for `count` instances. input: a data set {f: [Float32Array per comp], i: [...]},
   * or a list of them by data set index (a system script reads its parameters from set 1), or null;
   * output: {f, i, count} filled at acquired indexes. */
  Program.prototype.run = function (count, input, output) {
    var code = this.code, rf = this.rf, ri = this.ri, cf = this.cf, ci = this.ci;
    var sets = Array.isArray(input) ? input : [input];
    for (var n = 0; n < count; n++) {
      for (var p = 0; p < code.length; p++) {
        var s = code[p], op = s.op, t = s.types, a, b, c, d = s.dest;
        if (s.srcs) {
          var isInt = INT_OPS.test(op) || op === "select" && false;
          var src = s.srcs;
          if (isInt || op === "select" || op === "f2b" || op === "b2f" || op === "b2i") {
            a = (t & 1) ? ci[src[0] >> 2] : ri[src[0]];
            if (src.length > 1) b = (t & 2) ? ci[src[1] >> 2] : ri[src[1]];
            if (src.length > 2) c = (t & 4) ? ci[src[2] >> 2] : ri[src[2]];
          } else {
            a = (t & 1) ? cf[src[0] >> 2] : rf[src[0]];
            if (src.length > 1) b = (t & 2) ? cf[src[1] >> 2] : rf[src[1]];
            if (src.length > 2) c = (t & 4) ? cf[src[2] >> 2] : rf[src[2]];
          }
        }
        switch (op) {
          case "add": rf[d] = a + b; break;
          case "sub": rf[d] = a - b; break;
          case "mul": rf[d] = a * b; break;
          case "div": rf[d] = b !== 0 ? a / b : 0; break;
          case "mad": rf[d] = a * b + c; break;
          case "lerp": rf[d] = a + (b - a) * c; break;
          case "rcp": rf[d] = a !== 0 ? 1 / a : 0; break;
          case "rsq": rf[d] = a > 0 ? 1 / Math.sqrt(a) : 0; break;
          case "sqrt": rf[d] = a > 0 ? Math.sqrt(a) : 0; break;
          case "neg": rf[d] = -a; break;
          case "abs": rf[d] = Math.abs(a); break;
          case "exp": rf[d] = Math.exp(a); break;
          case "exp2": rf[d] = Math.pow(2, a); break;
          case "log": rf[d] = a > 0 ? Math.log(a) : 0; break;
          case "log2": rf[d] = a > 0 ? Math.log2(a) : 0; break;
          case "sin": rf[d] = Math.sin(a); break;
          case "cos": rf[d] = Math.cos(a); break;
          case "tan": rf[d] = Math.tan(a); break;
          case "asin": rf[d] = Math.asin(Math.max(-1, Math.min(1, a))); break;
          case "acos": rf[d] = Math.acos(Math.max(-1, Math.min(1, a))); break;
          case "atan": rf[d] = Math.atan(a); break;
          case "atan2": rf[d] = Math.atan2(a, b); break;
          case "ceil": rf[d] = Math.ceil(a); break;
          case "floor": rf[d] = Math.floor(a); break;
          case "fmod": rf[d] = b !== 0 ? a % b : 0; break;
          case "frac": rf[d] = a - Math.floor(a); break;
          case "trunc": rf[d] = Math.trunc(a); break;
          case "clamp": rf[d] = Math.min(Math.max(a, b), c); break;
          case "min": rf[d] = Math.min(a, b); break;
          case "max": rf[d] = Math.max(a, b); break;
          case "pow": rf[d] = Math.pow(a, b); break;
          case "round": rf[d] = Math.round(a); break;
          case "sign": rf[d] = Math.sign(a); break;
          case "step": rf[d] = b >= a ? 1 : 0; break;
          case "random": rf[d] = Math.random() * a; break;
          case "noise": rf[d] = 0; break;
          case "cmplt": ri[d] = a < b ? -1 : 0; break;
          case "cmple": ri[d] = a <= b ? -1 : 0; break;
          case "cmpgt": ri[d] = a > b ? -1 : 0; break;
          case "cmpge": ri[d] = a >= b ? -1 : 0; break;
          case "cmpeq": ri[d] = a === b ? -1 : 0; break;
          case "cmpneq": ri[d] = a !== b ? -1 : 0; break;
          case "select": ri[d] = a !== 0 ? b : c; break; // copies the bits, float or int
          case "addi": ri[d] = (a + b) | 0; break;
          case "subi": ri[d] = (a - b) | 0; break;
          case "muli": ri[d] = Math.imul(a, b); break;
          case "divi": ri[d] = b !== 0 ? (a / b) | 0 : 0; break;
          case "clampi": ri[d] = Math.min(Math.max(a, b), c); break;
          case "mini": ri[d] = Math.min(a, b); break;
          case "maxi": ri[d] = Math.max(a, b); break;
          case "absi": ri[d] = Math.abs(a) | 0; break;
          case "negi": ri[d] = -a | 0; break;
          case "signi": ri[d] = Math.sign(a); break;
          case "randomi": ri[d] = Math.floor(Math.random() * a) | 0; break;
          case "cmplti": ri[d] = a < b ? -1 : 0; break;
          case "cmplei": ri[d] = a <= b ? -1 : 0; break;
          case "cmpgti": ri[d] = a > b ? -1 : 0; break;
          case "cmpgei": ri[d] = a >= b ? -1 : 0; break;
          case "cmpeqi": ri[d] = a === b ? -1 : 0; break;
          case "cmpneqi": ri[d] = a !== b ? -1 : 0; break;
          case "bit_and": case "logic_and": ri[d] = a & b; break;
          case "bit_or": case "logic_or": ri[d] = a | b; break;
          case "bit_xor": case "logic_xor": ri[d] = a ^ b; break;
          case "bit_not": case "logic_not": ri[d] = ~a; break;
          case "bit_lshift": ri[d] = a << b; break;
          case "bit_rshift": ri[d] = a >> b; break;
          case "f2i": ri[d] = Math.trunc((t & 1) ? cf[s.srcs[0] >> 2] : rf[s.srcs[0]]) | 0; break;
          case "i2f": rf[d] = a; break;
          case "f2b": ri[d] = ((t & 1) ? cf[s.srcs[0] >> 2] : rf[s.srcs[0]]) !== 0 ? -1 : 0; break;
          case "b2f": rf[d] = a !== 0 ? 1 : 0; break;
          case "i2b": ri[d] = a !== 0 ? -1 : 0; break;
          case "b2i": ri[d] = a !== 0 ? 1 : 0; break;
          case "exec_index": ri[d] = n; break;
          case "inputdata_float": { var sf = sets[s.set]; rf[d] = sf && sf.f[s.comp] ? sf.f[s.comp][sf.count === 1 ? 0 : n] : 0; break; }
          case "inputdata_int32": { var si = sets[s.set]; ri[d] = si && si.i[s.comp] ? si.i[s.comp][si.count === 1 ? 0 : n] : 0; break; }
          case "inputdata_noadvance_float": { var nf = sets[s.set]; rf[d] = nf && nf.f[s.comp] ? nf.f[s.comp][0] : 0; break; }
          case "inputdata_noadvance_int32": { var ni = sets[s.set]; ri[d] = ni && ni.i[s.comp] ? ni.i[s.comp][0] : 0; break; }
          case "acquireindex": ri[d] = ((t & 1) ? ci[s.src >> 2] : ri[s.src]) !== 0 ? output.count++ : -1; break;
          case "outputdata_float": {
            var at = ri[s.index];
            if (at >= 0 && output.f[s.comp]) output.f[s.comp][at] = (t & 1) ? cf[s.src >> 2] : rf[s.src];
            break;
          }
          case "outputdata_int32": {
            var ati = ri[s.index];
            if (ati >= 0 && output.i[s.comp]) output.i[s.comp][ati] = (t & 1) ? ci[s.src >> 2] : ri[s.src];
            break;
          }
          case "external_func_call": {
            var args = [];
            for (var q = 0; q < s.ins.length; q++) {
              var v = s.ins[q];
              args.push(s.ext.inputs[q] ? cf[(v & 0x7fff) >> 2] : rf[v & 0x7fff]); // flag bit: a register
            }
            var res = s.fn(args, s.ext);
            for (q = 0; q < s.outs.length; q++) if (s.outs[q] !== 0xffff) rf[s.outs[q]] = res[q] || 0;
            break;
          }
          case "done": p = code.length; break;
        }
      }
    }
  };

  function DataSet(layout, capacity) {
    var floats = 0, ints = 0;
    layout.forEach(function (a) { floats = Math.max(floats, a.f + a.nf); ints = Math.max(ints, a.i + a.ni); });
    this.f = []; this.i = []; this.count = 0; this.capacity = capacity;
    for (var k = 0; k < floats; k++) this.f.push(new Float32Array(capacity));
    for (k = 0; k < ints; k++) this.i.push(new Int32Array(capacity));
  }

  /* One emitter: spawn by rate / burst, run update then spawn, keep the survivors. */
  function Emitter(def) {
    this.name = def.name;
    this.spawn = new Program(def.spawn);
    this.update = new Program(def.update);
    this.layout = def.layout;
    this.capacity = 4096;
    this.data = new DataSet(this.layout, this.capacity);
    this.spare = new DataSet(this.layout, this.capacity); // the update writes here, then the two swap
    this.born = null;
    this.rate = def.rate || 0; this.burst = def.burst || 0; this.burstTime = def.burst_time || 0;
    this.loop = def.loop > 0 && def.loop < 100000 ? def.loop : 0;
    this.color = def.color || null; this.renderer = def.renderer || "Sprite";
    this.sprite = def.sprite || null;
    if (this.sprite && typeof Image !== "undefined") {
      var self = this, img = new Image();
      img.onload = function () { self.image = maskFromLuminance(img); };
      img.src = this.sprite.icon;
    }
    this.acc = 0; this.age = 0; this.burstDone = false; this.total = 0;
    var by = {};
    this.layout.forEach(function (a) { by[a.name] = a; });
    this.attr = by;
  }
  Emitter.prototype.step = function (dt, engine, infos) {
    var e = engine;
    e.i[152] = this.data.count; e.i[153] = this.total; e.f[155] = this.age;
    this.spawn.setEngine(e); this.update.setEngine(e);
    // update the living particles
    if (this.data.count) { // no new arrays per frame: the survivors go to the spare set, which becomes current
      var next = this.spare;
      next.count = 0;
      this.update.run(this.data.count, this.data, next);
      this.spare = this.data;
      this.data = next;
    }
    if (infos) { // the system script's SpawnOutputInfo: [count, start dt, interval, group] per spawn module
      var self = this;
      infos.forEach(function (info) { if (info[0] > 0) self.birth(Math.min(info[0], self.capacity - self.data.count), info[1], info[2], info[3]); });
      this.age += dt;
      return;
    }
    // no system script to run: guess from the spawn rate / burst inputs
    var count = 0, interval = 0, start = 0;
    if (this.rate > 0) {
      var before = this.acc;
      this.acc += this.rate * dt;
      count = Math.floor(this.acc);
      this.acc -= count;
      interval = 1 / this.rate;
      start = (1 - before) / this.rate;
    }
    if (this.burst > 0 && !this.burstDone && this.age >= this.burstTime) { count += this.burst; this.burstDone = true; start = 0; interval = this.rate > 0 ? interval : 0; }
    count = Math.min(count, this.capacity - this.data.count);
    if (count > 0) this.birth(count, Math.min(start, dt), interval, 0);
    this.age += dt;
    if (this.loop && this.age >= this.loop) { this.age -= this.loop; this.burstDone = false; }
  };
  Emitter.prototype.birth = function (count, start, interval, group) {
    this.spawn.setParam("SpawnRate", interval > 0 ? 1 / interval : 0);
    this.spawn.setParam("SpawnInterval", interval);
    this.spawn.setParam("InterpSpawnStartDt", start);
    this.spawn.setParam("SpawnGroup", group, true);
    this.spawn.setParam("ExecutionCount", count, true);
    if (!this.born || this.born.capacity < count) this.born = new DataSet(this.layout, Math.max(64, count));
    var born = this.born;
    born.count = 0;
    this.spawn.run(count, null, born);
    this.append(born);
    this.total += count;
  };
  Emitter.prototype.append = function (born) {
    var d = this.data, n = Math.min(born.count, d.capacity - d.count);
    for (var k = 0; k < d.f.length; k++) d.f[k].set(born.f[k].subarray(0, n), d.count);
    for (k = 0; k < d.i.length; k++) d.i[k].set(born.i[k].subarray(0, n), d.count);
    d.count += n;
  };
  Emitter.prototype.value = function (name, k, n, fallback) {
    var a = this.attr[name];
    return a && !a.int ? this.data.f[a.offset + k][n] : fallback;
  };

  function System(def, emitters) {
    this.spawn = new Program(def.spawn); this.update = new Program(def.update);
    this.layout = def.layout; this.paramLayout = def.params; this.emitters = emitters;
    this.params = new DataSet(this.paramLayout, 1); this.params.count = 1;
    var self = this;
    this.paramLayout.forEach(function (a) { // User values (and any saved default)
      if (!a.value) return;
      var raw = b64(a.value), dv = new DataView(raw.buffer);
      for (var k = 0; k < a.nf; k++) if (4 * k + 3 < raw.length) self.params.f[a.f + k][0] = dv.getFloat32(4 * k, true);
      for (k = 0; k < a.ni; k++) if (4 * (a.nf + k) + 3 < raw.length) self.params.i[a.i + k][0] = dv.getInt32(4 * (a.nf + k), true);
    });
    this.infos = {};
    emitters.forEach(function (em) {
      self.infos[em.name] = self.layout.filter(function (a) { return a.name.indexOf(em.name + ".") === 0 && /\.SpawnOutputInfo$/.test(a.name); });
    });
    this.data = null;
  }
  System.prototype.fillParams = function () {
    var p = this.params, emitters = this.emitters;
    this.paramLayout.forEach(function (a) {
      var name = a.name, m = /^Engine\.([^.]+)\.(NumParticles|SpawnCountScale|TotalSpawnedParticles)$/.exec(name);
      if (m) {
        var em = emitters.filter(function (e) { return e.name === m[1]; })[0];
        if (m[2] === "SpawnCountScale") p.f[a.f][0] = 1;
        else if (em) p.i[a.i][0] = m[2] === "NumParticles" ? em.data.count : em.total;
      }
    });
  };
  System.prototype.step = function (dt, engine) {
    this.spawn.setEngine(engine); this.update.setEngine(engine);
    this.fillParams();
    var next = new DataSet(this.layout, 1);
    if (!this.data) this.spawn.run(1, [null, this.params], next);
    else this.update.run(1, [this.data, this.params], next);
    if (next.count) this.data = next;
    var out = {}, data = this.data;
    for (var name in this.infos) {
      out[name] = this.infos[name].map(function (a) {
        return data ? [data.i[a.i][0], data.f[a.f][0], data.f[a.f + 1][0], data.i[a.i + 1][0]] : [0, 0, 0, 0];
      });
    }
    return out;
  };

  function engineBlock() {
    var buf = new ArrayBuffer(160 * 4), f = new Float32Array(buf), i = new Int32Array(buf);
    for (var m = 0; m < 6; m++) for (var d = 0; d < 4; d++) f[24 + m * 16 + d * 5] = 1; // six identity matrices
    f[123] = 1; // rotation quaternion w
    f[132] = 1; f[137] = 1; f[142] = 1; // X, Y, Z axes
    f[144] = 1; f[145] = 1; f[146] = 1; // scale
    f[154] = 1; // spawn count scale
    i[5] = 3; // quality level
    return { f: f, i: i };
  }

  /* Particle textures are often grey masks with no alpha: then their brightness is the opacity
   * (white where the smoke is), so the black background does not draw a square. */
  function maskFromLuminance(img) {
    var c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight;
    var x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(img, 0, 0);
    try {
      var data = x.getImageData(0, 0, c.width, c.height), px = data.data, opaque = true;
      for (var o = 3; o < px.length; o += 4) if (px[o] < 250) { opaque = false; break; }
      if (opaque) {
        for (o = 0; o < px.length; o += 4) {
          var lum = Math.max(px[o], px[o + 1], px[o + 2]);
          px[o + 3] = lum;
          if (lum) { px[o] = Math.min(255, px[o] * 255 / lum); px[o + 1] = Math.min(255, px[o + 1] * 255 / lum); px[o + 2] = Math.min(255, px[o + 2] * 255 / lum); }
        }
        x.putImageData(data, 0, 0);
      }
    } catch (e) { /* pixels not readable: draw it as it is */ }
    c.naturalWidth = c.width; c.naturalHeight = c.height;
    return c;
  }

  function tone(v) { return Math.round(255 * (1 - Math.exp(-Math.max(0, v) * 1.5))); }

  function create(canvas, defs, systemDef) {
    var emitters = [], skipped = [], system = null, systemWhy = "";
    defs.forEach(function (d) {
      try { emitters.push(new Emitter(d)); }
      catch (err) { skipped.push({ name: d.name, why: err.message || String(err) }); }
    });
    if (systemDef && emitters.length) {
      try { system = new System(systemDef, emitters); }
      catch (err) { systemWhy = err.message || String(err); }
    }
    var engine = engineBlock(), raf = 0, last = 0, time = 0, frames = 0, yaw = 0.6;
    var view = { cx: 0, cy: 0, cz: 0, radius: 100 };

    function frame(now) {
      raf = requestAnimationFrame(frame);
      var dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
      if (last && now - last < 1000 / 60 - 2) return; // 60 images a second, whatever the screen
      last = now;
      advance(dt);
      draw(dt);
    }

    function advance(dt) {
      time += dt; frames++;
      engine.f[0] = dt; engine.f[1] = dt; engine.f[2] = 1 / dt; engine.f[3] = time; engine.f[4] = time;
      var infos = null;
      if (system) { try { infos = system.step(dt, engine); } catch (err) { systemWhy = err.message || String(err); system = null; } }
      emitters.forEach(function (em) { if (em.failed) return; try { em.step(dt, engine, infos && infos[em.name] && infos[em.name].length ? infos[em.name] : null); } catch (err) { em.failed = err.message || String(err); } });
    }

    function draw(dt) {
      var w = canvas.clientWidth || 300, h = canvas.clientHeight || 200, dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      var g = canvas.getContext("2d");
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.globalCompositeOperation = "source-over";
      g.fillStyle = "#050507"; g.fillRect(0, 0, canvas.width, canvas.height);
      // frame the particles: follow their bounds smoothly
      var min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity], any = false;
      emitters.forEach(function (em) {
        var pos = em.attr.Position; if (!pos) return;
        for (var n = 0; n < em.data.count; n++) for (var k = 0; k < 3; k++) {
          var v = em.data.f[pos.offset + k][n]; if (v < min[k]) min[k] = v; if (v > max[k]) max[k] = v; any = true;
        }
      });
      if (any) {
        var r = Math.max(20, Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2), ease = frames < 20 ? 1 : Math.min(1, dt * 1.5);
        view.cx += ((min[0] + max[0]) / 2 - view.cx) * ease; view.cy += ((min[1] + max[1]) / 2 - view.cy) * ease;
        view.cz += ((min[2] + max[2]) / 2 - view.cz) * ease; view.radius += (r - view.radius) * ease;
      }
      yaw += dt * 0.25;
      var pitch = 0.35, cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      var dist = view.radius * 1.9, focal = Math.min(canvas.width, canvas.height) * 0.75;
      // project every particle, then draw far to near (translucent sprites need that order)
      var list = [];
      emitters.forEach(function (em) {
        var pos = em.attr.Position; if (!pos || em.failed) return;
        for (var n = 0; n < em.data.count; n++) {
          // Unreal: X forward, Y right, Z up; orbit around the Z axis
          var x = em.data.f[pos.offset][n] - view.cx, y = em.data.f[pos.offset + 1][n] - view.cy, z = em.data.f[pos.offset + 2][n] - view.cz;
          var rx = x * cy - y * sy, ry = x * sy + y * cy;
          var depth = rx * cp + z * sp + dist, up = z * cp - rx * sp;
          if (depth > 1) list.push({ em: em, n: n, depth: depth, px: canvas.width / 2 + ry * focal / depth, py: canvas.height / 2 - up * focal / depth });
        }
      });
      list.sort(function (a, b) { return b.depth - a.depth; });
      for (var q = 0; q < list.length; q++) drawParticle(g, list[q], focal, dpr);
    }

    function drawParticle(g, it, focal, dpr) {
      var em = it.em, n = it.n;
      var size = em.value("SpriteSize", 0, n, em.attr.Scale ? 15 * em.value("Scale", 0, n, 1) : 15);
      var half = Math.max(2.5 * dpr, Math.abs(size) * 0.5 * focal / it.depth);
      var cr = em.value("Color", 0, n, em.color ? em.color[0] : 1), cg = em.value("Color", 1, n, em.color ? em.color[1] : 1);
      var cb = em.value("Color", 2, n, em.color ? em.color[2] : 1), ca = Math.max(0, Math.min(1, em.value("Color", 3, n, 1)));
      var img = em.image || null;
      if (!img) { // no texture: a soft glowing dot
        g.globalCompositeOperation = "lighter"; g.globalAlpha = 1;
        var grad = g.createRadialGradient(it.px, it.py, 0, it.px, it.py, half * 1.2);
        grad.addColorStop(0, "rgba(" + tone(cr) + "," + tone(cg) + "," + tone(cb) + "," + ca + ")");
        grad.addColorStop(1, "rgba(0,0,0,0)");
        g.fillStyle = grad; g.fillRect(it.px - half * 1.2, it.py - half * 1.2, half * 2.4, half * 2.4);
        return;
      }
      // the sprite's texture, tinted by the particle colour, on its flipbook cell, turned
      var grid = em.sprite.grid, cols = Math.max(1, grid[0] | 0), rows = Math.max(1, grid[1] | 0);
      var cell = Math.floor(em.value("SubImageIndex", 0, n, 0)) % (cols * rows); if (cell < 0) cell += cols * rows;
      var sw = img.naturalWidth / cols, sh = img.naturalHeight / rows, sx = (cell % cols) * sw, sy = Math.floor(cell / cols) * sh;
      var source = tinted(em, img, cr, cg, cb);
      g.globalCompositeOperation = em.sprite.blend === "Additive" ? "lighter" : "source-over";
      g.globalAlpha = ca;
      g.save();
      g.translate(it.px, it.py);
      g.rotate(-em.value("SpriteRotation", 0, n, 0) * Math.PI / 180);
      g.drawImage(source, sx, sy, sw, sh, -half, -half, 2 * half, 2 * half);
      g.restore();
      g.globalAlpha = 1;
    }

    /* The texture multiplied by a colour (cached per colour, in steps). */
    function tinted(em, img, r, gr, b) {
      var key = [r, gr, b].map(function (v) { return Math.round(Math.max(0, Math.min(4, v)) * 8); }).join(",");
      if (key === "8,8,8") return img;
      em.tints = em.tints || {};
      if (!em.tints[key]) {
        if (Object.keys(em.tints).length >= 32) em.tints = {}; // a colour that changes over life must not grow this forever
        var c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight;
        var x = c.getContext("2d");
        x.drawImage(img, 0, 0);
        x.globalCompositeOperation = "multiply";
        x.fillStyle = "rgb(" + tone(r) + "," + tone(gr) + "," + tone(b) + ")";
        x.fillRect(0, 0, c.width, c.height);
        x.globalCompositeOperation = "destination-in";
        x.drawImage(img, 0, 0);
        em.tints[key] = c;
      }
      return em.tints[key];
    }

    return {
      start: function () { if (!raf) { last = 0; raf = requestAnimationFrame(frame); } },
      stop: function () { cancelAnimationFrame(raf); raf = 0; },
      restart: function () {
        emitters.forEach(function (em) { em.data.count = 0; em.spare.count = 0; em.age = 0; em.acc = 0; em.burstDone = false; em.total = 0; em.failed = null; });
        if (system) system.data = null;
        time = 0;
      },
      state: function () {
        return { frames: frames, system: system ? "running" : (systemWhy || "none"), emitters: emitters.map(function (em) { return { name: em.name, particles: em.data.count, failed: em.failed || "" }; }), skipped: skipped };
      },
      advance: advance,
      particles: function (name) { var em = emitters.filter(function (e) { return e.name === name; })[0]; return em ? em.data : null; },
      skipped: skipped, running: emitters.length
    };
  }

  window.UassetNiagaraSim = { create: create, decode: decode };
})();
