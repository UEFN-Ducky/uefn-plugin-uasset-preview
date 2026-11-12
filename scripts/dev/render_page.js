// Render one switcher page of the widget alone, at the designer's resolution, for comparison with UEFN.
const PAGE = 2;
const res = document.getElementById("res");
res.value = "1280x720"; res.onchange();
const e = window.__umg;
e.setDesignTime(true);
const sw = e.switchers().find((s) => s.label === "Switcher_Tabs") || e.switchers()[0];
if (sw) e.setSwitcher(sw.id, PAGE);
// give the stage the exact pixel size of the viewport so nothing is rescaled
const stage = document.getElementById("stage");
stage.style.cssText = "position:fixed;left:0;top:0;width:1280px;height:720px;z-index:99";
await new Promise((r) => setTimeout(r, 900));
return { png: stage.toDataURL("image/png"), size: [stage.width, stage.height], report: e.materialReport() };
