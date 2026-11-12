/* Loading contracts: an exported example never talks to the Ducky host. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../ui/umg.html'), 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const example = { ok: true, spec: { name: 'Example', root: {}, animations: [], assets: {}, warnings: [] } };

function load({ embedded = false, offline = false, protocol = 'file:' } = {}) {
  const elements = new Map(), sent = [], listeners = new Set(), mounted = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '1920x1080', hidden: false,
      textContent: '', classList: { contains: () => false } });
    return elements.get(id);
  };
  const engine = { spec: example.spec, setViewport() {}, setHints() {}, setMuted() {}, on() {}, switchers: () => [],
    setEditMode() {}, setDesignTime() {}, tree: () => [], toggleStats() {}, selection: () => null, images: () => [], materialReport: () => ({ shaders: 0, failed: [] }) };
  const window = { setTimeout: () => 1, clearTimeout() {},
    addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn),
    UmgEngine: { create: (_, spec) => { mounted.push(spec.name); return engine; } } };
  const parent = embedded ? {} : window;
  parent.postMessage = message => {
    sent.push(message);
    queueMicrotask(() => {
      // A request-shaped message is not a reply, even with the correct id.
      for (const fn of [...listeners]) fn({ source: parent, data: message });
      const result = message.method === 'plugin.info' ? { filePath: 'Content/UI/Menu.uasset' } : example;
      for (const fn of [...listeners]) fn({ source: parent, data: { ...message, ok: true, result } });
    });
  };
  if (offline) window.__offlineDemo = example;
  vm.runInNewContext(source, { window, parent, location: { protocol, search: '' },
    crypto: { randomUUID: () => String(sent.length) }, URLSearchParams, document: { getElementById: element } });
  return { elements, sent, mounted };
}

test('opening the plugin page directly explains how to open a preview without contacting a host', async () => {
  const r = load();
  await new Promise(setImmediate);
  assert.equal(r.sent.length, 0);
  assert.match(r.elements.get('err').textContent, /autonome/i);
  assert.doesNotMatch(r.elements.get('err').textContent, /bridge error/i);
});

test('the self-contained example mounts without any host call', async () => {
  const r = load({ offline: true });
  await new Promise(setImmediate);
  assert.equal(r.sent.length, 0);
  assert.deepEqual(r.mounted, ['Example']);
  assert.equal(r.elements.get('err').textContent, '');
});

test('embedded Ducky still loads and ignores request-shaped messages', async () => {
  const r = load({ embedded: true, protocol: 'http:' });
  await new Promise(setImmediate);
  assert.deepEqual(r.sent.map(m => m.method), ['plugin.info', 'plugin.call']);
  assert.deepEqual(r.mounted, ['Example']);
  assert.equal(r.elements.get('err').textContent, '');
});
