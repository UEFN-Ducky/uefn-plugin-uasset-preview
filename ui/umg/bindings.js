/* View Bindings panel of the UMG preview: one card per binding, as UEFN's View Bindings panel
 * holds them (values, events, conditions). Read-only. Hovering a card outlines the widget it
 * drives; an animation it plays can be tried in the preview. */
(function () {
  "use strict";

  // 16 px line icons (stroke follows the text colour)
  var PATHS = {
    link: '<path d="M6.5 9.5l3-3"/><path d="M7 4.5l1-1a2.5 2.5 0 013.5 3.5l-1 1"/><path d="M9 11.5l-1 1A2.5 2.5 0 014.5 9l1-1"/>',
    preview: '<rect x="2" y="3" width="12" height="9" rx="1.5"/><path d="M6.5 6l3 1.5-3 1.5z" fill="currentColor"/>',
    search: '<circle cx="7" cy="7" r="4"/><path d="M10 10l3.5 3.5"/>',
    field: '<path d="M5.5 3C4 3 4 4 4 5.5S3.5 8 2.5 8c1 0 1.5.5 1.5 2.5S4 13 5.5 13"/><path d="M10.5 3C12 3 12 4 12 5.5s.5 2.5 1.5 2.5c-1 0-1.5.5-1.5 2.5S12 13 10.5 13"/>',
    func: '<path d="M10.5 3.5C9 3 8 3.8 7.7 5.5L6.6 11c-.3 1.6-1.3 2.2-2.6 1.8"/><path d="M5.5 7h4.5"/>',
    event: '<path d="M9 2L4 9h3.5L7 14l5-7H8.5z"/>',
    condition: '<circle cx="4" cy="4" r="1.5"/><circle cx="4" cy="12" r="1.5"/><circle cx="12" cy="8" r="1.5"/><path d="M4 5.5v5"/><path d="M4 8h3.5a3 3 0 003-2"/>',
    play: '<path d="M5 3.5l7 4.5-7 4.5z" fill="currentColor"/>',
    reverse: '<path d="M11 3.5L4 8l7 4.5z" fill="currentColor"/>',
    stop: '<rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor"/>',
    down: '<path d="M8 3v10"/><path d="M4.5 9.5L8 13l3.5-3.5"/>',
    text: '<path d="M3.5 4h9"/><path d="M8 4v9"/>',
    image: '<rect x="2.5" y="3" width="11" height="10" rx="1.5"/><circle cx="6" cy="6.5" r="1.2"/><path d="M3 12l3.5-3.5 2.5 2.5 1.5-1.5L13 12"/>',
    button: '<rect x="2" y="4.5" width="12" height="7" rx="2"/><path d="M6 8h4"/>',
    component: '<path d="M8 2l5.5 3v6L8 14l-5.5-3V5z"/><path d="M2.5 5L8 8l5.5-3"/><path d="M8 8v6"/>',
    panel: '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.5 7h11"/><path d="M7 7v6.5"/>',
    switcher: '<rect x="4.5" y="2.5" width="9" height="7" rx="1"/><path d="M2.5 5.5v7a1 1 0 001 1h7"/>',
    progress: '<rect x="2" y="6" width="12" height="4" rx="2"/><path d="M4 8h5"/>',
    widget: '<rect x="3" y="3" width="10" height="10" rx="1.5"/>',
    chevron: '<path d="M6 4l4 4-4 4"/>'
  };
  function icon(name, cls) {
    var span = document.createElement("span");
    span.className = "ic" + (cls ? " " + cls : "");
    span.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (PATHS[name] || PATHS.widget) + "</svg>";
    return span;
  }
  var KIND_ICONS = { TextBlock: "text", RichTextBlock: "text", Image: "image", Button: "button", UserWidget: "component",
    WidgetSwitcher: "switcher", ProgressBar: "progress", CanvasPanel: "panel", Overlay: "panel", StackBox: "panel",
    UniformGridPanel: "panel", GridPanel: "panel", ScaleBox: "panel", SizeBox: "panel", Border: "panel", ScrollBox: "panel" };
  var PLAYS = { QueuePlayAnimation: "play", PlayAnimation: "play", QueuePlayAnimationForward: "play", PlayAnimationForward: "play",
    QueuePlayAnimationReverse: "reverse", PlayAnimationReverse: "reverse", QueueStopAnimation: "stop", StopAnimation: "stop" };
  var VERBS = { play: "Play", reverse: "Play in reverse", stop: "Stop" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  // long names wrap after a dot or before a capital, never in the middle of a word
  function soft(text) { return String(text || "").replace(/\./g, ".​").replace(/([a-z])([A-Z])/g, "$1​$2"); }
  function chip(text, cls) { return el("span", "chip" + (cls ? " " + cls : ""), text); }

  function create(host, opts) {
    var engine = opts.engine, spec = opts.spec, data = spec.bindings || { values: [], events: [], conditions: [] };
    var filter = "all", query = "";
    var animNames = {};
    (spec.animations || []).forEach(function (a) { animNames[a.name] = true; });
    var total = data.values.length + data.events.length + data.conditions.length;

    function kindIcon(widget) {
      var kind = engine && engine.kindOf ? engine.kindOf(widget) : "";
      return icon(KIND_ICONS[kind] || "widget", "kind");
    }
    // one end of a binding: icon, name, and a quieter detail (the property, the event...)
    function end(iconEl, name, detail, hint) {
      var row = el("div", "end");
      row.appendChild(iconEl);
      var label = el("div", "label");
      label.appendChild(el("span", "name", soft(name)));
      if (detail) label.appendChild(el("span", "detail", soft(detail)));
      row.appendChild(label);
      if (hint) row.appendChild(chip(hint, "quiet"));
      return row;
    }
    function link(fnName) {
      var row = el("div", "link");
      row.appendChild(icon("down", "arrow"));
      if (fnName) { var f = chip("", "fn"); f.appendChild(icon("func")); f.appendChild(document.createTextNode(soft(fnName))); row.appendChild(f); }
      return row;
    }
    function action(b) { // what an event or condition runs: an animation or a Verse function
      var verb = PLAYS[b["function"]];
      if (verb) {
        var anim = (b.args || []).filter(function (a) { return a.name === "InAnimation"; })[0];
        var name = anim && anim.value ? anim.value : "(no animation)";
        var extras = [];
        (b.args || []).forEach(function (a) {
          if (a.name === "PlaybackSpeed" && parseFloat(a.value) !== 1) extras.push("speed ×" + parseFloat(a.value));
          if (a.name === "NumLoopsToPlay" && a.value && a.value !== "1") extras.push(a.value === "0" ? "loops forever" : a.value + " loops");
          if (a.name === "StartAtTime" && parseFloat(a.value)) extras.push("from " + parseFloat(a.value) + " s");
        });
        var row = end(icon(verb, "anim"), name, [VERBS[verb]].concat(extras).join(" · "));
        if (verb !== "stop" && animNames[name]) {
          var tryBtn = el("button", "try");
          tryBtn.type = "button"; tryBtn.title = "Play " + name + " in the preview";
          tryBtn.appendChild(icon(verb === "reverse" ? "reverse" : "play"));
          tryBtn.onclick = function (e) { e.stopPropagation(); if (opts.onPlay) opts.onPlay(name); };
          row.appendChild(tryBtn);
        }
        return row;
      }
      var args = (b.args || []).filter(function (a) { return a.value; }).map(function (a) { return a.name + ": " + a.value; });
      return end(icon("func", "verse"), b["function"] || "?", args.length ? args.join(", ") : "Verse function", "Verse");
    }

    function card(kind, b) {
      var c = el("div", "bcard " + kind + (b.enabled ? "" : " off"));
      var widget = b.widget || "";
      if (kind === "value") {
        var src = b["function"] ? (b.args || []).filter(function (a) { return a.bound; }).map(function (a) { return a.value; }).join(", ") : b.source;
        c.appendChild(end(icon("field", "field"), src || "?", "Verse field", b.value != null ? "= " + b.value : ""));
        c.appendChild(link(b["function"] || ""));
        var prop = widget && b.target.indexOf(widget + ".") === 0 ? b.target.slice(widget.length + 1) : b.target;
        c.appendChild(end(kindIcon(widget), widget || b.target, widget ? prop : ""));
      } else if (kind === "event") {
        var parts = String(b.event || "").split("."), ev = parts.pop();
        c.appendChild(end(icon("event", "event"), ev, parts.join(" › ")));
        c.appendChild(link(""));
        c.appendChild(action(b));
      } else {
        var cond = b.operation + " " + b.value + (b.value_max != null ? " and " + b.value_max : "");
        c.appendChild(end(icon("condition", "cond"), b.source, "when " + cond));
        c.appendChild(link(""));
        c.appendChild(action(b));
      }
      if (kind === "value" && b.mode && b.mode !== "one way") c.firstChild.appendChild(chip(b.mode, "quiet"));
      if (!b.enabled) c.firstChild.appendChild(chip("off", "warn"));
      c.dataset.search = JSON.stringify(b).toLowerCase();
      if (widget) {
        c.title = "Hover to outline " + widget + " in the preview";
        c.onmouseenter = function () { engine.highlight(widget); };
        c.onmouseleave = function () { engine.highlight(null); };
      }
      return c;
    }

    var GROUPS = [
      { key: "values", kind: "value", title: "Values", icon: "field", hint: "A field feeds a widget property" },
      { key: "events", kind: "event", title: "Events", icon: "event", hint: "A widget event runs an action" },
      { key: "conditions", kind: "condition", title: "Conditions", icon: "condition", hint: "A field reaching a value runs an action" }
    ];

    host.innerHTML = "";
    var head = el("div", "bhead");
    var box = el("label", "bsearch");
    box.appendChild(icon("search"));
    var input = el("input"); input.type = "search"; input.placeholder = "Search bindings";
    box.appendChild(input); head.appendChild(box);
    var chips = el("div", "bfilters");
    var filterButtons = {};
    [{ key: "all", title: "All", n: total }].concat(GROUPS.map(function (g) { return { key: g.key, title: g.title, n: data[g.key].length }; }))
      .forEach(function (f) {
        if (f.key !== "all" && !f.n) return;
        var b = el("button", "bfilter"); b.type = "button";
        b.appendChild(document.createTextNode(f.title + " ")); b.appendChild(el("span", "n", String(f.n)));
        b.onclick = function () { filter = f.key; refresh(); };
        filterButtons[f.key] = b; chips.appendChild(b);
      });
    head.appendChild(chips);
    host.appendChild(head);
    var list = el("div", "blist");
    host.appendChild(list);

    var sections = GROUPS.filter(function (g) { return data[g.key].length; }).map(function (g) {
      var sec = el("section", "bgroup");
      var h = el("h3"); h.appendChild(icon(g.icon, g.kind)); h.appendChild(document.createTextNode(g.title + " "));
      h.appendChild(el("span", "n", String(data[g.key].length))); h.title = g.hint;
      sec.appendChild(h);
      var cards = data[g.key].map(function (b) { var c = card(g.kind, b); sec.appendChild(c); return c; });
      list.appendChild(sec);
      return { group: g, el: sec, cards: cards };
    });
    var none = el("div", "bnone", "No binding matches this search.");
    list.appendChild(none);
    if (!total) { none.textContent = "This widget has no View Bindings."; }

    function refresh() {
      Object.keys(filterButtons).forEach(function (k) { filterButtons[k].classList.toggle("on", k === filter); });
      var shown = 0;
      sections.forEach(function (s) {
        var groupOn = filter === "all" || filter === s.group.key, count = 0;
        s.cards.forEach(function (c) {
          var on = groupOn && (!query || c.dataset.search.indexOf(query) >= 0);
          c.hidden = !on; if (on) count++;
        });
        s.el.hidden = !count; shown += count;
      });
      none.hidden = !!shown || !total;
      if (!total) none.hidden = false;
    }
    input.oninput = function () { query = input.value.trim().toLowerCase(); refresh(); };
    refresh();
    return { total: total };
  }

  window.UmgBindings = { create: create, icon: icon };
})();
