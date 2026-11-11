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
    var playing = null; // {anim, time, loop, speed, last, sources}
    var audio = null, audioBuffers = {}, muted = false;
    var showHints = false;

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

    // ---- assets ---------------------------------------------------------
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
      return (italic ? "italic " : "") + weight + " " + (font.size * PT_TO_PX) + "px " +
        (options.fontFamily || '"Segoe UI", "Noto Sans", Arial, sans-serif');
    }
    function textOf(node) {
      var p = node.props, s = node.anim.text != null ? node.anim.text : p.text || "";
      if (p.transform === "ToUpper") s = s.toUpperCase(); else if (p.transform === "ToLower") s = s.toLowerCase();
      return s;
    }
    function textLines(node, maxWidth) {
      var p = node.props;
      ctx.font = fontCss(p.font);
      try { ctx.letterSpacing = (p.font.letter_spacing / 1000 * p.font.size * PT_TO_PX) + "px"; } catch (e) {}
      var limit = p.wrap_at > 0 ? p.wrap_at : (p.wrap && maxWidth > 0 ? maxWidth : 0);
      var out = [];
      textOf(node).split("\n").forEach(function (para) {
        if (!limit) { out.push(para); return; }
        var line = "";
        para.split(" ").forEach(function (word) {
          var next = line ? line + " " + word : word;
          if (line && ctx.measureText(next).width > limit) { out.push(line); line = word; } else line = next;
        });
        out.push(line);
      });
      return out;
    }
    function measureText(node, maxWidth) {
      var p = node.props, lines = textLines(node, maxWidth), w = 0;
      lines.forEach(function (l) { w = Math.max(w, ctx.measureText(l).width); });
      var lineH = p.font.size * PT_TO_PX * 1.2 * (p.line_height || 1), o = p.font.outline_size || 0;
      return [w + o * 2, lines.length * lineH + o * 2];
    }

    // ---- layout ---------------------------------------------------------
    function collapsed(n) { return (n.anim.visibility || n.visibility) === "Collapsed"; }
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

    function drawBrush(brush, color, w, h) {
      if (!brush || brush.draw === "NoDrawType" || w <= 0 || h <= 0) return;
      var tint = mul(brush.tint, color);
      if (brush.draw === "RoundedBox") {
        var o = brush.outline || { radii: [0, 0, 0, 0], width: 0, color: [0, 0, 0, 0], rounding: "FixedRadius" };
        var radii = o.rounding === "HalfHeightRadius" ? [h / 2, h / 2, h / 2, h / 2] : o.radii, half = o.width / 2;
        roundRect(half, half, w - o.width, h - o.width, radii);
        ctx.fillStyle = css(tint); ctx.fill();
        if (o.width > 0 && o.color[3] > 0) { ctx.lineWidth = o.width; ctx.strokeStyle = css(mul(o.color, [1, 1, 1, color[3]])); ctx.stroke(); }
        return;
      }
      if (!brush.resource) { ctx.fillStyle = css(tint); ctx.fillRect(0, 0, w, h); return; }
      var asset = spec.assets[brush.resource] || {};
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
      lines.forEach(function (line, i) {
        var y = o + lineH * (i + 0.5);
        if (p.shadow_color[3] > 0) { ctx.fillStyle = css(p.shadow_color); ctx.fillText(line, x + p.shadow_offset[0], y + p.shadow_offset[1]); }
        if (o > 0) { ctx.lineJoin = "round"; ctx.lineWidth = o * 2; ctx.strokeStyle = css(f.outline_color); ctx.strokeText(line, x, y); }
        ctx.fillStyle = css(color); ctx.fillText(line, x, y);
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
      var vis = n.anim.visibility || n.visibility;
      if (vis === "Collapsed" || !n.rect) return;
      var r = n.rect, a = n.anim, x = n.xform;
      var t = [a.tx != null ? a.tx : x.t[0], a.ty != null ? a.ty : x.t[1]];
      var s = [a.sx != null ? a.sx : x.s[0], a.sy != null ? a.sy : x.s[1]];
      var sh = [a.shx != null ? a.shx : x.shear[0], a.shy != null ? a.shy : x.shear[1]];
      var angle = a.angle != null ? a.angle : x.angle;
      var opacity = alpha * (a.opacity != null ? a.opacity : n.opacity) * (n.enabled ? 1 : 0.5);
      ctx.save();
      ctx.translate(r.x, r.y);
      if (n.layoutScale !== 1) ctx.scale(n.layoutScale, n.layoutScale);
      if (pressed === n && n.kind === "Button") { ctx.translate(r.w / 2, r.h / 2); ctx.scale(0.97, 0.97); ctx.translate(-r.w / 2, -r.h / 2); }
      var px = n.pivot[0] * r.w, py = n.pivot[1] * r.h;
      ctx.translate(px + t[0], py + t[1]);
      if (angle) ctx.rotate(angle * Math.PI / 180);
      if (sh[0] || sh[1]) ctx.transform(1, Math.tan(sh[1] * Math.PI / 180), Math.tan(sh[0] * Math.PI / 180), 1, 0, 0);
      ctx.scale(s[0], s[1]);
      ctx.translate(-px, -py);
      if (vis !== "Hidden" && opacity > 0.001) {
        ctx.globalAlpha = opacity;
        painted.push({ node: n, matrix: ctx.getTransform() });
        if (n.clip) { ctx.beginPath(); ctx.rect(0, 0, r.w, r.h); ctx.clip(); }
        if (n.kind === "Image") drawBrush(n.props.brush, a.color || n.props.color, r.w, r.h);
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

    function frame(now) {
      raf = 0;
      if (disposed) return;
      if (playing) stepAnimation(now);
      if (!dirty && !playing) return;
      dirty = false;
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
      ctx.setTransform(fit * dpi, 0, 0, fit * dpi, ox, oy);
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
      if (playing) raf = requestAnimationFrame(frame);
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
      var target = hit(ev.clientX, ev.clientY), button = target ? buttonOf(target) : null;
      canvas.style.cursor = button ? "pointer" : "default";
      if (button !== hovered) {
        if (hovered) emit("unhover", { widget: hovered.name, label: hovered.label });
        hovered = button;
        if (button) { emit("hover", { widget: button.name, label: button.label }); uiSound(button, "hovered"); }
        invalidate();
      }
      emit("pointer", { widget: target ? target.name : "", label: target ? target.label : "", kind: target ? target.kind : "" });
    }
    function onDown(ev) {
      if (ev.button !== 0) return;
      resumeAudio();
      var target = hit(ev.clientX, ev.clientY);
      pressed = target ? buttonOf(target) : null;
      if (pressed) { emit("press", { widget: pressed.name, label: pressed.label }); uiSound(pressed, "pressed"); invalidate(); }
    }
    function onUp(ev) {
      if (!pressed) return;
      var target = hit(ev.clientX, ev.clientY), was = pressed;
      pressed = null;
      emit("release", { widget: was.name, label: was.label });
      if (target && buttonOf(target) === was) emit("click", { widget: was.name, label: was.label, text: was.props.text || "" });
      invalidate();
    }
    function onLeave() { if (hovered || pressed) { hovered = null; pressed = null; invalidate(); } }
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    var resizeObserver = new ResizeObserver(invalidate);
    resizeObserver.observe(canvas);

    // ---- audio ----------------------------------------------------------
    function resumeAudio() {
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
    function clearAnim() { allNodes.forEach(function (n) { n.anim = {}; }); }
    function applyAnimation(anim, t) {
      clearAnim();
      anim.tracks.forEach(function (tr) {
        var n = tr.root ? spec.root : nodesByName[tr.slot_of || tr.widget];
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
        }
      });
    }
    function stopSources() { if (playing) playing.sources.forEach(function (s) { try { s.stop(); } catch (e) {} }); }
    function startAudio(state) {
      state.anim.audio.forEach(function (a) {
        if (a.end > state.time || !a.end) {
          playSound(a.sound, { volume: a.volume, pitch: a.pitch, loop: a.loop, offset: state.time - a.start })
            .then(function (src) { if (src && playing === state) state.sources.push(src); else if (src) src.stop(); });
        }
      });
    }
    function stepAnimation(now) {
      var s = playing, dt = s.last ? (now - s.last) / 1000 : 0;
      s.last = now;
      if (!s.paused) s.time += dt * s.speed;
      var end = s.anim.end;
      if (s.time >= end) {
        if (s.loop && end > s.anim.start) { s.time = s.anim.start; stopSources(); s.sources = []; startAudio(s); }
        else { s.time = end; applyAnimation(s.anim, end); emit("animation", { name: s.anim.name, time: end, state: "finished" }); stopSources(); playing = null; dirty = true; return; }
      }
      applyAnimation(s.anim, s.time);
      emit("animation", { name: s.anim.name, time: s.time, state: "playing" });
      dirty = true;
    }

    var api = {
      spec: spec,
      on: function (fn) { listeners.push(fn); },
      setViewport: function (w, h) { viewport = { w: w, h: h }; invalidate(); },
      setHints: function (on) { showHints = !!on; invalidate(); },
      setMuted: function (on) { muted = !!on; if (muted) stopSources(); },
      switchers: function () {
        return allNodes.filter(function (n) { return n.kind === "WidgetSwitcher" && !n.nested; })
          .map(function (n) { return { id: n.id, label: n.label, active: n.props.active, count: n.children.length }; });
      },
      setSwitcher: function (id, index) {
        var n = allNodes.filter(function (x) { return x.id === id; })[0];
        if (n && n.children.length) { n.props.active = (index + n.children.length) % n.children.length; invalidate(); return n.props.active; }
        return 0;
      },
      setText: function (name, text) { var n = nodesByName[name]; if (n) { n.props.text = text; invalidate(); } },
      play: function (name, opts) {
        var anim = (spec.animations || []).filter(function (a) { return a.name === name; })[0];
        if (!anim) return false;
        opts = opts || {};
        stopSources();
        playing = { anim: anim, time: opts.from == null ? anim.start : opts.from, loop: !!opts.loop, speed: opts.speed || 1, last: 0, sources: [], paused: false };
        resumeAudio(); startAudio(playing);
        emit("animation", { name: name, time: playing.time, state: "started" });
        invalidate();
        return true;
      },
      seek: function (name, time) {
        var anim = (spec.animations || []).filter(function (a) { return a.name === name; })[0];
        if (!anim) return;
        stopSources(); playing = null; applyAnimation(anim, time); invalidate();
      },
      stop: function () { stopSources(); playing = null; clearAnim(); emit("animation", { state: "stopped" }); invalidate(); },
      missingMedia: function () {
        return Object.keys(spec.assets || {}).filter(function (k) { return !spec.assets[k].url; }).map(function (k) { return spec.assets[k]; });
      },
      dispose: function () {
        disposed = true; stopSources(); resizeObserver.disconnect();
        canvas.removeEventListener("pointermove", onMove); canvas.removeEventListener("pointerdown", onDown);
        window.removeEventListener("pointerup", onUp); canvas.removeEventListener("pointerleave", onLeave);
        if (audio) audio.close();
      }
    };
    invalidate();
    return api;
  }

  window.UmgEngine = { create: create, evalChannel: evalChannel };
})();
