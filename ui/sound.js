/* Sound preview: waveform, playback of a SoundWave, and of a SoundCue's node tree the way
 * UEFN plays it (backend/preview/sound_preview.py builds the data).
 * UassetSound.mount(host, data) fills host and returns { stop(), state() }. */
(function () {
  "use strict";

  var FRAME_MS = 1000 / 60 - 2; // playhead: 60 images a second are plenty
  var MAX_GAIN = 1; // a cue may ask for x5: played at x1 (noted), never louder than the file

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function seconds(s) { return (s || 0).toFixed(s >= 10 ? 1 : 2) + " s"; }
  function between(lo, hi, fallback) {
    lo = lo == null ? fallback : lo; hi = hi == null ? lo : hi;
    return lo + Math.random() * (hi - lo);
  }

  /* Decoding needs no click when done by an OfflineAudioContext; the buffer then plays in
   * the real AudioContext created on the first click (browsers keep audio silent before). */
  var decoder = null;
  function decode(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.arrayBuffer();
    }).then(function (data) {
      if (!decoder) decoder = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 1, 44100);
      return decoder.decodeAudioData(data);
    });
  }

  function mount(host, data) {
    host.innerHTML = "";
    var canvas = el("canvas", "snd-wave"), bar = el("div", "snd-bar"), list = el("ul", "snd-list"), notes = el("div", "snd-notes");
    var play = el("button", "", data.kind === "cue" ? "▶ Play cue" : "▶ Play"), stop = el("button", "", "■ Stop");
    var info = el("span", "snd-info");
    play.type = stop.type = "button";
    bar.appendChild(play); bar.appendChild(stop); bar.appendChild(info);
    host.appendChild(canvas); host.appendChild(bar); host.appendChild(notes); host.appendChild(list);

    var buffers = {}, ctx = null, sources = [], shown = null, startedAt = 0, endsAt = 0, raf = 0, drawnAt = 0;
    var waves = data.waves || {}, paths = Object.keys(waves), played = 0;

    function note(text) {
      if (!text || notes.textContent.indexOf(text) >= 0) return;
      notes.appendChild(el("div", "", text));
    }
    function audio() {
      if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }

    // ---- waveform -------------------------------------------------------
    function draw(now) {
      var w = Math.max(200, host.clientWidth - 24), h = 120, dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      canvas.style.width = w + "px"; canvas.style.height = h + "px";
      var g = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
      g.clearRect(0, 0, W, H);
      g.fillStyle = "#16161a"; g.fillRect(0, 0, W, H);
      var buf = shown && buffers[shown];
      if (!buf || !buf.getChannelData) {
        var label = shown ? "Loading…" : (data.kind === "cue" && !data.first) ? "Empty cue: silence" : "No sound to play";
        g.fillStyle = "#9a9aa3"; g.font = 12 * dpr + "px system-ui"; g.fillText(label, 10 * dpr, H / 2);
        return;
      }
      if (!buf.peaks || buf.peaks.length !== W) buf.peaks = peaks(buf, W);
      g.fillStyle = "#6c8cff";
      for (var x = 0; x < W; x++) {
        var lo = buf.peaks[x][0], hi = buf.peaks[x][1];
        g.fillRect(x, (1 - hi) * H / 2, 1, Math.max(1, (hi - lo) * H / 2));
      }
      if (ctx && now != null && endsAt > startedAt) {
        var t = (ctx.currentTime - startedAt) / (endsAt - startedAt);
        if (t >= 0 && t <= 1) { g.fillStyle = "#e8e8ec"; g.fillRect(Math.round(t * W), 0, Math.max(1, dpr), H); }
      }
    }
    function peaks(buf, columns) {
      var out = new Array(columns), channels = [];
      for (var c = 0; c < buf.numberOfChannels; c++) channels.push(buf.getChannelData(c));
      var step = buf.length / columns;
      for (var x = 0; x < columns; x++) {
        var lo = 0, hi = 0, from = Math.floor(x * step), to = Math.min(buf.length, Math.floor((x + 1) * step) || from + 1);
        for (var i = from; i < to; i++) for (c = 0; c < channels.length; c++) {
          var v = channels[c][i]; if (v < lo) lo = v; if (v > hi) hi = v;
        }
        out[x] = [lo, hi];
      }
      return out;
    }
    function animate() {
      raf = requestAnimationFrame(function (now) {
        raf = 0;
        if (!ctx || ctx.currentTime > endsAt + 0.05) { draw(); return; }
        if (now - drawnAt >= FRAME_MS) { drawnAt = now; draw(now); }
        animate();
      });
    }

    // ---- playback -------------------------------------------------------
    function stopAll() {
      sources.forEach(function (s) { try { s.stop(); } catch (e) {} });
      sources = []; endsAt = 0;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      draw();
    }
    function startWave(path, when, volume, pitch, loop) {
      var buf = buffers[path];
      if (!buf || !buf.getChannelData) { note("Not played: " + ((waves[path] || {}).name || path) + " (" + ((waves[path] || {}).missing || "not loaded") + ")"); return 0; }
      if (volume > MAX_GAIN) { note("The cue sets volume x" + volume.toFixed(2) + "; played at x" + MAX_GAIN + "."); volume = MAX_GAIN; }
      var a = audio(), src = a.createBufferSource(), gain = a.createGain();
      src.buffer = buf; src.playbackRate.value = pitch; src.loop = !!loop;
      gain.gain.value = volume;
      src.connect(gain).connect(a.destination);
      src.start(a.currentTime + when);
      sources.push(src);
      shown = path; played++;
      return buf.duration / pitch; // a looping wave counts once
    }
    /* Schedules node `id` at `when` seconds from now; returns how long it lasts. */
    function playNode(id, when, volume, pitch, depth) {
      var node = (data.nodes || {})[id];
      if (!node || depth > 40) return 0;
      var p = node.p || {}, kids = node.children || [], i, longest = 0, total = 0;
      switch (node.op) {
        case "WavePlayer":
          return node.wave ? startWave(node.wave, when, volume, pitch, p.bLooping) : 0;
        case "Random": {
          var weights = kids.map(function (_k, j) { return (p.Weights && p.Weights[j] != null) ? Math.max(0, p.Weights[j]) : 1; });
          var sum = weights.reduce(function (a, b) { return a + b; }, 0), r = Math.random() * sum;
          for (i = 0; i < kids.length; i++) { r -= weights[i]; if (r <= 0) break; }
          return kids.length ? playNode(kids[Math.min(i, kids.length - 1)], when, volume, pitch, depth + 1) : 0;
        }
        case "Modulator":
          return kids.length ? playNode(kids[0], when, volume * between(p.VolumeMin, p.VolumeMax, 1), pitch * between(p.PitchMin, p.PitchMax, 1), depth + 1) : 0;
        case "Mixer":
          for (i = 0; i < kids.length; i++) {
            var v = p.InputVolume && p.InputVolume[i] != null ? p.InputVolume[i] : 1;
            longest = Math.max(longest, playNode(kids[i], when, volume * v, pitch, depth + 1));
          }
          return longest;
        case "Concatenator":
          for (i = 0; i < kids.length; i++) {
            var cv = p.InputVolume && p.InputVolume[i] != null ? p.InputVolume[i] : 1;
            total += playNode(kids[i], when + total, volume * cv, pitch, depth + 1);
          }
          return total;
        case "Delay": {
          var wait = between(p.DelayMin, p.DelayMax, 0);
          return wait + (kids.length ? playNode(kids[0], when + wait, volume, pitch, depth + 1) : 0);
        }
        case "Looping":
          note("A Looping node repeats this in the game; the preview plays it once.");
          return kids.length ? playNode(kids[0], when, volume, pitch, depth + 1) : 0;
        default:
          // nodes that change how the sound is heard in the world (attenuation, ...) pass their child through
          if (kids.length === 1) {
            if (node.op !== "Attenuation") note("Node " + node.op + " is played through, its effect is not heard here.");
            return playNode(kids[0], when, volume, pitch, depth + 1);
          }
          note("Node " + node.op + " is not supported: only its first input is played.");
          return kids.length ? playNode(kids[0], when, volume, pitch, depth + 1) : 0;
      }
    }
    function start(fn) {
      stopAll();
      notes.innerHTML = "";
      var a = audio(), length = fn();
      startedAt = a.currentTime; endsAt = startedAt + length;
      draw(0); animate();
    }

    play.onclick = function () {
      if (data.kind === "cue") start(function () { return playNode(data.first, 0, 1, 1, 0); });
      else if (paths.length) start(function () { return startWave(paths[0], 0, 1, 1, false); });
    };
    stop.onclick = stopAll;

    // ---- list of waves --------------------------------------------------
    paths.forEach(function (path) {
      var w = waves[path], li = el("li", w.url ? "" : "missing");
      var name = el("span", "snd-name", w.name), meta = el("span", "snd-meta");
      meta.textContent = w.url
        ? [seconds(w.duration), w.channels === 1 ? "mono" : w.channels === 2 ? "stereo" : (w.channels || "?") + " ch", (w.sample_rate / 1000) + " kHz", (w.bits || "?") + "-bit"].join(" · ")
        : (w.missing || "not available");
      if (w.url && data.kind === "cue") {
        var b = el("button", "", "▶"); b.type = "button"; b.title = "Play this sound alone";
        b.onclick = function () { start(function () { return startWave(path, 0, 1, 1, false); }); };
        li.appendChild(b);
      }
      li.appendChild(name); li.appendChild(meta);
      li.onclick = function (ev) { if (ev.target.tagName !== "BUTTON" && w.url) { shown = path; draw(); } };
      list.appendChild(li);
    });

    var available = paths.filter(function (p) { return waves[p].url; });
    shown = available[0] || null;
    info.textContent = (data.kind === "cue" ? "Sound Cue · " + paths.length + " sound" + (paths.length === 1 ? "" : "s") : "Sound Wave")
      + (data.duration ? " · " + seconds(data.duration) : "");
    if (!available.length) play.disabled = true;
    var empty = data.kind === "cue" && !data.first;
    if (empty) note("This Sound Cue is empty: nothing is connected to its Output node, so it plays silence (" + seconds(data.duration) + " in UEFN).");
    draw();
    Promise.all(available.map(function (p) {
      return decode(waves[p].url).then(function (buf) { buffers[p] = buf; }, function (e) { waves[p].missing = "could not be decoded (" + (e.message || e) + ")"; });
    })).then(function () { draw(); });
    window.addEventListener("resize", function () { if (!raf) draw(); });

    return {
      stop: stopAll,
      state: function () {
        return { decoded: Object.keys(buffers).length, waves: paths.length, played: played, playing: !!(ctx && ctx.currentTime < endsAt),
                 shown: shown && waves[shown].name, notes: notes.textContent };
      }
    };
  }

  window.UassetSound = { mount: mount };
})();
