const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(new URL('../src/spviz/web/app.js', `file://${__filename}`), 'utf8');
const encodings = [], liveUrls = new Set(), snapshots = [];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.disabled = false; this.textContent = ''; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getContext() { return { fillRect() {}, drawImage: source => { this.pixels = source.pixels; } }; }
  toBlob(callback) { snapshots.push(this); encodings.push(() => callback({ pixels: this.pixels })); }
}
const elements = new Map(['comparison', 'comparison-count', 'comparison-status', 'comparison-views', 'volume', 'layer-output', 'representation', 'hold-comparison', 'clear-comparison'].map(id => [id, new Element('div')]));
elements.get('volume').width = 800;
elements.get('volume').height = 500;
elements.get('volume').pixels = 'original rendered plot';
elements.get('layer-output').textContent = 'Layer 3/10';
elements.get('representation').textContent = 'phase';
const state = { product: { name: 'IQ · Phase', axes: ['time', 'range'], units: 'rad' }, heldViews: new Map(), nextHeldView: 0, perm: [0, 1], fixedIndices: new Map(), logScale: false };
let urlCounter = 0;
const context = vm.createContext({
  state, $: id => elements.get(id),
  document: { createElement: tag => new Element(tag), documentElement: {} },
  getComputedStyle: () => ({ getPropertyValue: () => '#000000' }),
  displayBounds: () => [-Math.PI, Math.PI],
  URL: { createObjectURL: () => { const url = `blob:${++urlCounter}`; liveUrls.add(url); return url; }, revokeObjectURL: url => liveUrls.delete(url) },
});
vm.runInContext(source.slice(source.indexOf('function syncComparison()'), source.indexOf('function saveFullChain()')), context);
elements.get('hold-comparison').disabled = true;
context.holdForComparison();
assert.equal(state.heldViews.size, 0, 'loading or stale plots cannot be held');
elements.get('hold-comparison').disabled = false;
context.holdForComparison();
state.product = { name: 'Other view', axes: ['x', 'y'] };
elements.get('volume').pixels = 'new rendered plot';
elements.get('layer-output').textContent = 'Layer 8/10';
encodings.shift()();
assert.equal(snapshots[0].pixels, 'original rendered plot', 'copy pixels before switching inspector');
const first = state.heldViews.get(1).card;
assert.match(first.children[0].children[0].textContent, /IQ · Phase/);
assert.match(first.children[2].textContent, /Layer 3\/10/);
assert.equal(snapshots[0].width, 0, 'release temporary canvas backing storage');
for (let i = 0; i < 40; i++) context.holdForComparison();
assert.equal(state.heldViews.size, 41, 'no fixed comparison limit, including duplicate views');
while (encodings.length) encodings.shift()();
assert.equal(liveUrls.size, 41);
context.removeHeldView(1);
assert.equal(liveUrls.size, 40);
assert.equal(elements.get('comparison-views').children.length, 40);
context.holdForComparison();
context.clearComparison();
assert.equal(state.heldViews.size, 0);
assert.equal(liveUrls.size, 0);
assert.equal(elements.get('comparison').hidden, true);
encodings.shift()();
assert.equal(liveUrls.size, 0, 'late encoding cannot restore cleared content or leak a URL');
console.log('Frozen comparison, unlimited holds, stale-frame guard, removal, and resource cleanup passed.');
