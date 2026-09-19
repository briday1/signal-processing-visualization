const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(new URL('../src/spviz/web/app.js', `file://${__filename}`), 'utf8');
// Exercise the actual carousel event handlers without requiring a browser in CI.
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.events = {}; this.scrollTop = 0; this.clientHeight = 408; this.offsetHeight = 240;
    this.isConnected = true;
    this.style = { setProperty(name, value) { this[name] = value; } };
    this.classes = new Set();
    this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name), contains: name => this.classes.has(name) };
  }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      child.offsetTop = 84 + this.children.length * 252;
      this.children.push(child);
    }
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setPointerCapture(id) { this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture() { this.capture = undefined; }
  focus() { this.focused = true; }
}
let nextTimer = 0;
const timers = new Map(), selections = [];
const context = vm.createContext({
  document: { createElement: tag => new Element(tag) },
  matchMedia: () => ({ matches: true }),
  cancelAnimationFrame: () => {},
  setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
  clearTimeout: id => timers.delete(id),
  selectProduct: product => { selections.push(product.id); return Promise.resolve(); },
  handleViewerError: error => { throw error; },
  drawOverview: () => Promise.resolve(),
});
vm.runInContext(source.slice(source.indexOf('function groupCaptures('), source.indexOf('async function build()')), context);
const products = [
  { id: 'iq', capture_id: 'iq', capture_name: 'IQ', view_name: 'Amplitude', shape: [4], dtype: 'complex64' },
  { id: 'iq--view-2', capture_id: 'iq', capture_name: 'IQ', view_name: 'Phase', is_primary: true, shape: [4], dtype: 'complex64' },
  { id: 'iq--view-3', capture_id: 'iq', capture_name: 'IQ', view_name: 'Power', shape: [4], dtype: 'complex64' },
  { id: 'fft', name: 'FFT', upstream: ['iq'], shape: [4], dtype: 'float32' },
];
const groups = context.groupCaptures(products);
assert.equal(groups.length, 2, 'views must not add horizontal pipeline steps');
assert.equal(groups[0].active, 1, 'API primary may be in the middle');
context.buildCaptureStack(groups[0]);
context.buildCaptureStack(groups[1]);
context.centerView(groups[1], 0, "instant");
context.centerView(groups[0], groups[0].active, 'instant');
const flush = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(callback => callback()); };
flush();
assert.equal(selections.length, 0, 'initial centering must not steal inspector focus');

assert.equal(groups[0].active, 1);
assert.equal(groups[0].viewList.value, '1');
assert.deepEqual(Array.from(groups[0].viewList.children, option => option.textContent), ['Amplitude', 'Phase', 'Power']);
assert.equal(groups[0].viewList.parentElement, groups[0].previous.parentElement, 'list belongs beside the arrows');
assert.equal(groups[1].previous.disabled, true);
assert.equal(groups[1].next.disabled, true);
for (let i = 0; i < 7; i++) groups[0].next.onclick();
assert.equal(groups[0].active, 2);
assert.equal(groups[0].position, 8, 'rotation continues past the last view');
assert.equal(groups[0].next.disabled, false);
for (let i = 0; i < 9; i++) groups[0].previous.onclick();
assert.equal(groups[0].active, 2);
assert.equal(groups[0].position, -1, 'reverse rotation passes the first view');
assert.equal(groups[0].previous.disabled, false);
assert.equal(groups[1].active, 0, 'other taps remain independent');
groups[0].viewList.value = '0';
groups[0].viewList.onchange();
assert.equal(groups[0].active, 0);
assert.equal(selections.at(-1), 'iq');
let prevented = false;
groups[0].viewport.events.keydown({key:'ArrowUp', preventDefault: () => { prevented = true; }});
assert.equal(prevented, true);
assert.equal(groups[0].active, 2);
assert.equal(groups[0].cards[2].focused, true);
const scale = card => Number(card.style['--view-scale']);
assert.equal(scale(groups[0].cards[2]), 1);
assert.equal(scale(groups[0].cards[0]), 0.25);
assert.equal(scale(groups[0].cards[1]), 0.25);
// A wheel gesture travels continuously, then selects only at its snap point.
const before = selections.length;
groups[0].viewport.events.wheel({deltaX:0, deltaY:120, deltaMode:0, preventDefault() {}});
assert.equal(selections.length, before);
assert.ok(scale(groups[0].cards[0]) > 0.25 && scale(groups[0].cards[0]) < 1);
flush();
assert.equal(groups[0].active, 0);
assert.equal(groups[0].viewList.value, '0');
// Dragging and pointer cancellation leave the orbit snapped and usable.
groups[0].viewport.events.pointerdown({button:0,pointerId:3,clientY:200});
groups[0].viewport.events.pointermove({pointerId:3,clientY:20});
groups[0].viewport.events.pointerup({pointerId:3});
assert.equal(groups[0].active, 1);
assert.equal(groups[0].viewport.capture, undefined);
// Two views also loop in either direction, with the off view visibly recessed.
const pair = context.groupCaptures(products.slice(0,2))[0];
context.buildCaptureStack(pair);
context.centerView(pair, pair.active, 'instant');
assert.equal(scale(pair.cards[0]), 0.25);
assert.ok(Math.abs(parseFloat(pair.cards[0].style['--view-shift'])) >= 130);
pair.next.onclick();
assert.equal(pair.active, 0);
pair.next.onclick();
assert.equal(pair.active, 1);
pair.previous.onclick();
assert.equal(pair.active, 0);
assert.equal(pair.previous.disabled, false);
console.log('Circular wheel, drag, keyboard, list selection, depth miniatures, two-view and single-view behavior passed.');

groups[0].viewport.events.wheel({deltaX:0,deltaY:35,deltaMode:0,preventDefault(){}});
groups[0].next.onclick();
assert.ok(Number.isInteger(groups[0].position), 'clicking during a wheel gesture must still center a whole view');
