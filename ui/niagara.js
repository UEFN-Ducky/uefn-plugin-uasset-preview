/* Niagara system preview: what the effect is made of (backend/preview/niagara_preview.py).
 * The particles are not simulated here. UassetNiagara.mount(host, data) fills host. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function srgb(v) { v = Math.max(0, Math.min(1, v)); return v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055; }
  /* A linear colour as CSS; HDR colours (a glow, above 1) are scaled down to their hue. */
  function css(c) {
    var peak = Math.max(1, c[0], c[1], c[2]);
    return "rgb(" + [0, 1, 2].map(function (i) { return Math.round(255 * srgb(c[i] / peak)); }).join(",") + ")";
  }
  function swatch(c, title) {
    var s = el("span", "ns-swatch");
    s.style.background = css(c);
    s.title = title || c.map(function (v) { return +v.toFixed(3); }).join(", ");
    return s;
  }
  function format(v) {
    if (Array.isArray(v)) return "(" + v.map(function (x) { return +x.toFixed(3); }).join(", ") + ")";
    return typeof v === "number" ? String(+v.toFixed(4)) : String(v);
  }

  function look(box, l, label) {
    var line = el("div", "ns-look");
    if (l.icon) { var img = el("img", "ns-icon"); img.src = l.icon; img.alt = l.texture || ""; img.title = l.texture || ""; line.appendChild(img); }
    else if (l.color) line.appendChild(swatch(l.color));
    line.appendChild(el("span", "", (label ? label + ": " : "") + (l.material || "?")));
    if (l.texture) line.appendChild(el("span", "ns-muted", " — texture " + l.texture + (l.how === "parameter name" ? " (guessed from the parameter name)" : "")));
    if (l.why) line.appendChild(el("span", "ns-muted", " — " + l.why));
    box.appendChild(line);
  }

  function mount(host, data) {
    host.innerHTML = "";
    var head = el("div", "ns-head");
    if (data.thumbnail) { var t = el("img", "ns-thumb"); t.src = data.thumbnail; t.alt = "thumbnail saved by UEFN"; head.appendChild(t); }
    var emitters = data.emitters || [], on = emitters.filter(function (e) { return e.enabled; }).length;
    var text = el("div", "ns-intro");
    text.appendChild(el("div", "ns-title", "Niagara System · " + emitters.length + " emitter" + (emitters.length === 1 ? "" : "s") + (on !== emitters.length ? " (" + on + " on)" : "")));
    var simulated = data.simulation && data.simulation.emitters && data.simulation.emitters.length;
    text.appendChild(el("div", "ns-muted", (simulated ? "Below: the emitters this preview can run itself, then what the effect is made of, read from the file."
      : "This is what the effect is made of, read from the file (no emitter could be run here).") + (data.thumbnail ? " The picture is the thumbnail UEFN saved." : "")));
    head.appendChild(text);
    host.appendChild(head);

    // Experimental: the emitters' own compiled particle scripts, run here (ui/niagara_sim.js)
    var sim = null, simData = data.simulation || {};
    if (window.UassetNiagaraSim && simData.emitters && simData.emitters.length) {
      var box = el("div", "ns-sim"), canvas = el("canvas", "ns-sim-canvas"), bar = el("div", "ns-sim-bar");
      var pause = el("button", "", "Pause"), again = el("button", "", "Restart"), info = el("span", "ns-muted");
      pause.type = again.type = "button";
      bar.appendChild(pause); bar.appendChild(again); bar.appendChild(info);
      box.appendChild(canvas); box.appendChild(bar);
      host.appendChild(box);
      sim = window.UassetNiagaraSim.create(canvas, simData.emitters, simData.system);
      var playing = true;
      pause.onclick = function () { playing = !playing; pause.textContent = playing ? "Pause" : "Play"; if (playing) sim.start(); else sim.stop(); };
      again.onclick = function () { sim.restart(); };
      var off = (simData.skipped || []).concat(sim.skipped);
      info.textContent = "Experimental: " + sim.running + " emitter" + (sim.running === 1 ? "" : "s") + " run from their compiled scripts (sprites with their texture, other renderers as glowing points)"
        + (off.length ? " · not shown: " + off.map(function (o) { return o.name + " (" + o.why + ")"; }).join("; ") : "");
      if (sim.running) sim.start();
    } else if ((simData.skipped || []).length) {
      host.appendChild(el("div", "ns-muted", "Not simulated: " + simData.skipped.map(function (o) { return o.name + " (" + o.why + ")"; }).join("; ")));
    }

    emitters.forEach(function (e) {
      var card = el("div", "ns-card" + (e.enabled ? "" : " off"));
      var title = el("div", "ns-card-title");
      if (e.particle_color) title.appendChild(swatch(e.particle_color, "particle colour " + format(e.particle_color)));
      title.appendChild(el("strong", "", e.name || "?"));
      [e.enabled ? null : "off", e.sim, e.local_space ? "local space" : null].forEach(function (b) { if (b) title.appendChild(el("span", "ns-badge", b)); });
      card.appendChild(title);
      (e.renderers || []).forEach(function (r) {
        var row = el("div", "ns-renderer");
        row.appendChild(el("span", "ns-type", r.type + (r.disabled ? " (off)" : "") + (r.flipbook ? " · flipbook " + r.flipbook[0] + "×" + r.flipbook[1] : "")));
        if (r.meshes) row.appendChild(el("span", "", " · " + r.meshes.join(", ")));
        card.appendChild(row);
        if (r.look) look(card, r.look);
        (r.override_looks || []).forEach(function (l) { look(card, l, "override"); });
        (r.mesh_looks || []).forEach(function (l) { look(card, l, "mesh material"); });
      });
      if (e.summary && e.summary.length) {
        var chips = el("div", "ns-chips");
        e.summary.forEach(function (s) { chips.appendChild(el("span", "ns-chip", s[0] + " " + s[1])); });
        card.appendChild(chips);
      }
      if (e.parameters && e.parameters.length) {
        var more = el("details", "ns-params"), sum = el("summary", "", "All " + e.parameters.length + " parameters");
        var table = el("table");
        e.parameters.forEach(function (p) {
          var tr = el("tr"); tr.appendChild(el("td", "ns-muted", p[0])); tr.appendChild(el("td", "", format(p[1]))); table.appendChild(tr);
        });
        more.appendChild(sum); more.appendChild(table); card.appendChild(more);
      }
      host.appendChild(card);
    });
    return { state: function () { return { emitters: emitters.length, thumbnail: !!data.thumbnail, cards: host.querySelectorAll(".ns-card").length, sim: sim ? sim.state() : null }; } };
  }

  window.UassetNiagara = { mount: mount };
})();
