const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/spviz/web/app.js'), 'utf8');
const requests = [];
const metadata = { depth: 16, rows: 2, columns: 3, layer_chunks: true, depth_indices: Array.from({length:16}, (_, i) => i), shape: [16,2,3] };
const context = vm.createContext({
  state: { run: { static_export: { progressive: true } }, slices: new Map(), volumes: new Map(), rawVolumes: new Map() },
  staticBase: './data', staticAssetUrl: url => url, Float32Array, Uint8Array,
  MAX_SLICE_CACHE_BYTES: 100000, MAX_VOLUME_CACHE_BYTES: 100000, MAX_RAW_STATIC_CACHE_BYTES: 100000,
  indexEntries: () => [], qualityLimit: () => 96,
  lruSet: (cache, key, value) => cache.set(key, value),
  downsampleVolume: value => value, downsampleDepth: value => value,
  checkedJson: async url => { requests.push(url); return metadata; },
  fetch: async url => { requests.push(url); return { ok: true, arrayBuffer: async () => new Float32Array([1,2,3,4,5,6]).buffer }; },
});
vm.runInContext(source.slice(source.indexOf('async function checkedBuffer('), source.indexOf('async function getDisplayedSlice(')), context);
(async () => {
  const product = { id: 'large', shape: [16, 256, 1024] };
  const layer = await context.getSlice(product, [0,1,2], 7);
  assert.equal(layer.resolved_layer, 7);
  assert.deepEqual([...layer.values], [1,2,3,4,5,6]);
  assert.equal(requests.filter(url => url.endsWith('.f32')).length, 1);
  assert.ok(requests.includes('./data/layers/large--0-1-2--7.f32'));
  const count = requests.length;
  await context.getSlice(product, [0,1,2], 7);
  assert.equal(requests.length, count, 'cached layers must not refetch');
  requests.length = 0;
  const volume = await context.getVolume(product, [0,1,2], 256, 3);
  assert.equal(volume.depth, 3);
  assert.deepEqual([...volume.depth_indices], [0,8,15]);
  assert.equal(requests.filter(url => url.endsWith('.f32')).length, 3);
  assert.ok(requests.every(url => !url.endsWith('/volumes/large--0-1-2.f32')), 'never fetch the large full volume');
  console.log('Progressive selected-layer loading, reuse, and bounded context requests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
