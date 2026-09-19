const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(new URL('../src/spviz/web/app.js', `file://${__filename}`), 'utf8');
// Exercise the actual carousel event handlers without requiring a browser in CI.
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.events = {}; this.scrollTop = 0; this.clientHeight = 290; this.offsetHeight = 240;
    this.isConnected = true;
    this.style = { setProperty(name, value) { this[name] = value; } };
    this.classes = new Set();
    this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name), contains: name => this.classes.has(name) };
  }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      child.offsetTop = 25 + this.children.length * 72;
      this.children.push(child);
    }
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  scrollTo({ top }) { this.scrollTop = top; this.events.scroll?.(); }
  focus() { this.focused = true; }
}
let nextTimer = 0;
const timers = new Map(), selections = [];
const context = vm.createContext({
  document: { createElement: tag => new Element(tag) },
  matchMedia: () => ({ matches: true }),
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
assert.equal(groups[0].viewport.scrollTop, 72);
assert.equal(groups[0].cards[1].attributes['aria-current'], 'true');
assert.equal(groups[1].active, 0);
assert.equal(groups[1].previous.disabled, true);
assert.equal(groups[1].next.disabled, true);
groups[0].next.onclick();
flush();
assert.equal(groups[0].active, 2);
assert.equal(selections.at(-1), 'iq--view-3');
assert.equal(groups[0].next.disabled, true);
assert.equal(groups[1].active, 0, 'other tap primaries remain unchanged');
// Native wheel/touch scrolling settles on the nearest card and selects it.
groups[0].viewport.scrollTop = 0;
groups[0].viewport.events.scroll();
flush();
assert.equal(groups[0].active, 0);
assert.equal(selections.at(-1), 'iq');
assert.equal(groups[0].previous.disabled, true);
let prevented = false;
groups[0].viewport.events.keydown({ key: 'End', preventDefault: () => { prevented = true; } });
flush();
assert.equal(prevented, true);
assert.equal(groups[0].active, 2);
assert.equal(groups[0].cards[2].focused, true);
groups[0].cards[1].onclick();
flush();
assert.equal(groups[0].active, 1);
assert.equal(groups[0].cards.filter(card => card.classes.has('primary')).length, 1);
assert.equal(groups[0].label.textContent, 'Phase · 2/3');
console.log('Carousel grouping, defaults, native scrolling, buttons, clicks, keyboard, and independent taps passed.');

// Depth responds during a gesture, before selection settles.
const scale = card => Number(card.style['--view-scale']);
assert.equal(scale(groups[0].cards[1]), 1);
assert.ok(scale(groups[0].cards[0]) < 0.8);
const selectedBeforeScroll = selections.length;
groups[0].viewport.scrollTop = 36;
groups[0].viewport.events.scroll();
assert.equal(selections.length, selectedBeforeScroll);
assert.equal(scale(groups[0].cards[0]), scale(groups[0].cards[1]));
assert.ok(scale(groups[0].cards[0]) > 0.8 && scale(groups[0].cards[0]) < 1);
const halfwayScale = scale(groups[0].cards[0]);
groups[0].viewport.scrollTop = 35;
groups[0].viewport.events.scroll();
assert.ok(scale(groups[0].cards[0]) > halfwayScale);
assert.ok(scale(groups[0].cards[0]) - halfwayScale < 0.01);
assert.ok(Number(groups[0].cards[0].style.zIndex) >= Number(groups[0].cards[1].style.zIndex));
groups[0].viewport.scrollTop = 0;
groups[0].viewport.events.scroll();
assert.equal(scale(groups[0].cards[0]), 1);
assert.equal(scale(groups[0].cards[2]), 0.68);
assert.equal(scale(groups[1].cards[0]), 1);
console.log('Continuous depth scaling, smooth midpoint, foreground order, and compact layout passed.');
