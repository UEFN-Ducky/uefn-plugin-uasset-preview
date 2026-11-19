/* UMG runtime for the Ducky panel: Slate-style layout, painting, input, animation and
 * audio for a Widget Blueprint spec (see backend/preview/uasset/umg_spec.py).
 * Draws on a 2D canvas; no DOM elements are used for the widget itself. */
(function () {
  "use strict";

  var PT_TO_PX = 96 / 72; // Slate lays text out at 96 DPI
  var REFERENCE_HEIGHT = 1080; // UEFN DPI curve: scale 1.0 at 1080 on the short side

  function srgb(c) { return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; }
  function css(color, alphaMul) {
    var c = color || [1, 1, 1, 1];
    var ch = function (v) { return Math.round(255 * srgb(Math.min(1, Math.max(0, v)))); };
    return "rgba(" + ch(c[0]) + "," + ch(c[1]) + "," + ch(c[2]) + "," + (c[3] * (alphaMul == null ? 1 : alphaMul)) + ")";
  }
  function mul(a, b) { return [a[0] * b[0], a[1] * b[1], a[2] * b[2], a[3] * b[3]]; }
  function isWhite(c) { return c[0] >= 0.999 && c[1] >= 0.999 && c[2] >= 0.999; }

  /* Content Browser thumbnails bake Unreal's grey checkerboard where the asset is
   * transparent. Find that board (two flat greys on a regular grid) and turn it back
   * into transparency. Returns the image untouched when no board is found. */
  function removeChecker(img) {
    try {
      var w = img.naturalWidth, h = img.naturalHeight, c = document.createElement("canvas");
      c.width = w; c.height = h;
      var x = c.getContext("2d", { willReadFrequently: true });
      x.drawImage(img, 0, 0);
      var data = x.getImageData(0, 0, w, h), px = data.data, cell = Math.max(1, Math.round(w / 16));
      var hist = [{}, {}];
      for (var y = 0; y < h; y += 2) for (var i = 0; i < w; i += 2) {
        var o = (y * w + i) * 4, r = px[o], g = px[o + 1], b = px[o + 2];
        if (Math.abs(r - g) > 6 || Math.abs(g - b) > 6) continue;
        var bucket = hist[(Math.floor(i / cell) + Math.floor(y / cell)) & 1], key = g >> 2;
        bucket[key] = (bucket[key] || 0) + 1;
      }
      var mode = hist.map(function (b) {
        var best = -1, count = 0;
        Object.keys(b).forEach(function (k) { if (b[k] > count) { count = b[k]; best = +k; } });
        return { grey: best * 4 + 2, count: count };
      });
      var sampled = (w / 2) * (h / 2) / 2;
      if (mode[0].count < sampled * 0.04 || mode[1].count < sampled * 0.04 || Math.abs(mode[0].grey - mode[1].grey) < 24) return img;
      var tol = 14;
      for (y = 0; y < h; y++) for (i = 0; i < w; i++) {
        o = (y * w + i) * 4;
        var want = mode[(Math.floor(i / cell) + Math.floor(y / cell)) & 1].grey, other = mode[((Math.floor(i / cell) + Math.floor(y / cell)) & 1) ^ 1].grey;
        var d = Math.max(Math.abs(px[o] - want), Math.abs(px[o + 1] - want), Math.abs(px[o + 2] - want));
        // cell borders blur into the other grey in JPEG thumbnails
        var d2 = Math.max(Math.abs(px[o] - other), Math.abs(px[o + 1] - other), Math.abs(px[o + 2] - other));
        var near = Math.min(d, (i % cell < 2 || i % cell > cell - 3 || y % cell < 2 || y % cell > cell - 3) ? d2 : 255);
        if (near <= tol) px[o + 3] = 0; else if (near < tol * 2) px[o + 3] = Math.round(255 * (near - tol) / tol);
      }
      x.putImageData(data, 0, 0);
      c.naturalWidth = w; c.naturalHeight = h;
      return c;
    } catch (e) { return img; }
  }

  function isBlack(img) {
    try {
      var c = document.createElement("canvas");
      c.width = c.height = 32;
      var x = c.getContext("2d");
      x.drawImage(img, 0, 0, 32, 32);
      var px = x.getImageData(0, 0, 32, 32).data, dark = 0;
      for (var o = 0; o < px.length; o += 4) if (px[o] < 14 && px[o + 1] < 14 && px[o + 2] < 14) dark++;
      return dark >= 32 * 32 * 0.97;
    } catch (e) { return false; }
  }

  // ---- curves -----------------------------------------------------------
  function evalChannel(ch, t) {
    if (!ch) return null;
    var k = ch.keys;
    if (!k.length) return ch.default;
    if (t <= k[0][0]) return k[0][1];
    var last = k[k.length - 1];
    if (t >= last[0]) return last[1];
    var lo = 0, hi = k.length - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (k[mid][0] <= t) lo = mid; else hi = mid; }
    var a = k[lo], b = k[hi], dt = b[0] - a[0], u = dt > 0 ? (t - a[0]) / dt : 0;
    if (a[2] === 1) return a[1]; // constant
    if (a[2] === 0) return a[1] + (b[1] - a[1]) * u; // linear
    // cubic: Bezier with control points from the leave / arrive tangents
    var p1 = a[1] + a[4] * dt / 3, p2 = b[1] - b[3] * dt / 3, v = 1 - u;
    return v * v * v * a[1] + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u * b[1];
  }

  // ---- engine -------------------------------------------------------------
  function create(canvas, spec, options) {
    options = options || {};
    var ctx = canvas.getContext("2d");
    var listeners = [];
    var images = {}; // asset key -> HTMLImageElement | "missing"
    var tintCache = {};
    var nodesByName = {};
    var allNodes = [];
    var painted = []; // {node, matrix} in paint order, for hit testing
    var hints = [];
    var hovered = null, pressed = null;
    var viewport = { w: spec.design_size[0], h: spec.design_size[1] };
    var layoutSize = { w: viewport.w, h: viewport.h };
    var dirty = true, raf = 0, disposed = false;
    var players = []; // running or held animations: {scope, anim, time, dir, speed, loop, state, last, sources, manual}
    var rootScope = null;
    function anyPlaying() { return players.some(function (p) { return p.state === "playing"; }); }
    var audio = null, audioBuffers = {}, muted = false;
    var showHints = false;
    var editMode = false, selected = null, drag = null, highlighted = null, editHover = null;
    // the view: zoom 1 = fitted to the canvas; pan in canvas pixels (wheel zooms, right/middle/Space-drag pans)
    var view = { zoom: 1, x: 0, y: 0 }, fitScale = 1, baseMatrix = new DOMMatrix(), paintId = 0, shownPercent = 0, spaceDown = false;

    function emit(type, detail) { listeners.forEach(function (fn) { fn(type, detail || {}); }); }
    function invalidate() { dirty = true; if (!raf && !disposed) raf = requestAnimationFrame(frame); }

    (function index(node, parent, nested) {
      if (!node) return;
      node.parent = parent;
      node.anim = {};
      node.nested = !!nested;
      allNodes.push(node);
      if (!nested && !(node.name in nodesByName)) nodesByName[node.name] = node;
      var inner = nested || node.kind === "UserWidget";
      node.children.forEach(function (c) { index(c, node, inner); });
    })(spec.root, null, false);
    allNodes.forEach(function (n) { if (!(n.name in nodesByName)) nodesByName[n.name] = n; });

    // Every Widget Blueprint in the tree is a scope: its own widget names, animations and event bindings.
    rootScope = { owner: null, top: spec.root, byName: nodesByName, anims: spec.animations || [], events: spec.events || [] };
    allNodes.forEach(function (owner) {
      if (!owner.anims && !owner.events) return;
      var byName = {};
      (function collect(n) {
        n.children.forEach(function (c) {
          if (!(c.name in byName)) byName[c.name] = c;
          if (!c.anims && !c.events) collect(c); // a deeper blueprint keeps its own names
        });
      })(owner);
      owner.scope = { owner: owner, top: owner.children[0] || owner, byName: byName, anims: owner.anims || [], events: owner.events || [] };
    });
    function scopeOf(n) { // the blueprint a widget belongs to
      for (var cur = n.parent; cur; cur = cur.parent) if (cur.scope) return cur.scope;
      return rootScope;
    }

    // ---- assets ---------------------------------------------------------
    // UI materials run as real shaders; one that fails to build keeps its thumbnail.
    var materials = {}, materialRenderer = null, materialErrors = [], timeDrawn = false, startedAt = performance.now();
    if (window.UmgMaterial) {
      Object.keys(spec.materials || {}).forEach(function (key) {
        var built = window.UmgMaterial.compile(spec.materials[key]);
        if (built.ok) materials[key] = built;
        else materialErrors.push({ name: (spec.assets[key] || {}).name || key, error: built.error });
      });
      if (Object.keys(materials).length) materialRenderer = window.UmgMaterial.createRenderer();
    }
    /* A still material is rendered once per size and kept as a picture; an animated one (it reads
     * Time) is rendered every frame. The picture is capped in resolution (UI materials are soft
     * gradients) and its size moves in 32 px steps, so a widget growing in an animation does not
     * re-render a still material every frame. */
    var MATERIAL_FRAME_MS = 1000 / 60 - 2; // a little under, so a 60 Hz screen keeps every frame
    var MATERIAL_MAX_PX = 512, MATERIAL_STEP_PX = 32, MATERIAL_CACHE_MAX = 64;
    var materialCache = {}, materialCacheSize = 0;
    function drawMaterial(key, tint, w, h, params) {
      var m = materials[key];
      if (!m || !materialRenderer || m.program === null || w <= 0 || h <= 0) return false;
      var t = ctx.getTransform(), scale = Math.max(Math.hypot(t.a, t.b), Math.hypot(t.c, t.d));
      var pw = w * scale, ph = h * scale, cap = Math.min(1, MATERIAL_MAX_PX / Math.max(pw, ph));
      var bw = Math.max(MATERIAL_STEP_PX, Math.ceil(pw * cap / MATERIAL_STEP_PX) * MATERIAL_STEP_PX);
      var bh = Math.max(MATERIAL_STEP_PX, Math.ceil(ph * cap / MATERIAL_STEP_PX) * MATERIAL_STEP_PX);
      var id = key + "|" + bw + "x" + bh + "|" + tint.join(",") + "|" + (params ? JSON.stringify(params) : "");
      var now = (performance.now() - startedAt) / 1000, entry = materialCache[id];
      var live = m.usesTime || !!params; // changes from frame to frame: rendered every time, never cached
      if (!entry || live) {
        var sources = {};
        for (var i = 0; i < m.textures.length; i++) {
          var img = images[m.textures[i]];
          if (!img || img === "missing" || img === "clear") return false;
          sources[m.textures[i]] = img;
        }
        var renderStart = performance.now();
        var ok = materialRenderer.render(m, bw, bh, {
          time: now, tint: tint, images: sources, scalar: params && params.scalar, vector: params && params.vector
        });
        perf.render += performance.now() - renderStart;
        if (!ok) {
          if (m.error && !m.reported) { m.reported = true; materialErrors.push({ name: (spec.assets[key] || {}).name || key, error: m.error }); delete materials[key]; }
          return false;
        }
        var r = materialRenderer.region;
        if (live) entry = { canvas: materialRenderer.canvas, sx: r.x, sy: r.y, sw: r.w, sh: r.h, at: now }; // drawn at once, before the next render
        else {
          if (materialCacheSize >= MATERIAL_CACHE_MAX) { materialCache = {}; materialCacheSize = 0; }
          entry = materialCache[id] = { canvas: document.createElement("canvas"), at: now };
          entry.canvas.width = bw; entry.canvas.height = bh; materialCacheSize++;
          var copy = entry.canvas.getContext("2d");
          copy.drawImage(materialRenderer.canvas, r.x, r.y, r.w, r.h, 0, 0, bw, bh);
        }
      }
      if (m.usesTime) timeDrawn = true;
      if (m.additive) { ctx.save(); ctx.globalCompositeOperation = "lighter"; }
      var copyStart = performance.now();
      if (entry.sw) ctx.drawImage(entry.canvas, entry.sx, entry.sy, entry.sw, entry.sh, 0, 0, w, h);
      else ctx.drawImage(entry.canvas, 0, 0, w, h);
      perf.copy += performance.now() - copyStart; perf.count++;
      if (m.additive) ctx.restore();
      return true;
    }
    Object.keys(spec.assets || {}).forEach(function (key) {
      var a = spec.assets[key];
      if ((a.kind !== "texture" && a.kind !== "material") || !a.url) { images[key] = "missing"; return; }
      var img = new Image();
      img.crossOrigin = "anonymous"; // the panel is sandboxed: pixels are readable only through CORS
      img.onload = function () {
        // A material whose thumbnail is plain black is a see-through one (Unreal
        // previews translucency over black): draw nothing rather than a black box.
        if (a.quality === "thumbnail" && a.kind === "material" && isBlack(img)) images[key] = "clear";
        else images[key] = a.quality === "thumbnail" ? removeChecker(img) : img;
        tintCache = {}; invalidate();
      };
      img.onerror = function () { images[key] = "missing"; invalidate(); };
      img.src = a.url;
    });

    function tinted(key, color) {
      var img = images[key];
      if (!img || img === "missing" || img === "clear") return null;
      if (isWhite(color)) return img;
      var id = key + "|" + css(color, 1);
      if (!tintCache[id]) {
        var c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        var x = c.getContext("2d");
        x.drawImage(img, 0, 0);
        x.globalCompositeOperation = "multiply";
        x.fillStyle = css([color[0], color[1], color[2], 1]);
        x.fillRect(0, 0, c.width, c.height);
        x.globalCompositeOperation = "destination-in";
        x.drawImage(img, 0, 0);
        tintCache[id] = c;
      }
      return tintCache[id];
    }

    // ---- text -----------------------------------------------------------
    var WEIGHTS = { light: 300, regular: 400, medium: 500, semibold: 600, bold: 700, black: 900, heavy: 900 };
    function fontCss(font) {
      var face = String(font.typeface || "").toLowerCase(), weight = 700, italic = face.indexOf("italic") >= 0;
      Object.keys(WEIGHTS).forEach(function (k) { if (face.indexOf(k) >= 0) weight = WEIGHTS[k]; });
      if (weight === 400 && /^Burbank/i.test(font.family || "")) weight = 600; // Burbank Regular is a heavy face
      // Fortnite's fonts are not on this PC: the real one is used when installed, else the closest system face.
      var family = /^Burbank/i.test(font.family || "") ? '"Burbank Small", "Burbank Big Condensed", "Segoe UI", "Noto Sans", Arial, sans-serif'
        : '"Segoe UI", "Noto Sans", Arial, sans-serif';
      return (italic ? "italic " : "") + weight + " " + (font.size * PT_TO_PX) + "px " + (options.fontFamily || family);
    }
    function textOf(node) {
      var p = node.props, s = node.anim.text != null ? node.anim.text : p.text || "";
      if (p.transform === "ToUpper") s = s.toUpperCase(); else if (p.transform === "ToLower") s = s.toLowerCase();
      return s;
    }
    // Burbank is wider than the system face that stands in for it. These factors were measured
    // against UEFN's own rendering of the same lines, so wrapping and sizes land where UEFN puts them.
    function widthScale(font) {
      if (!/^Burbank/i.test(font.family || "")) return 1;
      return /black|bold|heavy/i.test(font.typeface || "") ? 1.04 : 1.11;
    }
    function textLines(node, maxWidth) {
      var p = node.props, k = widthScale(p.font);
      ctx.font = fontCss(p.font);
      try { ctx.letterSpacing = (p.font.letter_spacing / 1000 * p.font.size * PT_TO_PX) + "px"; } catch (e) {}
      var limit = p.wrap_at > 0 ? p.wrap_at : (p.wrap && maxWidth > 0 ? maxWidth : 0);
      var out = [];
      textOf(node).split("\n").forEach(function (para) {
        if (!limit) { out.push(para); return; }
        var line = "";
        para.split(" ").forEach(function (word) {
          var next = line ? line + " " + word : word;
          if (line && ctx.measureText(next).width * k > limit) { out.push(line); line = word; } else line = next;
        });
        out.push(line);
      });
      return out;
    }
    function measureText(node, maxWidth) {
      var p = node.props, lines = textLines(node, maxWidth), w = 0;
      lines.forEach(function (l) { w = Math.max(w, ctx.measureText(l).width); });
      var lineH = p.font.size * PT_TO_PX * 1.2 * (p.line_height || 1), o = p.font.outline_size || 0;
      return [w * widthScale(p.font) + o * 2, lines.length * lineH + o * 2];
    }

    // ---- layout ---------------------------------------------------------
    // Design time, as in UEFN's designer: every widget is shown whatever its Visibility,
    // unless it is hidden with the designer's eye icon.
    var designTime = !!options.designTime;
    function visibilityOf(n) {
      if (designTime) return n.design_hidden ? "Collapsed" : "Visible";
      return n.anim.visibility || n.visibility;
    }
    function collapsed(n) { return visibilityOf(n) === "Collapsed"; }
    function gridCells(n, kids) { // uniform grid: every cell as large as the largest child
      var p = n.props, sp = p.slot_padding || [0, 0, 0, 0], cols = 0, rows = 0, cw = p.min_slot_width || 0, ch = p.min_slot_height || 0;
      kids.forEach(function (c) {
        cols = Math.max(cols, (c.slot.column || 0) + 1); rows = Math.max(rows, (c.slot.row || 0) + 1);
        cw = Math.max(cw, c.desired[0] + sp[0] + sp[2]); ch = Math.max(ch, c.desired[1] + sp[1] + sp[3]);
      });
      return { cols: cols, rows: rows, w: cw, h: ch, pad: sp };
    }
    function pad(n) { return n.anim.pad || n.slot.pad || [0, 0, 0, 0]; }

    function measure(n) {
      var d = [0, 0], p = n.props, kids = n.children.filter(function (c) { return !collapsed(c); });
      kids.forEach(measure);
      var boxed = function (c) { var q = pad(c); return [c.desired[0] + q[0] + q[2], c.desired[1] + q[1] + q[3]]; };
      switch (n.kind) {
        case "Image": d = p.brush.size.slice(); break;
        case "TextBlock": d = measureText(n, n.rect ? n.rect.w : 0); break;
        case "Spacer": d = p.size.slice(); break;
        case "StackBox":
          var vertical = p.orientation === "Vertical";
          kids.forEach(function (c) {
            var b = boxed(c);
            if (vertical) { d[0] = Math.max(d[0], b[0]); d[1] += b[1]; } else { d[0] += b[0]; d[1] = Math.max(d[1], b[1]); }
          });
          break;
        case "UniformGridPanel":
          var cells = gridCells(n, kids);
          d = [cells.cols * cells.w, cells.rows * cells.h];
          break;
        case "WidgetSwitcher":
          var active = n.children[n.anim.active != null ? n.anim.active : p.active];
          if (active && !collapsed(active)) d = boxed(active);
          break;
        case "SizeBox":
          if (kids[0]) d = boxed(kids[0]);
          if (p.min_width != null) d[0] = Math.max(d[0], p.min_width);
          if (p.min_height != null) d[1] = Math.max(d[1], p.min_height);
          if (p.max_width != null) d[0] = Math.min(d[0], p.max_width);
          if (p.max_height != null) d[1] = Math.min(d[1], p.max_height);
          if (p.width != null) d[0] = p.width;
          if (p.height != null) d[1] = p.height;
          break;
        case "Button":
          if (n.variant === "custom" || kids.length) kids.forEach(function (c) { var b = boxed(c); d[0] = Math.max(d[0], b[0]); d[1] = Math.max(d[1], b[1]); });
          else {
            ctx.font = "800 26px " + (options.fontFamily || '"Segoe UI", Arial, sans-serif');
            d = [Math.max(160, ctx.measureText((p.text || "").toUpperCase()).width + 64), 64];
          }
          d[0] = Math.max(d[0], p.min_width || 0); d[1] = Math.max(d[1], p.min_height || 0);
          break;
        case "Border":
          if (kids[0]) d = [kids[0].desired[0] + p.pad[0] + p.pad[2], kids[0].desired[1] + p.pad[1] + p.pad[3]];
          break;
        case "UserWidget":
          if (p.design_size) d = p.design_size.slice();
          else if (kids[0]) d = kids[0].desired.slice();
          else if (p.missing) d = [160, 48];
          break;
        case "CanvasPanel": // as SConstraintCanvas: the largest slot, plus its offset when docked to an edge
          kids.forEach(function (c) {
            var s = c.slot;
            if (s.type !== "canvas") return;
            [0, 1].forEach(function (axis) {
              var lo = s.anchor_min[axis], hi = s.anchor_max[axis], o = s.offsets, size;
              if (lo !== hi) size = o[axis] + o[axis + 2];
              else size = (s.auto_size ? c.desired[axis] : o[axis + 2]) + ((lo === 0 || lo === 1) ? Math.abs(o[axis]) : 0);
              d[axis] = Math.max(d[axis], size);
            });
          });
          break;
        default: // Overlay, ScaleBox and anything container-like
          kids.forEach(function (c) { var b = boxed(c); d[0] = Math.max(d[0], b[0]); d[1] = Math.max(d[1], b[1]); });
      }
      n.desired = d;
    }

    function alignAxis(mode, start, size, want) {
      if (mode === "Fill") return [start, size];
      want = Math.min(want, Math.max(0, size)); // Slate's AlignChild never gives a child more than its cell
      if (mode === "Center") return [start + (size - want) / 2, want];
      if (mode === "Right" || mode === "Bottom") return [start + size - want, want];
      return [start, want];
    }
    function place(c, x, y, w, h) {
      var q = pad(c);
      var ax = alignAxis(c.slot.h || "Fill", x + q[0], w - q[0] - q[2], c.desired[0]);
      var ay = alignAxis(c.slot.v || "Fill", y + q[1], h - q[1] - q[3], c.desired[1]);
      arrange(c, ax[0], ay[0], Math.max(0, ax[1]), Math.max(0, ay[1]));
    }

    function arrange(n, x, y, w, h) {
      n.rect = { x: x, y: y, w: w, h: h };
      n.layoutScale = 1;
      var p = n.props, kids = n.children.filter(function (c) { return !collapsed(c); });
      if (n.kind === "CanvasPanel") {
        kids.forEach(function (c) {
          var s = c.slot;
          if (s.type !== "canvas") { arrange(c, 0, 0, w, h); return; }
          var o = c.anim.offsets || s.offsets, cx, cy, cw, ch;
          if (s.anchor_min[0] === s.anchor_max[0]) {
            cw = s.auto_size ? c.desired[0] : o[2];
            cx = s.anchor_min[0] * w + o[0] - s.align[0] * cw;
          } else { cx = s.anchor_min[0] * w + o[0]; cw = s.anchor_max[0] * w - o[2] - cx; }
          if (s.anchor_min[1] === s.anchor_max[1]) {
            ch = s.auto_size ? c.desired[1] : o[3];
            cy = s.anchor_min[1] * h + o[1] - s.align[1] * ch;
          } else { cy = s.anchor_min[1] * h + o[1]; ch = s.anchor_max[1] * h - o[3] - cy; }
          arrange(c, cx, cy, Math.max(0, cw), Math.max(0, ch));
        });
      } else if (n.kind === "StackBox") {
        var vertical = p.orientation === "Vertical", total = vertical ? h : w, fixed = 0, weight = 0;
        kids.forEach(function (c) {
          var q = pad(c), extra = vertical ? q[1] + q[3] : q[0] + q[2];
          if (c.slot.fill) { weight += c.slot.fill_value; fixed += extra; } else fixed += (vertical ? c.desired[1] : c.desired[0]) + extra;
        });
        var cursor = 0, spare = Math.max(0, total - fixed);
        kids.forEach(function (c) {
          var q = pad(c), extra = vertical ? q[1] + q[3] : q[0] + q[2];
          var len = (c.slot.fill ? (weight > 0 ? spare * c.slot.fill_value / weight : 0) : (vertical ? c.desired[1] : c.desired[0])) + extra;
          if (vertical) place(c, 0, cursor, w, len); else place(c, cursor, 0, len, h);
          cursor += len;
        });
      } else if (n.kind === "UniformGridPanel") {
        var grid = gridCells(n, kids), cellW = grid.cols ? w / grid.cols : 0, cellH = grid.rows ? h / grid.rows : 0;
        kids.forEach(function (c) {
          var gx = (c.slot.column || 0) * cellW + grid.pad[0], gy = (c.slot.row || 0) * cellH + grid.pad[1];
          var gw = cellW - grid.pad[0] - grid.pad[2], gh = cellH - grid.pad[1] - grid.pad[3];
          var ax = alignAxis(c.slot.h || "Left", gx, gw, c.desired[0]), ay = alignAxis(c.slot.v || "Top", gy, gh, c.desired[1]);
          arrange(c, ax[0], ay[0], Math.max(0, ax[1]), Math.max(0, ay[1]));
        });
      } else if (n.kind === "ScaleBox") {
        kids.forEach(function (c) {
          var dw = Math.max(1, c.desired[0]), dh = Math.max(1, c.desired[1]), s = 1;
          if (p.stretch === "UserSpecified") s = p.user_scale;
          else if (p.stretch === "ScaleToFill") s = Math.max(w / dw, h / dh);
          else if (p.stretch !== "None") s = Math.min(w / dw, h / dh);
          if (p.direction === "DownOnly") s = Math.min(1, s); else if (p.direction === "UpOnly") s = Math.max(1, s);
          var ax = alignAxis(c.slot.h === "Fill" ? "Center" : c.slot.h, 0, w, dw * s);
          var ay = alignAxis(c.slot.v === "Fill" ? "Center" : c.slot.v, 0, h, dh * s);
          arrange(c, ax[0], ay[0], dw, dh);
          c.layoutScale = s;
        });
      } else if (n.kind === "Border") {
        kids.forEach(function (c) {
          var ax = alignAxis(p.h, p.pad[0], w - p.pad[0] - p.pad[2], c.desired[0]);
          var ay = alignAxis(p.v, p.pad[1], h - p.pad[1] - p.pad[3], c.desired[1]);
          arrange(c, ax[0], ay[0], ax[1], ay[1]);
        });
      } else if (n.kind === "UserWidget") {
        kids.forEach(function (c) { arrange(c, 0, 0, w, h); });
      } else {
        kids.forEach(function (c) { place(c, 0, 0, w, h); });
      }
    }

    function layout() {
      if (!spec.root) return;
      measure(spec.root);
      arrange(spec.root, 0, 0, layoutSize.w, layoutSize.h);
      // Wrapped text measures against the width it was given: settle once more.
      if (allNodes.some(function (n) { return n.kind === "TextBlock" && (n.props.wrap || n.props.wrap_at); })) {
        measure(spec.root); arrange(spec.root, 0, 0, layoutSize.w, layoutSize.h);
      }
    }

    // ---- painting -------------------------------------------------------
    function roundRect(x, y, w, h, radii) {
      var m = Math.min(w, h) / 2, r = radii.map(function (v) { return Math.max(0, Math.min(v, m)); });
      ctx.beginPath();
      ctx.moveTo(x + r[0], y);
      ctx.lineTo(x + w - r[1], y); ctx.arcTo(x + w, y, x + w, y + r[1], r[1]);
      ctx.lineTo(x + w, y + h - r[2]); ctx.arcTo(x + w, y + h, x + w - r[2], y + h, r[2]);
      ctx.lineTo(x + r[3], y + h); ctx.arcTo(x, y + h, x, y + h - r[3], r[3]);
      ctx.lineTo(x, y + r[0]); ctx.arcTo(x, y, x + r[0], y, r[0]);
      ctx.closePath();
    }

    function drawBrush(brush, color, w, h, params) {
      params = params || brush.params; // material parameters animated by a widget animation win
      if (!brush || brush.draw === "NoDrawType" || w <= 0 || h <= 0) return;
      var tint = mul(brush.tint, color);
      if (brush.draw === "RoundedBox") {
        var o = brush.outline || { radii: [0, 0, 0, 0], width: 0, color: [0, 0, 0, 0], rounding: "FixedRadius" };
        var radii = o.rounding === "HalfHeightRadius" ? [h / 2, h / 2, h / 2, h / 2] : o.radii, half = o.width / 2;
        if (brush.resource) { // a rounded box with a picture: the picture, cut to the rounded shape
          ctx.save();
          roundRect(0, 0, w, h, radii); ctx.clip();
          drawBrush({ draw: "Image", tint: brush.tint, size: brush.size, margin: brush.margin, resource: brush.resource, params: params }, color, w, h);
          ctx.restore();
          roundRect(half, half, w - o.width, h - o.width, radii);
        } else {
          roundRect(half, half, w - o.width, h - o.width, radii);
          ctx.fillStyle = css(tint); ctx.fill();
        }
        if (o.width > 0 && o.color[3] > 0) { ctx.lineWidth = o.width; ctx.strokeStyle = css(mul(o.color, [1, 1, 1, color[3]])); ctx.stroke(); }
        return;
      }
      if (!brush.resource) { ctx.fillStyle = css(tint); ctx.fillRect(0, 0, w, h); return; }
      var asset = spec.assets[brush.resource] || {};
      if (asset.kind === "material" && drawMaterial(brush.resource, tint, w, h, params)) return;
      if (images[brush.resource] === "clear") return;
      var img = tinted(brush.resource, asset.kind === "material" ? [1, 1, 1, 1] : tint);
      ctx.save();
      ctx.globalAlpha *= tint[3];
      if (!img) {
        ctx.fillStyle = "rgba(120,130,150,0.28)"; ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = "rgba(200,210,230,0.5)"; ctx.lineWidth = 1; ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
        ctx.fillStyle = "rgba(230,235,245,0.8)"; ctx.font = "12px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        if (w > 60 && h > 16) ctx.fillText(asset.name || "image", w / 2, h / 2, w - 8);
      } else if (brush.draw === "Box" || brush.draw === "Border") {
        var iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height, m = brush.margin;
        var sl = m[0] * iw, st = m[1] * ih, sr = m[2] * iw, sb = m[3] * ih;
        var dl = m[0] * brush.size[0], dt = m[1] * brush.size[1], dr = m[2] * brush.size[0], db = m[3] * brush.size[1];
        var sx = [0, sl, iw - sr, iw], sy = [0, st, ih - sb, ih], dx = [0, dl, w - dr, w], dy = [0, dt, h - db, h];
        for (var r = 0; r < 3; r++) for (var c = 0; c < 3; c++) {
          if (brush.draw === "Border" && r === 1 && c === 1) continue;
          var sw = sx[c + 1] - sx[c], sh = sy[r + 1] - sy[r], dw = dx[c + 1] - dx[c], dh = dy[r + 1] - dy[r];
          if (sw > 0 && sh > 0 && dw > 0 && dh > 0) ctx.drawImage(img, sx[c], sy[r], sw, sh, dx[c], dy[r], dw, dh);
        }
      } else ctx.drawImage(img, 0, 0, w, h);
      ctx.restore();
    }

    function drawText(n, w, h) {
      var p = n.props, f = p.font, color = n.anim.color || p.color, lines = textLines(n, w);
      var lineH = f.size * PT_TO_PX * 1.2 * (p.line_height || 1), o = f.outline_size || 0;
      ctx.textBaseline = "middle";
      ctx.textAlign = p.justify === "Center" ? "center" : p.justify === "Right" ? "right" : "left";
      var x = p.justify === "Center" ? w / 2 : p.justify === "Right" ? w - o : o;
      // Burbank (UEFN's text font) spaces its lines ~1.85x its size while the box keeps the
      // smaller height: in UEFN the next lines overflow below the text block (measured on UEFN
      // captures: STARTER/PACK overlaps the subtitle under it, a wrapped chip runs past its pill).
      var pitch = /^Burbank/i.test(f.family || "") ? lineH * (1.85 / 1.2) : lineH;
      lines.forEach(function (line, i) {
        var y = o + lineH * 0.5 + pitch * i;
        ctx.save();
        if (f.skew) ctx.transform(1, 0, -f.skew, 1, f.skew * y, 0); // Slate's font skew: the top leans right
        var k = widthScale(f);
        ctx.translate(x, 0); ctx.scale(k, 1); // widen around the anchor so alignment is kept
        if (p.shadow_color[3] > 0) { ctx.fillStyle = css(p.shadow_color); ctx.fillText(line, p.shadow_offset[0] / k, y + p.shadow_offset[1]); }
        if (o > 0) { ctx.lineJoin = "round"; ctx.lineWidth = o * 2; ctx.strokeStyle = css(f.outline_color); ctx.strokeText(line, 0, y); }
        ctx.fillStyle = css(color); ctx.fillText(line, 0, y);
        ctx.restore();
      });
    }

    var BUTTON_STYLES = {
      loud: { top: "#fff45c", bottom: "#f5c400", text: "#1b1b1f" },
      regular: { top: "#3d8bff", bottom: "#1552d6", text: "#ffffff" },
      quiet: { top: "rgba(255,255,255,0.16)", bottom: "rgba(255,255,255,0.08)", text: "#ffffff" }
    };
    function drawButton(n, w, h) {
      var p = n.props, state = !n.enabled ? "disabled" : pressed === n ? "pressed" : hovered === n ? "hovered" : "normal";
      var style = BUTTON_STYLES[n.variant];
      if (p.background) {
        // the project's own background; states shown the way Slate tints a button
        var shade = state === "hovered" ? 1.15 : state === "pressed" ? 0.8 : state === "disabled" ? 0.5 : 1;
        ctx.save();
        ctx.filter = shade === 1 ? "none" : "brightness(" + shade + ")";
        drawBrush({ draw: "Image", tint: [1, 1, 1, 1], size: [w, h], margin: [0, 0, 0, 0], resource: p.background }, [1, 1, 1, 1], w, h);
        ctx.restore();
      } else if (style && !n.children.length) {
        var g = ctx.createLinearGradient(0, 0, 0, h);
        g.addColorStop(0, style.top); g.addColorStop(1, style.bottom);
        roundRect(0, 0, w, h, [6, 6, 6, 6]); ctx.fillStyle = g; ctx.fill();
        if (state === "hovered") { ctx.fillStyle = "rgba(255,255,255,0.18)"; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "#ffffff"; ctx.stroke(); }
        if (state === "pressed") { ctx.fillStyle = "rgba(0,0,0,0.22)"; ctx.fill(); }
        if (state === "disabled") { ctx.fillStyle = "rgba(40,40,48,0.6)"; ctx.fill(); }
      } else if (p.style) {
        drawBrush(p.style[state] || p.style.normal, p.color, w, h);
      }
      if ((p.background || style) && !n.children.length && p.text) {
        ctx.font = "800 26px " + (options.fontFamily || '"Segoe UI", Arial, sans-serif');
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = p.background ? "#ffffff" : style.text;
        ctx.fillText(p.text.toUpperCase(), w / 2, h / 2 + 1, w - 16);
      }
    }

    function paint(n, alpha) {
      var vis = visibilityOf(n);
      if (vis === "Collapsed" || !n.rect) return;
      var r = n.rect, a = n.anim, x = n.xform;
      var t = [a.tx != null ? a.tx : x.t[0], a.ty != null ? a.ty : x.t[1]];
      var s = [a.sx != null ? a.sx : x.s[0], a.sy != null ? a.sy : x.s[1]];
      var sh = [a.shx != null ? a.shx : x.shear[0], a.shy != null ? a.shy : x.shear[1]];
      var angle = a.angle != null ? a.angle : x.angle;
      var opacity = alpha * (a.opacity != null ? a.opacity : n.opacity) * (n.enabled ? 1 : 0.5);
      ctx.save();
      n.pm = ctx.getTransform(); // the parent's space: where this widget's slot offsets live
      ctx.translate(r.x, r.y);
      if (n.layoutScale !== 1) ctx.scale(n.layoutScale, n.layoutScale);
      if (pressed === n && n.kind === "Button" && !n.children.length) { ctx.translate(r.w / 2, r.h / 2); ctx.scale(0.97, 0.97); ctx.translate(-r.w / 2, -r.h / 2); }
      var px = n.pivot[0] * r.w, py = n.pivot[1] * r.h;
      ctx.translate(px + t[0], py + t[1]);
      if (angle) ctx.rotate(angle * Math.PI / 180);
      if (sh[0] || sh[1]) ctx.transform(1, Math.tan(sh[1] * Math.PI / 180), Math.tan(sh[0] * Math.PI / 180), 1, 0, 0);
      ctx.scale(s[0], s[1]);
      ctx.translate(-px, -py);
      n.wm = ctx.getTransform(); n.pid = paintId;
      if (vis !== "Hidden" && opacity > 0.001) {
        ctx.globalAlpha = opacity;
        painted.push({ node: n, matrix: ctx.getTransform() });
        if (n.clip) { ctx.beginPath(); ctx.rect(0, 0, r.w, r.h); ctx.clip(); }
        if (n.kind === "Image") drawBrush(n.props.brush, a.color || n.props.color, r.w, r.h, a.mat);
        else if (n.kind === "TextBlock") drawText(n, r.w, r.h);
        else if (n.kind === "Button") drawButton(n, r.w, r.h);
        else if (n.kind === "Border") drawBrush(n.props.brush, n.props.color, r.w, r.h);
        else if (n.kind === "ProgressBar") {
          ctx.fillStyle = "rgba(0,0,0,0.45)"; ctx.fillRect(0, 0, r.w, r.h);
          ctx.fillStyle = css(n.props.fill); ctx.fillRect(0, 0, r.w * (a.percent != null ? a.percent : n.props.percent), r.h);
        } else if (n.kind === "UserWidget" && n.props.missing) {
          ctx.strokeStyle = "rgba(255,180,90,0.9)"; ctx.setLineDash([6, 4]); ctx.strokeRect(1, 1, r.w - 2, r.h - 2); ctx.setLineDash([]);
          ctx.fillStyle = "rgba(255,200,130,0.95)"; ctx.font = "13px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(n.class, r.w / 2, r.h / 2, r.w - 8);
        }
        var kids = n.children;
        if (n.kind === "WidgetSwitcher") {
          var active = kids[a.active != null ? a.active : n.props.active];
          kids = active ? [active] : [];
        } else if (n.kind === "CanvasPanel") {
          kids = kids.slice().sort(function (p, q) { return (p.slot.z || 0) - (q.slot.z || 0); });
        }
        kids.forEach(function (c) { paint(c, opacity); });
        if (showHints && n.kind === "Button") hints.push({ m: ctx.getTransform(), w: r.w, h: r.h, hot: hovered === n });
      }
      ctx.restore();
    }

    var perf = { render: 0, copy: 0, count: 0 }, perfShown = { render: 0, copy: 0, count: 0 };
    var showStats = false, frameTimes = [], frameGaps = [], lastFrameAt = 0, hoverAt = 0, hoverLatency = null;
    function drawStats() {
      var avg = function (a) { return a.length ? a.reduce(function (s, v) { return s + v; }, 0) / a.length : 0; };
      var gap = avg(frameGaps), lines = [
        "fps " + (gap ? Math.round(1000 / gap) : "-") + " · frame " + avg(frameTimes).toFixed(1) + " ms (max " + Math.max.apply(null, frameTimes.concat([0])).toFixed(1) + ")",
        "hover -> first movement " + (hoverLatency == null ? "-" : hoverLatency.toFixed(0) + " ms"),
        "canvas " + canvas.width + "x" + canvas.height + " · materials " + Object.keys(materials).length,
        "materials: shader " + perfShown.render.toFixed(1) + " ms + copy " + perfShown.copy.toFixed(1) + " ms (" + perfShown.count + " draws)",
        "gpu " + ((materialRenderer && materialRenderer.info) || "-").slice(0, 46)
      ];
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.font = "12px ui-monospace, Consolas, monospace"; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillStyle = "rgba(0,0,0,0.7)"; ctx.fillRect(4, 4, 380, 86);
      ctx.fillStyle = "#9eff9e"; lines.forEach(function (l, i) { ctx.fillText(l, 10, 9 + i * 16); });
    }
    function frame(now) {
      raf = 0;
      if (disposed) return;
      var workStart = performance.now();
      if (anyPlaying()) stepPlayers(now);
      if (!dirty && !anyPlaying() && !timeDrawn) return;
      // Only an animated material changed: 60 images a second are plenty, whatever the screen's rate
      // (a 144-360 Hz screen would otherwise redraw everything 2-6 times as often for nothing).
      if (!dirty && !anyPlaying() && lastFrameAt && now - lastFrameAt < MATERIAL_FRAME_MS) { raf = requestAnimationFrame(frame); return; }
      if (lastFrameAt) { frameGaps.push(now - lastFrameAt); if (frameGaps.length > 60) frameGaps.shift(); }
      lastFrameAt = now;
      dirty = false; timeDrawn = false;
      var dpr = Math.min(window.devicePixelRatio || 1, 2), cw = canvas.clientWidth, ch = canvas.clientHeight;
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr);
      }
      var dpi = Math.min(viewport.w, viewport.h) / REFERENCE_HEIGHT;
      if (spec.custom_size) dpi = 1;
      layoutSize = { w: viewport.w / dpi, h: viewport.h / dpi };
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      var fit = Math.min(canvas.width / viewport.w, canvas.height / viewport.h);
      var ox = (canvas.width - viewport.w * fit) / 2, oy = (canvas.height - viewport.h * fit) / 2;
      fitScale = fit;
      var z = view.zoom;
      ctx.setTransform(fit * dpi * z, 0, 0, fit * dpi * z, ox * z + view.x, oy * z + view.y);
      baseMatrix = ctx.getTransform(); paintId++;
      layout();
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, layoutSize.w, layoutSize.h); ctx.clip();
      if (options.drawBackground) options.drawBackground(ctx, layoutSize.w, layoutSize.h);
      painted = []; hints = [];
      if (spec.root) paint(spec.root, 1);
      ctx.restore();
      // button outlines go on top of everything, at a constant on-screen thickness
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      hints.forEach(function (hint) {
        var pts = [[0, 0], [hint.w, 0], [hint.w, hint.h], [0, hint.h]].map(function (p) { return hint.m.transformPoint(new DOMPoint(p[0], p[1])); });
        ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
        pts.slice(1).forEach(function (p) { ctx.lineTo(p.x, p.y); });
        ctx.closePath();
        ctx.lineWidth = 2 * dpr; ctx.setLineDash([6 * dpr, 4 * dpr]);
        ctx.strokeStyle = hint.hot ? "#ffffff" : "#7dd3fc"; ctx.stroke(); ctx.setLineDash([]);
      });
      if (highlighted) { // the widget a binding in the side list points at
        var hl = null;
        for (var hi = painted.length - 1; hi >= 0; hi--) if (painted[hi].node === highlighted) { hl = painted[hi]; break; }
        if (hl) {
          var box = [[0, 0], [highlighted.rect.w, 0], [highlighted.rect.w, highlighted.rect.h], [0, highlighted.rect.h]]
            .map(function (p) { return hl.matrix.transformPoint(new DOMPoint(p[0], p[1])); });
          ctx.beginPath(); ctx.moveTo(box[0].x, box[0].y);
          box.slice(1).forEach(function (p) { ctx.lineTo(p.x, p.y); });
          ctx.closePath(); ctx.fillStyle = "rgba(125,211,252,0.18)"; ctx.fill();
          ctx.lineWidth = 2 * dpr; ctx.strokeStyle = "#7dd3fc"; ctx.stroke();
        }
      }
      if (editMode) drawEditOverlay(dpr);
      var pct = Math.round(view.zoom * fit / dpr * 100);
      if (pct !== shownPercent) { shownPercent = pct; emit("view", { percent: pct }); }
      if (hoverAt && players.some(function (p) { return p.dir > 0 && p.time > p.anim.start; })) { hoverLatency = performance.now() - hoverAt; hoverAt = 0; }
      frameTimes.push(performance.now() - workStart); if (frameTimes.length > 60) frameTimes.shift();
      perfShown = perf; perf = { render: 0, copy: 0, count: 0 };
      if (showStats) drawStats();
      if (anyPlaying() || timeDrawn || showStats) raf = requestAnimationFrame(frame);
      else lastFrameAt = 0;
    }

    // ---- editing --------------------------------------------------------
    // Field ids match backend/preview/uasset/writer.py FIELDS.
    function owner(n) { // a node that comes from another Widget Blueprint is edited through its instance
      var top = n;
      while (top && top.nested) top = top.parent;
      return top;
    }
    function fieldsOf(n) {
      var f = { opacity: n.opacity, translation: n.xform.t.slice(), scale: n.xform.s.slice(), angle: n.xform.angle };
      if (n.kind === "TextBlock") { f.text = n.props.text; f.text_color = n.props.color.slice(); f.shadow_color = n.props.shadow_color.slice(); f.font_size = n.props.font.size; }
      if (n.kind === "Image") { f.image_color = n.props.color.slice(); f.tint = n.props.brush.tint.slice(); f.image_size = n.props.brush.size.slice(); f.resource = n.props.brush.resource; }
      if (n.slot.type === "canvas") { f.offsets = n.slot.offsets.slice(); f.alignment = n.slot.align.slice(); }
      else if (n.slot.pad) f.padding = n.slot.pad.slice();
      return f;
    }
    function describe(n) {
      return { id: n.id, name: n.name, label: n.label, kind: n.kind, className: n["class"], slot: n.slot.type, fields: fieldsOf(n) };
    }
    function applyField(n, field, value) {
      var p = n.props, side = field.indexOf(".") > 0 ? +field.split(".")[1] : -1;
      if (field === "opacity") n.opacity = value;
      else if (field === "translation") n.xform.t = value.slice();
      else if (field === "scale") n.xform.s = value.slice();
      else if (field === "angle") n.xform.angle = value;
      else if (field === "text") p.text = value;
      else if (field === "text_color" || field === "image_color") p.color = value.slice();
      else if (field === "shadow_color") p.shadow_color = value.slice();
      else if (field === "font_size") p.font.size = value;
      else if (field === "tint") p.brush.tint = value.slice();
      else if (field === "image_size") p.brush.size = value.slice();
      else if (field === "resource") p.brush.resource = value;
      else if (field === "alignment") n.slot.align = value.slice();
      else if (field.indexOf("offsets.") === 0) n.slot.offsets[side] = value;
      else if (field.indexOf("padding.") === 0) { if (!n.slot.pad) n.slot.pad = [0, 0, 0, 0]; n.slot.pad[side] = value; }
      else return false;
      tintCache = {}; invalidate();
      return true;
    }
    function canvasPoint(ev) { // pointer position in canvas pixels
      var b = canvas.getBoundingClientRect();
      return new DOMPoint((ev.clientX - b.left) * canvas.width / b.width, (ev.clientY - b.top) * canvas.height / b.height);
    }
    function hitAny(clientX, clientY) { // edit mode: anything drawn can be picked, click-through or not
      var pt = canvasPoint({ clientX: clientX, clientY: clientY });
      for (var i = painted.length - 1; i >= 0; i--) {
        var e = painted[i];
        if (hitTarget(e.node) && inside(e.node, e.matrix, pt)) return e.node;
      }
      return null;
    }
    function inside(n, m, pt) {
      var local = m.inverse().transformPoint(pt);
      return local.x >= 0 && local.y >= 0 && local.x <= n.rect.w && local.y <= n.rect.h;
    }
    function shown(n) { return !!(n && n.rect && n.wm && n.pid === paintId); } // drawn in the last frame
    function quad(n, m) {
      m = m || n.wm;
      return [[0, 0], [n.rect.w, 0], [n.rect.w, n.rect.h], [0, n.rect.h]].map(function (p) { return m.transformPoint(new DOMPoint(p[0], p[1])); });
    }
    function bounds(pts) {
      var xs = pts.map(function (p) { return p.x; }), ys = pts.map(function (p) { return p.y; });
      return { l: Math.min.apply(null, xs), t: Math.min.apply(null, ys), r: Math.max.apply(null, xs), b: Math.max.apply(null, ys) };
    }
    function toLocal(m, dx, dy) { // a canvas-pixel movement, in the units of the space m draws
      var inv = m.inverse(), a = inv.transformPoint(new DOMPoint(0, 0)), b = inv.transformPoint(new DOMPoint(dx, dy));
      return [b.x - a.x, b.y - a.y];
    }
    function within(n, top) { for (var c = n; c; c = c.parent) if (c === top) return true; return false; }
    function px() { return Math.min(window.devicePixelRatio || 1, 2); } // canvas pixels per CSS pixel
    // The eight handles: [hx, hy], -1 = left/top edge, 1 = right/bottom, 0 = the middle of that side.
    var HANDLES = [[-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0]];
    function handlePoints(n) {
      var q = quad(n), big = Math.hypot(q[1].x - q[0].x, q[1].y - q[0].y) > 36 * px() && Math.hypot(q[3].x - q[0].x, q[3].y - q[0].y) > 36 * px();
      return HANDLES.filter(function (h) { return big || (h[0] && h[1]); }).map(function (h) {
        return { h: h, p: n.wm.transformPoint(new DOMPoint((h[0] + 1) / 2 * n.rect.w, (h[1] + 1) / 2 * n.rect.h)) };
      });
    }
    function handleAt(pt) {
      if (!shown(selected)) return null;
      var hits = handlePoints(selected).filter(function (e) { return Math.abs(e.p.x - pt.x) <= 7 * px() && Math.abs(e.p.y - pt.y) <= 7 * px(); });
      return hits.length ? hits[0].h : null;
    }
    function handleCursor(h) {
      if (!h[0]) return "ns-resize";
      if (!h[1]) return "ew-resize";
      return h[0] === h[1] ? "nwse-resize" : "nesw-resize";
    }
    // A canvas slot is resized through its size (Offsets); anything else through its render Scale.
    function sizeMode(n) { return n.slot.type === "canvas" && !n.slot.auto_size; }
    // Smart guides: the edges and centres of everything else on screen, and of the screen itself.
    function snapTargets(n) {
      var xs = [], ys = [];
      function add(b) {
        [b.l, (b.l + b.r) / 2, b.r].forEach(function (v) { xs.push({ v: v, lo: b.t, hi: b.b }); });
        [b.t, (b.t + b.b) / 2, b.b].forEach(function (v) { ys.push({ v: v, lo: b.l, hi: b.r }); });
      }
      add(bounds([[0, 0], [layoutSize.w, 0], [layoutSize.w, layoutSize.h], [0, layoutSize.h]].map(function (p) { return baseMatrix.transformPoint(new DOMPoint(p[0], p[1])); })));
      painted.forEach(function (e) {
        if (e.node === spec.root || within(e.node, n) || !e.node.rect || e.node.rect.w < 1 || e.node.rect.h < 1) return;
        add(bounds(quad(e.node, e.matrix)));
      });
      return { x: xs, y: ys };
    }
    function snapAxis(lines, targets) { // the smallest move that lays one of lines on a target, within 6 px
      var best = null;
      lines.forEach(function (m) {
        targets.forEach(function (t) { var d = t.v - m; if (Math.abs(d) <= 6 * px() && (best === null || Math.abs(d) < Math.abs(best))) best = d; });
      });
      return best || 0;
    }
    function guidesFor(box, targets, axes) { // the guide lines to draw for a box that has just snapped
      var out = [];
      [[box.l, (box.l + box.r) / 2, box.r, "x"], [box.t, (box.t + box.b) / 2, box.b, "y"]].forEach(function (axis) {
        var key = axis[3], lo = key === "x" ? box.t : box.l, hi = key === "x" ? box.b : box.r;
        if (axes && !axes[key]) return;
        targets[key].forEach(function (t) {
          if (axis.slice(0, 3).some(function (v) { return Math.abs(v - t.v) < 0.75; })) out.push({ axis: key, v: t.v, lo: Math.min(lo, t.lo), hi: Math.max(hi, t.hi) });
        });
      });
      return out;
    }
    function round(v, step) { return Math.round(v / step) * step; }
    function editDown(ev) {
      var pt = canvasPoint(ev), h = handleAt(pt);
      if (h) { startDrag("resize", selected, pt, h); return; }
      var target = hitAny(ev.clientX, ev.clientY), node = target ? owner(target) : null;
      // a press inside the selection moves it; a click there without moving picks what is under the cursor
      if (shown(selected) && inside(selected, selected.wm, pt)) { startDrag("move", selected, pt, null); drag.clickPick = node; return; }
      selected = node;
      emit("select", node ? describe(node) : {});
      if (node && shown(node)) startDrag("move", node, pt, null);
      invalidate();
    }
    function startDrag(kind, node, pt, h) {
      drag = { kind: kind, node: node, x: pt.x, y: pt.y, h: h, moved: false, canvasSlot: node.slot.type === "canvas", size: sizeMode(node),
        box: bounds(quad(node)), targets: snapTargets(node), guides: [], pm: node.pm, wm: node.wm,
        rect: { w: node.rect.w, h: node.rect.h }, offsets: node.slot.offsets ? node.slot.offsets.slice() : null,
        t: node.xform.t.slice(), s: node.xform.s.slice() };
    }
    function editMove(ev) {
      var pt = canvasPoint(ev);
      if (!drag) {
        var h = handleAt(pt), target = h ? null : hitAny(ev.clientX, ev.clientY), node = target ? owner(target) : null;
        canvas.style.cursor = spaceDown ? "grab" : h ? handleCursor(h) : (node || (shown(selected) && inside(selected, selected.wm, pt))) ? "move" : "default";
        if (node !== editHover) { editHover = node; invalidate(); }
        emit("pointer", { widget: node ? node.name : "", label: node ? node.label : "", kind: node ? node.kind : "" });
        return;
      }
      var sd = [pt.x - drag.x, pt.y - drag.y];
      if (!drag.moved && Math.hypot(sd[0], sd[1]) < 3 * px()) return;
      drag.moved = true; editHover = null;
      var snap = !(ev.ctrlKey || ev.metaKey); // Ctrl: no smart guides, as in Photoshop
      if (drag.kind === "move") moveTo(ev, sd, snap); else resizeTo(ev, sd, snap);
      invalidate();
    }
    function moveTo(ev, sd, snap) {
      var n = drag.node, b = drag.box;
      if (ev.shiftKey) { if (Math.abs(sd[0]) > Math.abs(sd[1])) sd[1] = 0; else sd[0] = 0; } // Shift: straight lines only
      drag.guides = [];
      if (snap) {
        var box = function () { return { l: b.l + sd[0], t: b.t + sd[1], r: b.r + sd[0], b: b.b + sd[1] }; }, m = box();
        if (!(ev.shiftKey && !sd[0])) sd[0] += snapAxis([m.l, (m.l + m.r) / 2, m.r], drag.targets.x);
        if (!(ev.shiftKey && !sd[1])) sd[1] += snapAxis([m.t, (m.t + m.b) / 2, m.b], drag.targets.y);
        drag.guides = guidesFor(box(), drag.targets);
      }
      var d = toLocal(drag.pm, sd[0], sd[1]);
      if (drag.canvasSlot) { n.slot.offsets[0] = Math.round(drag.offsets[0] + d[0]); n.slot.offsets[1] = Math.round(drag.offsets[1] + d[1]); }
      else n.xform.t = [Math.round(drag.t[0] + d[0] / (n.layoutScale || 1)), Math.round(drag.t[1] + d[1] / (n.layoutScale || 1))];
    }
    function resizeTo(ev, sd, snap) {
      var n = drag.node, h = drag.h, b = drag.box, centred = ev.altKey, keep = ev.shiftKey && h[0] && h[1];
      if (snap) { // the edges being pulled stick to the guides too
        if (h[0]) sd[0] += snapAxis([(h[0] < 0 ? b.l : b.r) + sd[0]], drag.targets.x);
        if (h[1]) sd[1] += snapAxis([(h[1] < 0 ? b.t : b.b) + sd[1]], drag.targets.y);
      }
      var local = toLocal(drag.wm, sd[0], sd[1]), size0 = [drag.rect.w, drag.rect.h], size = size0.slice();
      [0, 1].forEach(function (i) { if (h[i]) size[i] = size0[i] + h[i] * local[i] * (centred ? 2 : 1); });
      if (keep && size0[0] > 0 && size0[1] > 0) { // Shift on a corner: keep the proportions
        var fx = size[0] / size0[0], fy = size[1] / size0[1], k = Math.abs(fx - 1) > Math.abs(fy - 1) ? fx : fy;
        size = [size0[0] * k, size0[1] * k];
      }
      size = size.map(function (v) { return Math.max(1, Math.round(v)); });
      [0, 1].forEach(function (i) {
        if (!h[i]) return;
        // how far each edge moves, in the widget's own units: the far edge stays put (Alt: both, from the centre)
        var grow = size[i] - size0[i], dMin = centred ? -grow / 2 : h[i] < 0 ? -grow : 0, dMax = centred ? grow / 2 : h[i] > 0 ? grow : 0;
        if (drag.size) {
          var s = n.slot, o = drag.offsets;
          if (s.anchor_min[i] === s.anchor_max[i]) { s.offsets[i] = Math.round(o[i] + dMin + s.align[i] * grow); s.offsets[i + 2] = size[i]; }
          else { s.offsets[i] = Math.round(o[i] + dMin); s.offsets[i + 2] = Math.round(o[i + 2] - dMax); }
        } else { // render Scale, with the translation moved so the far edge stays where it was
          var s0 = drag.s[i], pivot = n.pivot[i] * size0[i], left = pivot * (1 - s0) + drag.t[i] + dMin * s0;
          n.xform.s[i] = round(s0 * size[i] / size0[i], 0.001);
          n.xform.t[i] = round(left - pivot * (1 - n.xform.s[i]), 0.1);
        }
      });
      drag.guides = snap ? guidesFor(predictedBox(), drag.targets, { x: !!h[0], y: !!h[1] }) : [];
    }
    function predictedBox() { // the dragged widget's box as the next frame will draw it
      var n = drag.node;
      layout();
      var m = n.pm.translate(n.rect.x, n.rect.y);
      if (n.layoutScale !== 1) m = m.scale(n.layoutScale, n.layoutScale);
      var t = n.xform.t, s = n.xform.s, p = [n.pivot[0] * n.rect.w, n.pivot[1] * n.rect.h];
      m = m.translate(p[0] + t[0], p[1] + t[1]);
      if (n.xform.angle) m = m.rotate(n.xform.angle);
      m = m.scale(s[0], s[1]).translate(-p[0], -p[1]);
      return bounds(quad(n, m));
    }
    function editUp() {
      if (!drag) return;
      var d = drag, n = d.node; drag = null;
      invalidate();
      if (!d.moved) {
        if (d.clickPick !== undefined && d.clickPick !== selected) { selected = d.clickPick; emit("select", selected ? describe(selected) : {}); }
        return;
      }
      var changes = [];
      if (d.offsets) [0, 1, 2, 3].forEach(function (i) { if (n.slot.offsets[i] !== d.offsets[i]) changes.push(["offsets." + i, n.slot.offsets[i], d.offsets[i]]); });
      if (!same2(n.xform.t, d.t)) changes.push(["translation", n.xform.t.slice(), d.t.slice()]);
      if (!same2(n.xform.s, d.s)) changes.push(["scale", n.xform.s.slice(), d.s.slice()]);
      if (changes.length) emit("edit", { node: describe(n), changes: changes });
    }
    function same2(a, b) { return a[0] === b[0] && a[1] === b[1]; }
    function pill(text, x, y, dpr) { // a small label on the canvas, centred on x, its top at y
      ctx.font = Math.round(11 * dpr) + "px ui-sans-serif, system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      var w = ctx.measureText(text).width + 12 * dpr, hgt = 18 * dpr;
      x = Math.max(w / 2 + 2, Math.min(canvas.width - w / 2 - 2, x)); y = Math.max(2, Math.min(canvas.height - hgt - 2, y));
      ctx.fillStyle = "#ff8a00"; ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x - w / 2, y, w, hgt, 4 * dpr); else ctx.rect(x - w / 2, y, w, hgt);
      ctx.fill(); ctx.fillStyle = "#fff"; ctx.fillText(text, x, y + hgt / 2 + 0.5);
    }
    function outline(pts, width, color) {
      ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
      pts.slice(1).forEach(function (p) { ctx.lineTo(p.x, p.y); });
      ctx.closePath(); ctx.lineWidth = width; ctx.strokeStyle = color; ctx.stroke();
    }
    function fmt(v) { return String(Math.round(v * 100) / 100); }
    function drawEditOverlay(dpr) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (editHover && editHover !== selected && shown(editHover)) outline(quad(editHover), 1 * dpr, "#4da3ff");
      if (!shown(selected)) return;
      var n = selected, q = quad(n), box = bounds(q), moving = drag && drag.moved && drag.kind === "move";
      if (drag && drag.moved) drag.guides.forEach(function (g) { // smart guides, in Photoshop's magenta
        var v = Math.round(g.v) + 0.5;
        ctx.beginPath();
        if (g.axis === "x") { ctx.moveTo(v, g.lo); ctx.lineTo(v, g.hi); } else { ctx.moveTo(g.lo, v); ctx.lineTo(g.hi, v); }
        ctx.lineWidth = 1 * dpr; ctx.strokeStyle = "#ff2bd6"; ctx.stroke();
      });
      outline(q, 1.5 * dpr, "#ff8a00");
      if (!moving) handlePoints(n).forEach(function (e) {
        var r = 4 * dpr;
        ctx.fillStyle = "#fff"; ctx.fillRect(e.p.x - r, e.p.y - r, 2 * r, 2 * r);
        ctx.lineWidth = 1.5 * dpr; ctx.strokeStyle = "#ff8a00"; ctx.strokeRect(e.p.x - r, e.p.y - r, 2 * r, 2 * r);
      });
      var scaled = n.xform.s[0] !== 1 || n.xform.s[1] !== 1, label;
      if (moving) label = drag.canvasSlot ? "X " + fmt(n.slot.offsets[0]) + "   Y " + fmt(n.slot.offsets[1]) : "Offset " + fmt(n.xform.t[0]) + ", " + fmt(n.xform.t[1]);
      else if (drag && drag.moved && !drag.size) label = "Scale " + fmt(n.xform.s[0]) + " × " + fmt(n.xform.s[1]);
      else label = fmt(n.rect.w) + " × " + fmt(n.rect.h) + (scaled ? "   scale " + fmt(n.xform.s[0]) + " × " + fmt(n.xform.s[1]) : "");
      pill(label, (box.l + box.r) / 2, box.b + 8 * dpr, dpr);
    }
    // ---- the view: zoom and pan ----------------------------------------
    function zoomAt(factor, cx, cy) {
      var z = Math.min(32, Math.max(0.1, view.zoom * factor)), k = z / view.zoom;
      view.x = cx - (cx - view.x) * k; view.y = cy - (cy - view.y) * k; view.zoom = z;
      invalidate();
    }
    function onWheel(ev) { // the wheel zooms towards the cursor, as in UEFN's designer
      ev.preventDefault();
      var pt = canvasPoint(ev), dy = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY;
      zoomAt(Math.exp(-dy * 0.0015), pt.x, pt.y);
    }
    function panDown(ev) { // right or middle button, or the left one with Space held: move the view
      var pt = canvasPoint(ev);
      drag = { kind: "pan", x: pt.x, y: pt.y, vx: view.x, vy: view.y };
      canvas.style.cursor = "grabbing";
      try { canvas.setPointerCapture(ev.pointerId); } catch (e) {}
    }
    function panMove(ev) {
      var pt = canvasPoint(ev);
      view.x = drag.vx + pt.x - drag.x; view.y = drag.vy + pt.y - drag.y; invalidate();
    }
    function onKey(ev) { // Space held over the preview: drag to move the view
      if (ev.key !== " " || /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target && ev.target.tagName) || "") || !canvas.matches(":hover")) return;
      ev.preventDefault();
      spaceDown = ev.type === "keydown";
      if (!drag) canvas.style.cursor = spaceDown ? "grab" : "default";
    }

    // ---- input ----------------------------------------------------------
    function hit(clientX, clientY) {
      var b = canvas.getBoundingClientRect(), sx = canvas.width / b.width, sy = canvas.height / b.height;
      var pt = new DOMPoint((clientX - b.left) * sx, (clientY - b.top) * sy);
      for (var i = painted.length - 1; i >= 0; i--) {
        var e = painted[i], vis = e.node.anim.visibility || e.node.visibility;
        if (vis !== "Visible") continue; // HitTestInvisible / SelfHitTestInvisible never take the hit
        if (!hitTarget(e.node)) continue; // layout containers are transparent to the cursor
        var local = e.matrix.inverse().transformPoint(pt);
        if (local.x >= 0 && local.y >= 0 && local.x <= e.node.rect.w && local.y <= e.node.rect.h) return e.node;
      }
      return null;
    }
    function hitTarget(n) { return n.kind === "Image" || n.kind === "TextBlock" || n.kind === "Button" || n.kind === "Border" || n.kind === "ProgressBar"; }
    function buttonOf(n) {
      for (var cur = n; cur; cur = cur.parent) {
        var v = cur.anim.visibility || cur.visibility;
        if (cur !== n && (v === "HitTestInvisible")) return null;
        if (cur.kind === "Button") return cur.enabled ? cur : null;
      }
      return null;
    }
    function onMove(ev) {
      if (drag && drag.kind === "pan") { panMove(ev); return; }
      if (editMode) { editMove(ev); return; }
      var target = hit(ev.clientX, ev.clientY), button = target ? buttonOf(target) : null;
      canvas.style.cursor = button ? "pointer" : "default";
      if (button !== hovered) {
        if (hovered) { emit("unhover", { widget: hovered.name, label: hovered.label }); fire(hovered, "unhover"); }
        hovered = button;
        if (button) { hoverAt = performance.now(); emit("hover", { widget: button.name, label: button.label }); uiSound(button, "hovered"); fire(button, "hover"); }
        invalidate();
      }
      emit("pointer", { widget: target ? target.name : "", label: target ? target.label : "", kind: target ? target.kind : "" });
    }
    function onDown(ev) {
      if (ev.button === 1 || ev.button === 2 || (ev.button === 0 && spaceDown)) { ev.preventDefault(); panDown(ev); return; }
      if (ev.button !== 0) return;
      if (editMode) { editDown(ev); if (drag) try { canvas.setPointerCapture(ev.pointerId); } catch (e) {} return; }
      audioAllowed = true;
      resumeAudio();
      var target = hit(ev.clientX, ev.clientY);
      pressed = target ? buttonOf(target) : null;
      if (pressed) { emit("press", { widget: pressed.name, label: pressed.label }); uiSound(pressed, "pressed"); fire(pressed, "press"); invalidate(); }
    }
    function onUp(ev) {
      if (drag && drag.kind === "pan") { drag = null; canvas.style.cursor = spaceDown ? "grab" : "default"; return; }
      if (editMode) { editUp(); return; }
      if (!pressed) return;
      var target = hit(ev.clientX, ev.clientY), was = pressed;
      pressed = null;
      emit("release", { widget: was.name, label: was.label });
      fire(was, "release");
      if (target && buttonOf(target) === was) { emit("click", { widget: was.name, label: was.label, text: was.props.text || "" }); fire(was, "click"); }
      invalidate();
    }
    function onLeave() {
      if (editHover && !drag) { editHover = null; invalidate(); }
      if (!hovered && !pressed) return;
      if (hovered) fire(hovered, "unhover");
      hovered = null; pressed = null; invalidate();
    }

    // ---- event bindings --------------------------------------------------
    // What the blueprint itself binds to a button's events (read from the file, not guessed):
    // play or reverse one of its animations, or call a function that lives in Verse.
    var EVENT_KIND = { OnButtonHighlight: "hover", OnHovered: "hover", OnButtonUnhighlight: "unhover", OnUnhovered: "unhover",
      OnButtonClicked: "click", OnClicked: "click", OnPressed: "press", OnReleased: "release" };
    function fire(button, kind) {
      var own = scopeOf(button);
      run(own, function (ev) { return ev.widget === button.name && !ev.via; }, own.owner ? own.owner.label : button.label);
      // the blueprint that contains this one may listen to the same button ("Card_1 -> Root_Button -> clicked")
      if (own.owner) run(scopeOf(own.owner), function (ev) { return ev.widget === own.owner.name && ev.via === button.name; }, own.owner.label);
      function run(scope, matches, who) { scope.events.forEach(function (ev) {
        if (!matches(ev) || EVENT_KIND[ev.event] !== kind) return;
        if (ev.action === "call") { emit("call", { widget: who, "function": ev["function"], event: ev.event }); return; }
        var anim = scope.anims.filter(function (a) { return a.name === ev.animation; })[0];
        if (!anim) return;
        if (ev.action === "stop") { players = players.filter(function (p) { return !(p.scope === scope && p.anim === anim); }); invalidate(); return; }
        startPlayer(scope, anim, { reverse: ev.action === "reverse", speed: ev.speed, loops: ev.loops });
        emit("bound", { widget: who, animation: anim.name, action: ev.action, event: ev.event });
      }); }
    }
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    function onMenu(ev) { ev.preventDefault(); } // the right button moves the view
    function onDouble() { if (editMode && selected) emit("open", describe(selected)); } // e.g. type into a text
    canvas.addEventListener("contextmenu", onMenu);
    canvas.addEventListener("dblclick", onDouble);
    window.addEventListener("keydown", onKey); window.addEventListener("keyup", onKey);
    var resizeObserver = new ResizeObserver(invalidate);
    resizeObserver.observe(canvas);

    // ---- audio ----------------------------------------------------------
    /* The browser's audio system is started on a click only. Creating it blocks the page for tens
     * of milliseconds (it opens the sound device), which made the first frame of a hover animation
     * jump; and a browser keeps audio silent until a click anyway. */
    var audioAllowed = false;
    function resumeAudio() {
      if (!audioAllowed) return null;
      if (!audio) { try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { audio = null; } }
      if (audio && audio.state === "suspended") audio.resume();
      return audio;
    }
    function loadSound(key) {
      var a = (spec.assets || {})[key];
      if (!a || !a.url || !resumeAudio()) return Promise.resolve(null);
      if (!audioBuffers[key]) {
        audioBuffers[key] = fetch(a.url).then(function (r) { return r.arrayBuffer(); })
          .then(function (buf) { return audio.decodeAudioData(buf); }).catch(function () { return null; });
      }
      return audioBuffers[key];
    }
    function playSound(key, opts) {
      opts = opts || {};
      return loadSound(key).then(function (buffer) {
        if (!buffer || muted) return null;
        var src = audio.createBufferSource(), gain = audio.createGain();
        src.buffer = buffer; src.playbackRate.value = opts.pitch || 1; src.loop = !!opts.loop;
        gain.gain.value = opts.volume == null ? 1 : opts.volume;
        src.connect(gain).connect(audio.destination);
        src.start(0, Math.max(0, opts.offset || 0));
        return src;
      });
    }
    function tick(freq, length, level) { // generic UI blip when the real sound is Fortnite content
      if (muted || !resumeAudio()) return;
      var osc = audio.createOscillator(), gain = audio.createGain(), t = audio.currentTime;
      osc.type = "triangle"; osc.frequency.setValueAtTime(freq, t); osc.frequency.exponentialRampToValueAtTime(freq * 0.6, t + length);
      gain.gain.setValueAtTime(level, t); gain.gain.exponentialRampToValueAtTime(0.0001, t + length);
      osc.connect(gain).connect(audio.destination); osc.start(t); osc.stop(t + length);
    }
    function uiSound(button, state) {
      var key = button.props[state + "_sound"];
      if (key && spec.assets[key] && spec.assets[key].url) { playSound(key); return; }
      if (options.genericUiSounds === false) return;
      if (state === "pressed") tick(520, 0.09, 0.12); else if (audio) tick(880, 0.04, 0.035);
    }

    // ---- animation ------------------------------------------------------
    var VISIBILITY = ["Visible", "Collapsed", "Hidden", "HitTestInvisible", "SelfHitTestInvisible"]; // ESlateVisibility order
    function clearAnim() { allNodes.forEach(function (n) { n.anim = {}; }); }
    function applyTracks(scope, anim, t) {
      anim.tracks.forEach(function (tr) {
        var n = tr.root ? scope.top : scope.byName[tr.slot_of || tr.widget];
        if (!n) return;
        var time = Math.max(tr.start, tr.end == null ? t : Math.min(t, tr.end)), c = tr.channels, v;
        if (tr.kind === "transform") {
          ["tx", "ty", "sx", "sy", "shx", "shy", "angle"].forEach(function (k) { v = evalChannel(c[k], time); if (v != null) n.anim[k] = v; });
        } else if (tr.kind === "float") {
          v = evalChannel(c.v, time);
          if (v == null) return;
          if (tr.prop === "RenderOpacity") n.anim.opacity = v; else if (tr.prop === "Percent") n.anim.percent = v;
        } else if (tr.kind === "color") {
          var base = n.props.color || [1, 1, 1, 1], out = base.slice();
          ["r", "g", "b", "a"].forEach(function (k, i) { v = evalChannel(c[k], time); if (v != null) out[i] = v; });
          n.anim.color = out;
        } else if (tr.kind === "margin") {
          var m = (tr.prop === "Padding" ? n.slot.pad : n.slot.offsets) || [0, 0, 0, 0], next = m.slice();
          ["l", "t", "r", "b"].forEach(function (k, i) { v = evalChannel(c[k], time); if (v != null) next[i] = v; });
          if (tr.prop === "Padding") n.anim.pad = next; else n.anim.offsets = next;
        } else if (tr.kind === "visibility") {
          var step = tr["default"], steps = tr.steps || [];
          for (var si = 0; si < steps.length; si++) { if (steps[si][0] <= time || si === 0) step = steps[si][1]; else break; }
          if (step != null && VISIBILITY[step]) n.anim.visibility = VISIBILITY[step];
        } else if (tr.kind === "material") {
          var mat = n.anim.mat || (n.anim.mat = { scalar: {}, vector: {} });
          Object.keys(c).forEach(function (k) {
            v = evalChannel(c[k], time);
            if (v == null) return;
            var parts = k.split(":");
            if (parts[0] === "s") mat.scalar[parts[1]] = v;
            else {
              var base = (n.props.brush && n.props.brush.params && n.props.brush.params.vector && n.props.brush.params.vector[parts[1]]) || mat.vector[parts[1]] || null;
              var vec = mat.vector[parts[1]] || (base ? base.slice() : [0, 0, 0, 1]);
              vec["rgba".indexOf(parts[2])] = v; mat.vector[parts[1]] = vec;
            }
          });
        }
      });
    }
    function applyPlayers() { // every running or held animation, oldest first
      clearAnim();
      players.forEach(function (p) { applyTracks(p.scope, p.anim, p.time); });
      dirty = true;
    }
    function stopSources(p) { p.sources.forEach(function (s) { try { s.stop(); } catch (e) {} }); p.sources = []; }
    function startAudio(p) {
      if (p.dir < 0) return;
      p.anim.audio.forEach(function (a) {
        if (a.end > p.time || !a.end) {
          playSound(a.sound, { volume: a.volume, pitch: a.pitch, loop: a.loop, offset: p.time - a.start })
            .then(function (src) { if (src && players.indexOf(p) >= 0) p.sources.push(src); else if (src) src.stop(); });
        }
      });
    }
    function removePlayers(test) {
      players = players.filter(function (p) { if (test(p)) { stopSources(p); return false; } return true; });
    }
    /* Forward: from the start, or from where a reverse run of the same animation is. Reverse: from where
     * it is, or from the end. A finished forward run holds its last frame, as UMG does; a finished
     * reverse run is back at the start and lets go. */
    function startPlayer(scope, anim, opts) {
      opts = opts || {};
      var p = players.filter(function (x) { return x.scope === scope && x.anim === anim; })[0];
      if (!p) {
        p = { scope: scope, anim: anim, time: opts.reverse ? anim.end : anim.start, sources: [] };
        players.push(p);
      } else if (!opts.reverse && !(p.state === "playing" && p.dir < 0)) p.time = anim.start;
      stopSources(p);
      if (opts.from != null) p.time = opts.from;
      p.dir = opts.reverse ? -1 : 1; p.speed = opts.speed > 0 ? opts.speed : 1; p.loop = !!opts.loop;
      p.manual = !!opts.manual; p.state = "playing"; p.last = performance.now();
      if (opts.manual) audioAllowed = true; // a Play button press is a click
      if (p.anim.audio.length) { resumeAudio(); startAudio(p); }
      invalidate();
      return p;
    }
    function stepPlayers(now) {
      players.slice().forEach(function (p) {
        if (p.state !== "playing") return;
        var dt = p.last ? Math.max(0, now - p.last) / 1000 : 0;
        p.last = now;
        p.time += dt * p.speed * p.dir;
        if (p.dir > 0 && p.time >= p.anim.end) {
          if (p.loop && p.anim.end > p.anim.start) { p.time = p.anim.start; stopSources(p); startAudio(p); }
          else { p.time = p.anim.end; p.state = "held"; stopSources(p); if (p.manual) emit("animation", { name: p.anim.name, time: p.time, state: "finished" }); return; }
        } else if (p.dir < 0 && p.time <= p.anim.start) {
          removePlayers(function (x) { return x === p; });
          return;
        }
        if (p.manual) emit("animation", { name: p.anim.name, time: p.time, state: "playing" });
      });
      applyPlayers();
    }

    var api = {
      spec: spec,
      on: function (fn) { listeners.push(fn); },
      setViewport: function (w, h) { viewport = { w: w, h: h }; view = { zoom: 1, x: 0, y: 0 }; invalidate(); },
      // "fit" (the whole screen in the panel), "actual" (100 %: one screen pixel per pixel), "in", "out"
      zoom: function (how) {
        var cx = canvas.width / 2, cy = canvas.height / 2;
        if (how === "fit") { view = { zoom: 1, x: 0, y: 0 }; invalidate(); }
        else if (how === "actual") zoomAt(px() / fitScale / view.zoom, cx, cy);
        else zoomAt(how === "in" ? 1.25 : 0.8, cx, cy);
        return shownPercent;
      },
      setHints: function (on) { showHints = !!on; invalidate(); },
      stats: function () {
        var avg = function (x) { return x.length ? x.reduce(function (t, v) { return t + v; }, 0) / x.length : 0; };
        return { fps: frameGaps.length ? 1000 / avg(frameGaps) : 0, frameMs: avg(frameTimes), hoverMs: hoverLatency,
          shaderMs: perfShown.render, copyMs: perfShown.copy, draws: perfShown.count, gpu: materialRenderer && materialRenderer.info,
          playing: players.filter(function (p) { return p.state === "playing"; }).map(function (p) { return p.anim.name + (p.loop ? " (loop)" : ""); }) };
      },
      toggleStats: function () { showStats = !showStats; frameTimes = []; frameGaps = []; invalidate(); return showStats; },
      setDesignTime: function (on) { designTime = !!on; invalidate(); },
      setEditMode: function (on) { editMode = !!on; drag = null; hovered = null; pressed = null; editHover = null; if (!on) selected = null; canvas.style.cursor = "default"; invalidate(); },
      selection: function () { return selected ? describe(selected) : null; },
      screenBox: function (id) { // where a widget is drawn, in CSS pixels of the page (tests, tools)
        var n = allNodes.filter(function (x) { return x.id === id; })[0], b = canvas.getBoundingClientRect(), k = b.width / canvas.width;
        if (!shown(n)) return null;
        var q = bounds(quad(n));
        return { left: b.left + q.l * k, top: b.top + q.t * k, right: b.left + q.r * k, bottom: b.top + q.b * k };
      },
      describeNode: function (id) { var n = allNodes.filter(function (x) { return x.id === id; })[0]; return n ? describe(n) : null; },
      select: function (id) {
        var n = allNodes.filter(function (x) { return x.id === id; })[0];
        selected = n && !n.nested ? n : null;
        emit("select", selected ? describe(selected) : {});
        invalidate();
      },
      tree: function () { // this Widget Blueprint's own widgets, in hierarchy order (nested blueprints stay closed)
        var out = [];
        (function walk(n, depth) {
          if (!n || n.nested) return;
          out.push({ id: n.id, label: n.label, kind: n.kind, depth: depth,
            hidden: (n.visibility === "Collapsed" || n.visibility === "Hidden") });
          n.children.forEach(function (c) { walk(c, depth + 1); });
        })(spec.root, 0);
        return out;
      },
      setField: function (id, field, value) {
        var n = allNodes.filter(function (x) { return x.id === id; })[0];
        return n ? applyField(n, field, value) : false;
      },
      images: function () { // pictures this widget already references (what an image can be swapped to)
        return Object.keys(spec.assets || {}).filter(function (k) { var a = spec.assets[k]; return a.kind === "texture" || a.kind === "material"; })
          .map(function (k) { return { key: k, name: spec.assets[k].name, kind: spec.assets[k].kind }; });
      },
      setMuted: function (on) { muted = !!on; if (muted) players.forEach(stopSources); },
      switchers: function () {
        return allNodes.filter(function (n) { return n.kind === "WidgetSwitcher" && !n.nested; })
          .map(function (n) { return { id: n.id, label: n.label, active: n.props.active, count: n.children.length }; });
      },
      setSwitcher: function (id, index) {
        var n = allNodes.filter(function (x) { return x.id === id; })[0];
        if (n && n.children.length) { n.props.active = (index + n.children.length) % n.children.length; invalidate(); return n.props.active; }
        return 0;
      },
      kindOf: function (name) { var n = nodesByName[name]; return n ? n.kind : ""; },
      highlight: function (name) { // outline this blueprint's widget called name (null: none)
        var n = name ? nodesByName[name] : null;
        if (n !== highlighted) { highlighted = n || null; invalidate(); }
        return !!n;
      },
      setText: function (name, text) { var n = nodesByName[name]; if (n) { n.props.text = text; invalidate(); } },
      play: function (name, opts) {
        var anim = (spec.animations || []).filter(function (a) { return a.name === name; })[0];
        if (!anim) return false;
        opts = opts || {};
        removePlayers(function (p) { return p.manual; });
        var player = startPlayer(rootScope, anim, { from: opts.from == null ? anim.start : opts.from, loop: opts.loop, speed: opts.speed, manual: true });
        emit("animation", { name: name, time: player.time, state: "started" });
        return true;
      },
      seek: function (name, time) {
        var anim = (spec.animations || []).filter(function (a) { return a.name === name; })[0];
        if (!anim) return;
        removePlayers(function (p) { return p.manual; });
        players.push({ scope: rootScope, anim: anim, time: time, dir: 1, speed: 1, loop: false, state: "held", last: 0, sources: [], manual: true });
        applyPlayers(); invalidate();
      },
      stop: function () { removePlayers(function () { return true; }); clearAnim(); emit("animation", { state: "stopped" }); invalidate(); },
      materialReport: function () { return { shaders: Object.keys(materials).length, failed: materialErrors.slice() }; },
      missingMedia: function () {
        return Object.keys(spec.assets || {}).filter(function (k) { return !spec.assets[k].url; }).map(function (k) { return spec.assets[k]; });
      },
      dispose: function () {
        disposed = true; removePlayers(function () { return true; }); resizeObserver.disconnect();
        canvas.removeEventListener("pointermove", onMove); canvas.removeEventListener("pointerdown", onDown);
        window.removeEventListener("pointerup", onUp); canvas.removeEventListener("pointerleave", onLeave);
        canvas.removeEventListener("wheel", onWheel); canvas.removeEventListener("contextmenu", onMenu); canvas.removeEventListener("dblclick", onDouble);
        window.removeEventListener("keydown", onKey); window.removeEventListener("keyup", onKey);
        if (audio) audio.close();
      }
    };
    invalidate();
    return api;
  }

  window.UmgEngine = { create: create, evalChannel: evalChannel };
})();
