const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/spviz/web/app.js'), 'utf8');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
let rendered;
const context = vm.createContext({
  state: { colorMap: 'auto', bitmaps: new Map() },
  document: { createElement: () => ({ getContext: () => ({
    createImageData: (w,h) => ({data: new Uint8ClampedArray(w*h*4)}),
    putImageData: image => { rendered = Array.from(image.data); },
  }) }) },
  MAX_BITMAP_CACHE_BYTES: 10000, lruSet() {},
});
vm.runInContext(source.slice(source.indexOf('const $ ='), source.indexOf('function themeInk(')), context);
vm.runInContext(source.slice(source.indexOf('function isBinaryProduct('), source.indexOf('function stopPlayback(')), context);
vm.runInContext(source.slice(source.indexOf('function bitmapFor('), source.indexOf('async function drawOverview(')), context);
const results = fixtures.map(f => {
  context.state.run = {products:[{id:'test',dtype:f.binary?'bool':'float32'}]};
  context.bitmapFor({product:'test',permutation:[0,1],layer:0,rows:1,columns:f.values.length,representation:f.phase?'phase':'real',values:Float32Array.from(f.values, v=>v===null?NaN:v)}, f.low, f.high, f.log, 'test');
  return rendered;
});
process.stdout.write(JSON.stringify(results));
