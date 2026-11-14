// Dev tool: run a script inside a page in headless Edge (DevTools protocol) and save a picture.
// usage: node scripts/dev/browser.cjs <page.html> <out.png> <script.js>
// The script is the body of an async function. If it returns {png: "data:image/png;base64,..."}
// that image is saved (e.g. the preview canvas alone); otherwise a screenshot of the page is.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const [page, out, scriptFile] = process.argv.slice(2);
const edge = process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const port = 9333 + Math.floor(Math.random() * 500);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "umg-dev-"));
const child = spawn(edge, ["--headless=new", "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist",
  "--window-size=1600,900", "--remote-debugging-port=" + port, "--user-data-dir=" + profile, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  let target = null;
  for (let i = 0; i < 50 && !target; i++) {
    await sleep(200);
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch (e) {}
  }
  if (!target) throw new Error("browser did not start");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const waiting = new Map(); const errors = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
    if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push(d.params.args.map((a) => a.value || a.description).join(" "));
  };
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send("Runtime.enable"); await send("Page.enable");
  await send("Page.navigate", { url: "file:///" + path.resolve(page).replace(/\\/g, "/") });
  await sleep(2500);
  const res = await send("Runtime.evaluate", { expression: "(async () => {" + fs.readFileSync(scriptFile, "utf8") + "\n})()", awaitPromise: true, returnByValue: true });
  const value = res.result.result?.value;
  if (value && typeof value.png === "string") {
    fs.writeFileSync(out, Buffer.from(value.png.split(",")[1], "base64"));
    delete value.png;
  } else {
    await sleep(400);
    const shot = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  }
  console.log("RESULT", JSON.stringify(value ?? res.result.exceptionDetails?.exception?.description ?? res.result));
  if (errors.length) console.log("PAGE ERRORS", errors.slice(0, 5));
  ws.close(); child.kill();
  setTimeout(() => { try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {} process.exit(0); }, 500);
})().catch((e) => { console.log("FAILED", e.message); child.kill(); process.exit(1); });
