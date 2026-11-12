/* UI material renderer: turns a material node graph (backend/preview/uasset/material.py)
 * into a fragment shader and draws it on the GPU. A graph with a node this file does
 * not know is refused (compile() returns an error) so the caller can fall back. */
(function () {
  "use strict";

  var TYPES = ["", "float", "vec2", "vec3", "vec4"];
  function num(v) { var s = String(+v || 0); return /[.e]/.test(s) ? s : s + ".0"; }
  function lit(values) {
    return values.length === 1 ? num(values[0]) : TYPES[values.length] + "(" + values.map(num).join(",") + ")";
  }

  function compile(graph) {
    var lines = [], cache = {}, busy = {}, uniforms = [], uniformIndex = {}, counter = 0;
    var usesTime = false, textures = [];
    var over = graph.overrides || { scalar: {}, vector: {}, texture: {} };

    function tmp(dim, expr) {
      var name = "t" + (counter++);
      lines.push("  " + TYPES[dim] + " " + name + " = " + expr + ";");
      return { e: name, d: dim };
    }
    function uniform(kind, key, type, value) {
      var id = kind + ":" + key;
      if (!(id in uniformIndex)) {
        uniformIndex[id] = "u" + uniforms.length;
        uniforms.push({ name: uniformIndex[id], kind: kind, key: key, type: type, value: value });
      }
      return uniformIndex[id];
    }
    function cast(v, dim) {
      if (v.d === dim) return v;
      if (v.d === 1) return { e: TYPES[dim] + "(" + v.e + ")", d: dim };
      if (v.d > dim) return { e: v.e + "." + "xyzw".slice(0, dim), d: dim };
      var padding = []; for (var i = v.d; i < dim; i++) padding.push("0.0");
      return { e: TYPES[dim] + "(" + v.e + "," + padding.join(",") + ")", d: dim };
    }
    function pair(a, b) { // Unreal: a scalar spreads over a vector; unequal vectors keep the shared part
      if (a.d === b.d || a.d === 1 || b.d === 1) return [a, b, Math.max(a.d, b.d)];
      var d = Math.min(a.d, b.d);
      return [cast(a, d), cast(b, d), d];
    }
    function same(a, b) { var p = pair(a, b); return [cast(p[0], p[2]), cast(p[1], p[2]), p[2]]; }
    function pick(v, output) { // outputs of colour-like nodes: RGB, R, G, B, A, RGBA
      if (output === 0) return cast(v, 3);
      if (output >= 1 && output <= 4) return { e: v.e + "." + "rgba"[output - 1], d: 1 };
      return v;
    }
    function wired(node, pin) { var w = node.p[pin]; return !!(w && typeof w === "object" && w.node > 0); }
    function input(node, pin, fallback) {
      var w = node.p[pin];
      if (!w || typeof w !== "object" || !(w.node > 0)) return fallback;
      return masked(emit(String(w.node), w.output || 0), w.mask);
    }
    function masked(v, mask) {
      if (!mask) return v;
      var parts = mask.split("").filter(function (c) { return "rgba".indexOf(c) < v.d; });
      if (!parts.length) return v;
      return tmp(parts.length, v.e + "." + parts.join(""));
    }
    function constant(node, name, fallback) { return { e: num(node.p[name] == null ? fallback : node.p[name]), d: 1 }; }
    function uv() { return { e: "v_uv", d: 2 }; }

    function binary(node, op, a0, b0) {
      var p = pair(input(node, "A", constant(node, "ConstA", a0)), input(node, "B", constant(node, "ConstB", b0)));
      return tmp(p[2], "(" + p[0].e + " " + op + " " + p[1].e + ")");
    }
    function call2(node, fn, a0, b0) {
      var p = same(input(node, "A", constant(node, "ConstA", a0)), input(node, "B", constant(node, "ConstB", b0)));
      return tmp(p[2], fn + "(" + p[0].e + "," + p[1].e + ")");
    }
    function unary(node, make) { var v = input(node, "Input", { e: "0.0", d: 1 }); return tmp(v.d, make(v.e, v.d)); }
    function sampler(node) {
      var name = node.p.ParameterName, key = (name && over.texture[name]) || (node.p.Texture && node.p.Texture.asset) || "";
      if (!key) throw new Error("texture node without a texture");
      if (textures.indexOf(key) < 0) textures.push(key);
      var coords = cast(input(node, "Coordinates", uv()), 2);
      var u = uniform("texture", key, "sampler2D", key);
      return tmp(4, "toLinear(texture2D(" + u + "," + coords.e + "))");
    }

    function emit(id, output) {
      var key = id + ":" + output;
      if (cache[key]) return cache[key];
      if (busy[id]) throw new Error("the material graph loops on itself");
      var node = graph.nodes[id];
      if (!node) throw new Error("a wire leads to a missing node");
      busy[id] = true;
      var out = build(node, output);
      busy[id] = false;
      return (cache[key] = out);
    }

    function build(node, output) {
      var p = node.p, a, b, c, v, q;
      switch (node.op) {
        case "Constant": return { e: num(p.R), d: 1 };
        case "Constant2Vector": return { e: lit([p.R, p.G]), d: 2 };
        case "Constant3Vector": return pick({ e: lit((p.Constant || [0, 0, 0, 0]).slice(0, 4)), d: 4 }, output);
        case "Constant4Vector": return pick({ e: lit((p.Constant || [0, 0, 0, 0]).slice(0, 4)), d: 4 }, output === 0 ? 5 : output);
        case "ScalarParameter":
          v = p.ParameterName in over.scalar ? over.scalar[p.ParameterName] : (p.DefaultValue || 0);
          return { e: uniform("scalar", p.ParameterName, "float", v), d: 1 };
        case "VectorParameter":
          v = over.vector[p.ParameterName] || p.DefaultValue || [0, 0, 0, 1];
          return pick({ e: uniform("vector", p.ParameterName, "vec4", v), d: 4 }, output);
        case "VertexColor": return pick({ e: "u_tint", d: 4 }, output);
        case "Time": usesTime = true; return { e: "u_time", d: 1 };
        case "TextureCoordinate":
          return tmp(2, "v_uv * " + lit([p.UTiling == null ? 1 : p.UTiling, p.VTiling == null ? 1 : p.VTiling]));
        case "TextureSample": case "TextureSampleParameter2D": return pick(sampler(node), output);
        case "Add": return binary(node, "+", 0, 1);
        case "Subtract": return binary(node, "-", 1, 1);
        case "Multiply": return binary(node, "*", 0, 1);
        case "Divide": return binary(node, "/", 1, 2);
        case "Min": return call2(node, "min", 0, 1);
        case "Max": return call2(node, "max", 0, 1);
        case "Fmod": return call2(node, "mod", 0, 1);
        case "DotProduct": q = same(input(node, "A", { e: "0.0", d: 1 }), input(node, "B", { e: "0.0", d: 1 })); return tmp(1, "dot(" + q[0].e + "," + q[1].e + ")");
        case "Distance": q = same(input(node, "A", { e: "0.0", d: 1 }), input(node, "B", { e: "0.0", d: 1 })); return tmp(1, "distance(" + q[0].e + "," + q[1].e + ")");
        case "Abs": return unary(node, function (e) { return "abs(" + e + ")"; });
        case "Saturate": return unary(node, function (e) { return "clamp(" + e + ",0.0,1.0)"; });
        case "OneMinus": return unary(node, function (e) { return "(1.0 - " + e + ")"; });
        case "Frac": return unary(node, function (e) { return "fract(" + e + ")"; });
        case "Floor": return unary(node, function (e) { return "floor(" + e + ")"; });
        case "Ceil": return unary(node, function (e) { return "ceil(" + e + ")"; });
        case "Round": return unary(node, function (e) { return "floor(" + e + " + 0.5)"; });
        case "Sign": return unary(node, function (e) { return "sign(" + e + ")"; });
        case "SquareRoot": return unary(node, function (e) { return "sqrt(max(" + e + ",0.0))"; });
        case "Reroute": return input(node, "Input", { e: "0.0", d: 1 });
        case "Normalize": v = input(node, "VectorInput", { e: "vec3(0.0,0.0,1.0)", d: 3 }); return tmp(v.d, v.d === 1 ? "sign(" + v.e + ")" : "normalize(" + v.e + ")");
        case "Length": v = input(node, "Input", { e: "0.0", d: 1 }); return tmp(1, "length(" + v.e + ")");
        case "Sine": case "Cosine":
          q = p.Period == null ? 1 : p.Period;
          return unary(node, function (e) { return (node.op === "Sine" ? "sin(" : "cos(") + e + (q > 0 ? " * " + num(6.283185307179586 / q) : "") + ")"; });
        case "Arctangent": return unary(node, function (e) { return "atan(" + e + ")"; });
        case "Arctangent2": case "Arctangent2Fast":
          q = same(input(node, "Y", { e: "0.0", d: 1 }), input(node, "X", { e: "1.0", d: 1 }));
          return tmp(q[2], "atan(" + q[0].e + "," + q[1].e + ")");
        case "ConstantBiasScale":
          return unary(node, function (e) { return "((" + e + " + " + num(p.Bias == null ? 1 : p.Bias) + ") * " + num(p.Scale == null ? 0.5 : p.Scale) + ")"; });
        case "Power":
          a = input(node, "Base", { e: "0.0", d: 1 }); b = cast(input(node, "Exponent", constant(node, "ConstExponent", 2)), 1);
          return tmp(a.d, "pow(max(" + a.e + ",0.0)," + b.e + ")");
        case "LinearInterpolate":
          q = same(input(node, "A", constant(node, "ConstA", 0)), input(node, "B", constant(node, "ConstB", 1)));
          c = input(node, "Alpha", constant(node, "ConstAlpha", 0.5));
          return tmp(q[2], "mix(" + q[0].e + "," + q[1].e + "," + (c.d === 1 ? c.e : cast(c, q[2]).e) + ")");
        case "Clamp":
          v = input(node, "Input", { e: "0.0", d: 1 });
          a = cast(input(node, "Min", constant(node, "MinDefault", 0)), v.d); b = cast(input(node, "Max", constant(node, "MaxDefault", 1)), v.d);
          return tmp(v.d, "clamp(" + v.e + "," + a.e + "," + b.e + ")");
        case "Step":
          q = same(input(node, "Y", constant(node, "ConstY", 1)), input(node, "X", constant(node, "ConstX", 0)));
          return tmp(q[2], "step(" + q[0].e + "," + q[1].e + ")");
        case "SmoothStep":
          v = input(node, "Value", constant(node, "ConstValue", 0));
          a = cast(input(node, "Min", constant(node, "ConstMin", 0)), v.d); b = cast(input(node, "Max", constant(node, "ConstMax", 1)), v.d);
          return tmp(v.d, "smoothstep(" + a.e + "," + b.e + "," + v.e + ")");
        case "AppendVector":
          a = input(node, "A", { e: "0.0", d: 1 }); b = input(node, "B", { e: "0.0", d: 1 });
          if (a.d + b.d > 4) throw new Error("AppendVector makes more than 4 channels");
          return tmp(a.d + b.d, TYPES[a.d + b.d] + "(" + a.e + "," + b.e + ")");
        case "ComponentMask":
          v = input(node, "Input", { e: "0.0", d: 1 });
          q = ["R", "G", "B", "A"].map(function (ch, i) { return p[ch] && i < v.d ? "rgba"[i] : ""; }).join("");
          if (!q) throw new Error("ComponentMask keeps no channel");
          return tmp(q.length, v.d === 1 ? v.e : v.e + "." + q);
        case "If":
          a = cast(input(node, "A", { e: "0.0", d: 1 }), 1); b = cast(input(node, "B", constant(node, "ConstB", 0)), 1);
          var gt = input(node, "AGreaterThanB", { e: "0.0", d: 1 }), lt = input(node, "ALessThanB", { e: "0.0", d: 1 });
          var dim = Math.max(gt.d, lt.d), eq = wired(node, "AEqualsB") ? input(node, "AEqualsB", null) : null;
          if (eq) dim = Math.max(dim, eq.d);
          v = "(" + a.e + " >= " + b.e + " ? " + cast(gt, dim).e + " : " + cast(lt, dim).e + ")";
          if (eq) v = "(abs(" + a.e + " - " + b.e + ") <= " + num(p.EqualsThreshold == null ? 0.00001 : p.EqualsThreshold) + " ? " + cast(eq, dim).e + " : " + v + ")";
          return tmp(dim, v);
        case "Panner":
          usesTime = true;
          a = cast(input(node, "Coordinate", uv()), 2); b = cast(input(node, "Time", { e: "u_time", d: 1 }), 1);
          c = cast(input(node, "Speed", { e: lit([p.SpeedX || 0, p.SpeedY || 0]), d: 2 }), 2);
          return tmp(2, "(" + a.e + " + " + c.e + " * " + b.e + ")");
        default:
          throw new Error("node " + node.op + " is not supported");
      }
    }

    try {
      if (graph.domain !== "MD_UI") throw new Error("not a UI material");
      var pins = graph.pins || {};
      var pin = function (name, dim, fallback) {
        var w = pins[name];
        if (!w || w.use_constant || !(w.node > 0)) {
          if (w && w.use_constant) return { e: lit(dim === 1 ? [w.constant] : w.constant.slice(0, dim)), d: dim };
          return fallback;
        }
        return cast(masked(emit(String(w.node), w.output || 0), w.mask), dim);
      };
      var colour = pin("EmissiveColor", 3, { e: "vec3(0.0)", d: 3 });
      var alpha = { e: "1.0", d: 1 };
      if (graph.blend === "BLEND_Masked") { alpha = pin("OpacityMask", 1, alpha); alpha = { e: "step(0.3333, " + alpha.e + ")", d: 1 }; }
      else if (graph.blend !== "BLEND_Opaque") alpha = pin("Opacity", 1, alpha);
      var additive = graph.blend === "BLEND_Additive";
      var head = ["precision highp float;", "varying vec2 v_uv;", "uniform float u_time;", "uniform vec4 u_tint;"];
      uniforms.forEach(function (u) { head.push("uniform " + u.type + " " + u.name + ";"); });
      head.push("vec4 toLinear(vec4 c) { return vec4(pow(c.rgb, vec3(2.2)), c.a); }");
      head.push("vec3 toSrgb(vec3 c) { return pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2)); }");
      head.push("void main() {");
      var tail = [
        "  float a = clamp(" + alpha.e + ", 0.0, 1.0) * u_tint.a;",
        "  vec3 c = toSrgb(" + colour.e + " * u_tint.rgb);",
        additive ? "  gl_FragColor = vec4(c * a, 0.0);" : "  gl_FragColor = vec4(c * a, a);",
        "}"
      ];
      return { ok: true, source: head.concat(lines, tail).join("\n"), uniforms: uniforms, textures: textures, usesTime: usesTime, additive: additive };
    } catch (e) {
      return { ok: false, error: e.message || String(e) };
    }
  }

  /* One shared GPU surface: render(material, w, h, state) leaves the picture in .canvas. */
  function createRenderer() {
    var canvas = document.createElement("canvas"), gl = null;
    try { gl = canvas.getContext("webgl2", { premultipliedAlpha: true, alpha: true }) || canvas.getContext("webgl", { premultipliedAlpha: true, alpha: true }); } catch (e) { gl = null; }
    if (!gl) return null;
    var repeat = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
    var quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var VERTEX = "attribute vec2 p; varying vec2 v_uv; void main() { v_uv = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5); gl_Position = vec4(p, 0.0, 1.0); }";
    var textureCache = [];

    function shader(type, source) {
      var s = gl.createShader(type);
      gl.shaderSource(s, source); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { var log = gl.getShaderInfoLog(s); gl.deleteShader(s); throw new Error(log || "shader did not compile"); }
      return s;
    }
    function program(material) {
      if (material.program !== undefined) return material.program;
      material.program = null;
      try {
        var prog = gl.createProgram();
        gl.attachShader(prog, shader(gl.VERTEX_SHADER, VERTEX));
        gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, material.source));
        gl.bindAttribLocation(prog, 0, "p");
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || "shader did not link");
        material.locations = { time: gl.getUniformLocation(prog, "u_time"), tint: gl.getUniformLocation(prog, "u_tint") };
        material.uniforms.forEach(function (u) { material.locations[u.name] = gl.getUniformLocation(prog, u.name); });
        material.program = prog;
      } catch (e) { material.error = e.message; }
      return material.program;
    }
    function texture(image) {
      for (var i = 0; i < textureCache.length; i++) if (textureCache[i].image === image) return textureCache[i].tex;
      var tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      var wrap = repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      textureCache.push({ image: image, tex: tex });
      return tex;
    }

    /* state: {time, tint:[r,g,b,a] linear, scalar:{name:v}, vector:{name:[4]}, images:{assetKey: image}} */
    function render(material, width, height, state) {
      var prog = program(material);
      if (!prog) return false;
      width = Math.max(1, Math.min(2048, Math.round(width))); height = Math.max(1, Math.min(2048, Math.round(height)));
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      gl.viewport(0, 0, width, height);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.disable(gl.BLEND);
      gl.useProgram(prog);
      gl.bindBuffer(gl.ARRAY_BUFFER, quad);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      var loc = material.locations, unit = 0, ready = true;
      gl.uniform1f(loc.time, state.time || 0);
      gl.uniform4fv(loc.tint, state.tint || [1, 1, 1, 1]);
      material.uniforms.forEach(function (u) {
        if (u.kind === "scalar") gl.uniform1f(loc[u.name], state.scalar && u.key in state.scalar ? state.scalar[u.key] : u.value);
        else if (u.kind === "vector") gl.uniform4fv(loc[u.name], (state.vector && state.vector[u.key]) || u.value);
        else {
          var image = state.images && state.images[u.key];
          if (!image || !image.width) { ready = false; return; }
          gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, texture(image)); gl.uniform1i(loc[u.name], unit++);
        }
      });
      if (!ready) return false;
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return true;
    }
    return { canvas: canvas, render: render };
  }

  window.UmgMaterial = { compile: compile, createRenderer: createRenderer };
})();
