const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(new URL('../src/spviz/web/app.js', `file://${__filename}`), 'utf8');
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.disabled = false; this.checked = false;
    this.textContent = ''; this.messages = [];
    this.contentWindow = { postMessage: (data, origin) => this.messages.push({ data: structuredClone(data), origin }) };
  }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const elements = new Map();
const $ = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
const product = { id: 'iq', name: 'IQ · Phase', shape: [8, 20, 512], axes: ['receiver', 'integration', 'sample'], representation: 'phase' };
const state = { product, heldViews: new Map(), nextHeldView: 0, viewAxes: [0, 1, 2], perm: [0, 1, 2], fixedIndices: new Map(), layer: 2, yaw: 0.3, pitch: 0.1, isolate: false, viewMode: 'surface', aspect: 'data', scaleMin: 0, scaleMax: 1, logScale: false, opacity: 0.25, currentOpacity: 1, pixelDensity: 96, theme: 'dark', colorMap: 'auto' };
let messageHandler, renders = 0;
const parent = { messages: [], postMessage(data) { this.messages.push(data); } };
const context = vm.createContext({ state, $, URL, structuredClone, embeddedProduct: null,
  location: { href: 'https://example.org/radar/', origin: 'https://example.org' },
  document: { createElement: tag => new Element(tag), documentElement: { dataset: {} } },
  window: { parent, addEventListener: (name, handler) => { if (name === 'message') messageHandler = handler; } },
  comparisonInitialized: false, lastComparisonGeometry: '', colorMaps: {},
  stopPlayback() { state.timer = null; state.playing = false; },
  startPlayback() { state.timer = 1; state.playing = true; }, isBinaryProduct: () => false, syncScaleLabels() {}, updateScale() {}, syncScaleLegend() {},
  syncFixedDimensionControls() {}, syncLayer() {}, scheduleDraw: () => renders++,
});
vm.runInContext(source.slice(source.indexOf('function comparisonGeometry()'), source.indexOf('function saveFullChain()')), context);
context.holdForComparison();
assert.equal(state.heldViews.size, 0);
context.rememberRenderedView();
context.holdForComparison();
context.holdForComparison();
const [a, b] = [...state.heldViews.values()];
assert.match(a.iframe.src, /viewer=iq/);
assert.match(a.iframe.title, /Interactive comparison/);
assert.equal(a.settings.layer, 2);
function receive(panel, type, geometry) {
  messageHandler({ origin: 'https://example.org', source: panel.iframe.contentWindow, data: { channel: 'spviz-comparison', type, geometry } });
}
receive(a, 'ready'); receive(b, 'ready');
assert.equal(a.iframe.messages[0].data.type, 'initialize');
receive(a, 'geometry', { ...a.settings, layer: 4 });
assert.equal(b.settings.layer, 2, 'viewers start independent');
$('link-comparison').checked = true;
$('link-comparison').onchange();
assert.equal(b.settings.layer, 4);
receive(b, 'geometry', { ...b.settings, layer: 6, yaw: 0.8 });
assert.equal(a.settings.layer, 6, 'linking works in both directions');
assert.equal(a.settings.yaw, 0.8);
assert.equal(a.iframe.messages.at(-1).data.settings.scaleMin, 0, 'display controls are linked too');
state.product = { ...product, id: 'different', shape: [4, 20, 512] };
context.rememberRenderedView(); context.holdForComparison();
const c = [...state.heldViews.values()][2];
receive(c, 'ready');
const count = c.iframe.messages.length;
receive(a, 'geometry', { ...a.settings, layer: 1 });
assert.equal(c.iframe.messages.length, count, 'incompatible dimensions stay independent');
const oldLayer = b.settings.layer;
messageHandler({ origin: 'https://other.org', source: a.iframe.contentWindow, data: { channel: 'spviz-comparison', type: 'geometry', geometry: { ...a.settings, layer: 7 } } });
assert.equal(b.settings.layer, oldLayer, 'reject other origins');
messageHandler({ origin: 'https://example.org', source: {}, data: { channel: 'spviz-comparison', type: 'geometry', geometry: { ...a.settings, layer: 7 } } });
assert.equal(b.settings.layer, oldLayer, 'reject unrelated windows');
$('link-comparison').checked = false;
receive(a, 'geometry', { ...a.settings, layer: 3 });
assert.equal(b.settings.layer, oldLayer);
for (let i = 0; i < 40; i++) context.holdForComparison();
assert.equal(state.heldViews.size, 43, 'no arbitrary viewer count limit');
context.removeHeldView(1);
assert.equal(a.iframe.src, 'about:blank');
context.clearComparison();
assert.equal(state.heldViews.size, 0);
assert.equal($('comparison-views').children.length, 0);
// Exercise the actual embedded-viewer settings application and no-echo guard.
state.product = product;
const geometry = { ...context.comparisonGeometry(), layer: 5, yaw: -0.4, pitch: 0.2, perm: [1, 0, 2] };
const scaleMin = state.scaleMin;
context.applyComparisonSettings(geometry);
assert.equal(state.layer, 5);
assert.equal(state.yaw, -0.4);
assert.deepEqual(Array.from(state.perm), [1, 0, 2]);
assert.equal(state.scaleMin, scaleMin);
assert.equal($('permutation').value, '1,0,2');
assert.ok(renders > 0);
context.embeddedProduct = 'iq'; context.comparisonInitialized = true;
context.publishComparisonGeometry();
assert.equal(parent.messages.length, 0, 'linked updates do not echo');
state.layer = 6;
context.publishComparisonGeometry();
assert.equal(parent.messages.length, 1);
assert.equal(parent.messages[0].geometry.layer, 6);
context.applyComparisonSettings({ ...geometry, shape: [4, 20, 512], layer: 0 });
assert.equal(state.layer, 6, 'incompatible update is ignored');
console.log('Independent interactive viewers, bidirectional compatible linking, no echo, and cleanup passed.');

context.applyComparisonSettings({ ...context.comparisonSettings(), yaw: 0.7, pitch: -0.3,
  scaleMin: 0.2, scaleMax: 0.8, opacity: 0.4, currentOpacity: 0.6,
  opacityLinked: true, playing: true, pixelDensity: 128, theme: 'light', colorMap: 'auto', logScale: true });
assert.equal(state.pitch, -0.3, 'nutation/pitch follows the linked viewer');
assert.equal(state.yaw, 0.7);
assert.equal(state.opacity, 0.4);
assert.equal(state.currentOpacity, 0.6);
assert.equal(state.opacityLinked, true);
assert.equal(state.scaleMin, 0.2);
assert.equal(state.scaleMax, 0.8);
assert.equal(state.pixelDensity, 128);
assert.equal(state.theme, 'light');
assert.equal(state.logScale, false, 'phase cannot use logarithmic values');
assert.equal(state.playing, true);
assert.equal($('play').textContent, 'Pause layers');
context.publishComparisonGeometry();
assert.equal(parent.messages.length, 1, 'appearance/playback updates do not echo');
state.playing = false;
context.publishComparisonGeometry();
assert.equal(parent.messages.at(-1).geometry.playing, false, 'pause is broadcast even without a layer change');
console.log('Rotation, nutation, opacity, range, quality, appearance, and playback linking passed.');

context.applyComparisonSettings({ ...context.comparisonSettings(), playing: true });
assert.equal(state.timer, null);
state.opacity = 0.7;
context.publishComparisonGeometry();
assert.equal(state.timer, 1, 'editing a playing follower transfers the playback clock');
