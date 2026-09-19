const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../src/spviz/web/app.js'), 'utf8');
const decodes = [], paints = [];
let serial = 0;
class Image {
  constructor() { this.id = ++serial; }
  decode() { return new Promise((resolve, reject) => decodes.push({resolve, reject})); }
}
const context = vm.createContext({
  Image,
  state: {colorMap:'auto',overviewAspect:'data',theme:'dark',run:{static_export:{previews:true}}},
  staticBase:'./data', staticAssetUrl: url => url,
  document: {createElement: () => {
    const staging = {width:0,height:0};
    staging.getContext = () => ({drawImage(image) { staging.frame = image.id; }});
    return staging;
  }},
});
vm.runInContext(source.slice(source.indexOf('function overviewAppearance('), source.indexOf('function isBinaryProduct(')), context);
const status = {}, canvas = {
  width:640,height:480,parentElement:{querySelector:()=>status},
  getContext:()=>({drawImage(staging) { paints.push(staging.frame); }}),
};
(async () => {
  const old = context.drawOverview({id:'phase'},canvas);
  assert.deepEqual(paints, [], 'pending redraw must not clear or paint the visible canvas');
  const latest = context.drawOverview({id:'phase'},canvas);
  decodes[1].resolve(); await latest;
  assert.deepEqual(paints, [2]);
  decodes[0].resolve(); await old;
  assert.deepEqual(paints, [2], 'late old decode must never overwrite the completed frame');
  const staleAppearance = context.drawOverview({id:'phase'},canvas);
  context.state.colorMap = 'viridis';
  decodes[2].resolve(); await staleAppearance;
  assert.deepEqual(paints, [2], 'an old palette cannot flash while its replacement is queued');
  context.state.colorMap = 'auto';
  const failure = context.drawOverview({id:'phase'},canvas);
  decodes[3].reject(new Error('test failure'));
  await assert.rejects(failure);
  assert.deepEqual(paints, [2], 'failed replacement must preserve the last complete image');
  console.log('Atomic thumbnail updates, reverse completion order, stale appearance, and failures passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
