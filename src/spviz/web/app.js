const state = {
  run: null,
  product: null,
  heldViews: new Map(),
  heldFrame: null,
  nextHeldView: 0,
  captureGroups: [],
  viewAxes: [],
  perm: [],
  layer: 0,
  isolate: false,
  currentOpacity: 1,
  opacity: 0.25,
  opacityLinked: false,
  pixelDensity: 96,
  scaleMin: 0,
  scaleMax: 1,
  logScale: false,
  yaw: 0.3,
  pitch: 0,
  playing: false,
  timer: null,
  renderVersion: 0,
  frame: 0,
  volumes: new Map(),
  rawVolumes: new Map(),
  slices: new Map(),
  bitmaps: new Map(),
  coordinates: new Map(),
  fixedIndices: new Map(),
  theme: "dark",
  colorMap: "auto",
  aspect: "data",
  overviewAspect: "data",
  viewMode: "surface",
};
const embeddedProduct = new URLSearchParams(location.search).get("viewer");
if (embeddedProduct) document.documentElement.classList.add("embedded-viewer");
let comparisonInitialized = false;
let lastComparisonGeometry = "";
let qualityTimer = 0;
const MAX_CANVAS_BACKING_PIXELS = 2_000_000,
  MAX_VOLUME_CACHE_BYTES = 64 * 1024 * 1024,
  MAX_SLICE_CACHE_BYTES = 64 * 1024 * 1024,
  MAX_BITMAP_CACHE_BYTES = 64 * 1024 * 1024,
  MAX_RAW_STATIC_CACHE_BYTES = 96 * 1024 * 1024,
  MAX_CONTEXT_DENSITY = 256;
const $ = (id) => document.getElementById(id),
  hexColors = (colors) =>
    colors.map((hex) => [
      parseInt(hex.slice(1, 3), 16),
      parseInt(hex.slice(3, 5), 16),
      parseInt(hex.slice(5), 16),
    ]),
  colorMaps = {
    spviz: hexColors(["#17213a", "#6177ff", "#28bfa7", "#ed985f", "#eb6170"]),
    viridis: hexColors(["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]),
    plasma: hexColors(["#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"]),
    inferno: hexColors(["#000004", "#420a68", "#932667", "#dd513a", "#fcffa4"]),
    magma: hexColors(["#000004", "#3b0f70", "#8c2981", "#de4968", "#fcfdbf"]),
    cividis: hexColors(["#00224e", "#434e6c", "#7d7c78", "#bcae6c", "#fee838"]),
    coolwarm: hexColors([
      "#3b4cc0",
      "#7093f3",
      "#b9d0f9",
      "#dddddd",
      "#f7b89c",
      "#d95847",
      "#b40426",
    ]),
    twilight: hexColors([
      "#e2d9e2",
      "#9e9ac8",
      "#6276ba",
      "#3e4a89",
      "#356d6c",
      "#587d43",
      "#a57b35",
      "#c85a32",
      "#b5365a",
      "#7e3f78",
      "#e2d9e2",
    ]),
  };
function palette(mode = "sequential") {
  return colorMaps[
    state.colorMap === "auto"
      ? mode === "cyclic"
        ? "twilight"
        : mode === "diverging"
          ? "coolwarm"
          : "spviz"
      : state.colorMap
  ];
}
function usesDivergingPalette(min, max, log = false) {
  return (
    !log &&
    min < 0 &&
    max > 0 &&
    (state.colorMap === "auto" || state.colorMap === "coolwarm")
  );
}
function themeInk() {
  const style = getComputedStyle(document.documentElement);
  return {
    muted: style.getPropertyValue("--canvas-muted").trim(),
    label: style.getPropertyValue("--canvas-label").trim(),
    strong: style.getPropertyValue("--canvas-strong").trim(),
    line: style.getPropertyValue("--canvas-line").trim(),
  };
}
function updateScale() {
  const bounds = state.product ? displayBounds(state.product) : [0, 1],
    cyclic = state.product?.representation === "phase",
    diverging =
      !cyclic && usesDivergingPalette(bounds[0], bounds[1], state.logScale),
    colors = palette(
      cyclic ? "cyclic" : diverging ? "diverging" : "sequential",
    ).map((color) => `rgb(${color.join(",")})`);
  let gradient;
  if (cyclic) {
    gradient = `linear-gradient(90deg,${colors.map((color, index) => `${color} ${(index * 100) / Math.max(1, colors.length - 1)}%`).join(",")})`;
  } else if (diverging) {
    const midpoint = Math.floor(colors.length / 2),
      stops = colors.map((color, index) => {
        const position = (index * 100) / Math.max(1, colors.length - 1);
        return index === midpoint
          ? `transparent ${position}%`
          : `${color} ${position}%`;
      });
    gradient = `linear-gradient(90deg,${stops.join(",")})`;
  } else {
    gradient = `linear-gradient(90deg,transparent 0%,${colors.map((color, index) => `${color} ${((index + 1) * 100) / colors.length}%`).join(",")})`;
  }
  document.documentElement.style.setProperty("--colormap-gradient", gradient);
  document.documentElement.style.setProperty("--binary-color", "var(--red)");
}
function bytes(n) {
  if (!Number.isFinite(n) || n < 0) return "n/a";
  for (const u of ["B", "KiB", "MiB", "GiB"]) {
    if (n < 1024 || u === "GiB")
      return `${n.toFixed(n < 10 && u !== "B" ? 1 : 0)} ${u}`;
    n /= 1024;
  }
}
function permutations(n) {
  const out = [];
  function walk(a, left) {
    if (!left.length) out.push(a);
    else
      left.forEach((v, i) =>
        walk([...a, v], [...left.slice(0, i), ...left.slice(i + 1)]),
      );
  }
  walk(
    [],
    Array.from({ length: n }, (_, i) => i),
  );
  return out;
}
function cachedBytes(value) {
  if (value instanceof HTMLCanvasElement) return value.width * value.height * 4;
  return value?.values?.byteLength || 0;
}
function lruSet(cache, key, value, limit, maximumBytes = Infinity) {
  cache.delete(key);
  cache.set(key, value);
  let totalBytes = 0;
  for (const cached of cache.values()) totalBytes += cachedBytes(cached);
  while (cache.size > limit || totalBytes > maximumBytes) {
    const oldest = cache.keys().next().value,
      removed = cache.get(oldest);
    cache.delete(oldest);
    totalBytes -= cachedBytes(removed);
  }
}
function setVolumeStatus(message, error = false) {
  $("hold-comparison").disabled = !state.heldFrame;
  const status = $("volume-status");
  status.textContent = message;
  status.classList.toggle("visible", Boolean(message));
  status.classList.toggle("error", error);
  $("volume").setAttribute("aria-busy", String(Boolean(message) && !error));
}
function resetVolumeGeometry() {
  $("volume")._geometry = null;
}
function clearVolumeCanvas() {
  state.heldFrame = null;
  $("hold-comparison").disabled = true;
  const canvas = $("volume"),
    width = canvas.width,
    height = canvas.height;
  if (width || height) {
    canvas.width = width;
    canvas.height = height;
  }
  resetVolumeGeometry();
}
function qualityLimit() {
  return state.pixelDensity;
}
function contextQualityLimit() {
  return Math.min(MAX_CONTEXT_DENSITY, qualityLimit());
}
const staticBase = window.SPVIZ_STATIC_BASE;
if (window.SPVIZ_GALLERY_URL) {
  $("gallery-link").href = window.SPVIZ_GALLERY_URL;
  $("gallery-link").hidden = false;
}
function downsampleVolume(volume, limit) {
  const sourceRows = volume.rows,
    sourceColumns = volume.columns,
    rows = Math.min(sourceRows, limit),
    columns = Math.min(sourceColumns, limit),
    rowPositions = Array.from({ length: rows }, (_, row) =>
      Math.round((row * (sourceRows - 1)) / Math.max(1, rows - 1)),
    ),
    columnPositions = Array.from({ length: columns }, (_, column) =>
      Math.round((column * (sourceColumns - 1)) / Math.max(1, columns - 1)),
    );
  if (rows === sourceRows && columns === sourceColumns) return volume;
  const values = new Float32Array(volume.depth * rows * columns);
  for (let depth = 0; depth < volume.depth; depth++)
    for (let row = 0; row < rows; row++) {
      const sourceRow = rowPositions[row];
      for (let column = 0; column < columns; column++) {
        const sourceColumn = columnPositions[column];
        values[(depth * rows + row) * columns + column] =
          volume.values[
            (depth * sourceRows + sourceRow) * sourceColumns + sourceColumn
          ];
      }
    }
  return {
    ...volume,
    rows,
    columns,
    row_indices: rowPositions.map(
      (position) => volume.row_indices?.[position] ?? position,
    ),
    column_indices: columnPositions.map(
      (position) => volume.column_indices?.[position] ?? position,
    ),
    shape: [volume.depth, rows, columns],
    values,
  };
}
function downsampleDepth(volume, limit) {
  const depth = Math.min(volume.depth, Math.max(1, limit));
  if (depth === volume.depth) return volume;
  const sourcePositions = Array.from({ length: depth }, (_, index) =>
      Math.round((index * (volume.depth - 1)) / Math.max(1, depth - 1)),
    ),
    planeSize = volume.rows * volume.columns,
    values = new Float32Array(depth * planeSize);
  sourcePositions.forEach((sourcePosition, index) =>
    values.set(
      volume.values.subarray(
        sourcePosition * planeSize,
        (sourcePosition + 1) * planeSize,
      ),
      index * planeSize,
    ),
  );
  return {
    ...volume,
    depth,
    depth_indices: sourcePositions.map(
      (position) => volume.depth_indices?.[position] ?? position,
    ),
    shape: [depth, volume.rows, volume.columns],
    values,
  };
}
function staticAssetUrl(url) {
  const revision = state.run?.static_export?.revision;
  return staticBase && revision && url.startsWith(`${staticBase}/`) && !url.endsWith("/run.json")
    ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(revision)}` : url;
}
async function checkedJson(url) {
  const response = await fetch(staticAssetUrl(url), url.endsWith("/run.json") ? { cache: "no-cache" } : undefined);
  if (!response.ok)
    throw new Error(`${response.status} ${response.statusText}`);
  try {
    return await response.json();
  } catch {
    throw new Error("The server returned invalid JSON");
  }
}
async function checkedVolume(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await response.text());
  const metadataHeader = response.headers.get("X-Spviz-Metadata");
  if (!metadataHeader) throw new Error("Volume response is missing metadata");
  let metadata;
  try {
    metadata = JSON.parse(metadataHeader);
  } catch {
    throw new Error("Volume response contains invalid metadata");
  }
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength % Float32Array.BYTES_PER_ELEMENT)
    throw new Error("Volume response has an invalid byte length");
  if (
    !Number.isInteger(metadata.rows) ||
    !Number.isInteger(metadata.columns) ||
    metadata.rows < 1 ||
    metadata.columns < 1
  )
    throw new Error("Volume response has invalid dimensions");
  const values = new Float32Array(buffer),
    planeLength = metadata.rows * metadata.columns,
    validLengths = new Set([
      planeLength,
      planeLength * Math.max(1, Number(metadata.depth) || 1),
    ]);
  if (!validLengths.has(values.length))
    throw new Error("Volume response length does not match its metadata");
  return { ...metadata, values };
}
function indexEntries(product, displayedAxes, extra = []) {
  const indices = new Map();
  if (state.product?.id === product.id)
    for (const [axis, value] of state.fixedIndices)
      if (!displayedAxes.includes(axis)) indices.set(axis, value);
  for (const [axis, value] of extra)
    if (!displayedAxes.includes(axis)) indices.set(axis, value);
  return [...indices].sort(([left], [right]) => left - right);
}
function indexedUrl(url, entries) {
  if (!entries.length) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}${entries
    .map(([axis, value]) => `index=${axis}:${value}`)
    .join("&")}`;
}
async function checkedBuffer(url) {
  const response = await fetch(staticAssetUrl(url));
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.arrayBuffer();
}
async function getVolume(
  product,
  perm,
  limit = qualityLimit(),
  depthLimit = 12,
) {
  const fixed = indexEntries(product, perm),
    fixedKey = fixed.map(([axis, value]) => `${axis}:${value}`).join(";"),
    key = `${product.id}:${perm.join(",")}:${limit}:${depthLimit}:${fixedKey}`,
    cached = state.volumes.get(key);
  if (cached) {
    state.volumes.delete(key);
    state.volumes.set(key, cached);
    return cached;
  }
  const contextOnly = state.run?.static_export?.progressive && limit <= 96 && depthLimit <= 12,
    stem = `${product.id}--${perm.join("-")}`,
    sourceKey = `${contextOnly ? "contexts" : "volumes"}/${stem}`;
  let source;
  if (staticBase) {
    const rawKey = `${sourceKey}:${depthLimit}`;
    source = state.rawVolumes.get(rawKey);
    if (!source) {
      source = checkedJson(`${staticBase}/${sourceKey}.json`).then(async metadata => {
        if (!metadata.layer_chunks) {
          const buffer = await checkedBuffer(`${staticBase}/${sourceKey}.f32`);
          return [metadata, buffer];
        }
        const count = Math.min(depthLimit, metadata.depth),
          positions = Array.from({ length: count }, (_, i) =>
            Math.round(i * (metadata.depth - 1) / Math.max(1, count - 1)));
        const chunks = await Promise.all(positions.map(position =>
          checkedBuffer(`${staticBase}/layers/${stem}--${position}.f32`)));
        const planeBytes = metadata.rows * metadata.columns * 4;
        if (chunks.some(chunk => chunk.byteLength !== planeBytes)) throw new Error("Layer length does not match its metadata");
        const values = new Uint8Array(planeBytes * chunks.length);
        chunks.forEach((chunk, index) => values.set(new Uint8Array(chunk), index * planeBytes));
        return [{ ...metadata, depth: chunks.length, depth_indices: positions.map(i => metadata.depth_indices[i]), shape: [chunks.length, metadata.rows, metadata.columns] }, values.buffer];
      })
        .then(([metadata, buffer]) => {
          if (
            buffer.byteLength % Float32Array.BYTES_PER_ELEMENT ||
            !Number.isInteger(metadata.depth) ||
            !Number.isInteger(metadata.rows) ||
            !Number.isInteger(metadata.columns) ||
            metadata.depth < 1 ||
            metadata.rows < 1 ||
            metadata.columns < 1
          )
            throw new Error("Static volume has invalid metadata");
          const values = new Float32Array(buffer),
            expected = metadata.depth * metadata.rows * metadata.columns;
          if (values.length !== expected)
            throw new Error("Static volume length does not match its metadata");
          return { ...metadata, values };
        })
        .then((result) => {
          lruSet(
            state.rawVolumes,
            rawKey,
            result,
            12,
            MAX_RAW_STATIC_CACHE_BYTES,
          );
          return result;
        })
        .catch((error) => {
          state.rawVolumes.delete(rawKey);
          throw error;
        });
      state.rawVolumes.set(rawKey, source);
    }
  }
  const pending = (
    staticBase
      ? Promise.resolve(source).then((volume) =>
          downsampleVolume(downsampleDepth(volume, depthLimit), limit),
        )
      : checkedVolume(
          indexedUrl(
            `/api/product/${encodeURIComponent(product.id)}/volume?perm=${perm.join(",")}&limit=${limit}&depth_limit=${depthLimit}`,
            fixed,
          ),
        )
  )
    .then((result) => {
      lruSet(state.volumes, key, result, 12, MAX_VOLUME_CACHE_BYTES);
      return result;
    })
    .catch((error) => {
      state.volumes.delete(key);
      throw error;
    });
  lruSet(state.volumes, key, pending, 12);
  return pending;
}
async function getSlice(product, perm, layer, limit = qualityLimit()) {
  const sourceDepth = perm.length === 3 ? product.shape[perm[0]] : 1,
    clamped = Math.max(0, Math.min(layer, sourceDepth - 1)),
    fixed = indexEntries(product, perm),
    fixedKey = fixed.map(([axis, value]) => `${axis}:${value}`).join(";"),
    key = `${product.id}:${perm.join(",")}:${clamped}:${limit}:${fixedKey}`,
    cached = state.slices.get(key);
  if (cached) return cached;
  let pending;
  if (staticBase && state.run?.static_export?.progressive) {
    const stem = `${product.id}--${perm.join("-")}`;
    pending = checkedJson(`${staticBase}/volumes/${stem}.json`).then(async metadata => {
      const buffer = await checkedBuffer(metadata.layer_chunks
        ? `${staticBase}/layers/${stem}--${clamped}.f32`
        : `${staticBase}/volumes/${stem}.f32`);
      const planeSize = metadata.rows * metadata.columns;
      if (buffer.byteLength !== planeSize * 4 * (metadata.layer_chunks ? 1 : metadata.depth))
        throw new Error("Layer length does not match its metadata");
      const values = metadata.layer_chunks ? new Float32Array(buffer)
        : new Float32Array(buffer, clamped * planeSize * 4, planeSize);
      return { ...downsampleVolume({ ...metadata, depth: 1, values }, limit), layer: clamped, resolved_layer: clamped };
    });
  } else if (staticBase) {
    pending = getVolume(product, perm, limit, sourceDepth).then((volume) => {
      const depthIndices =
          volume.depth_indices ||
          Array.from({ length: volume.depth }, (_, index) => index),
        exactPosition = depthIndices.indexOf(clamped),
        resolvedPosition =
          exactPosition >= 0
            ? exactPosition
            : depthIndices.reduce(
                (best, value, index) =>
                  Math.abs(value - clamped) <
                  Math.abs(depthIndices[best] - clamped)
                    ? index
                    : best,
                0,
              ),
        planeSize = volume.rows * volume.columns;
      return {
        ...volume,
        layer: clamped,
        resolved_layer: depthIndices[resolvedPosition],
        values: volume.values.subarray(
          resolvedPosition * planeSize,
          (resolvedPosition + 1) * planeSize,
        ),
      };
    });
  } else {
    pending = checkedVolume(
      indexedUrl(
        `/api/product/${encodeURIComponent(product.id)}/slice?perm=${perm.join(",")}&layer=${clamped}&limit=${limit}&format=f32`,
        fixed,
      ),
    );
  }
  pending = pending
    .then((result) => {
      lruSet(state.slices, key, result, 48, MAX_SLICE_CACHE_BYTES);
      return result;
    })
    .catch((error) => {
      state.slices.delete(key);
      throw error;
    });
  lruSet(state.slices, key, pending, 48);
  return pending;
}
async function getDisplayedSlice(product, perm, layer) {
  const sliced2D = perm.length === 2 && state.viewMode === "slices";
  return sliced2D
    ? get2DTrace(product, perm, layer)
    : getSlice(product, perm, layer);
}
async function get2DTrace(product, perm, layer, limit = qualityLimit()) {
  const clamped = Math.max(0, Math.min(layer, product.shape[perm[0]] - 1));
  if (!staticBase) {
    const fixed = indexEntries(product, [perm[1]], [[perm[0], clamped]]),
      fixedKey = fixed.map(([axis, value]) => `${axis}:${value}`).join(";"),
      key = `${product.id}:trace:${perm.join(",")}:${clamped}:${limit}:${fixedKey}`;
    let pending = state.slices.get(key);
    if (!pending) {
      pending = checkedVolume(
        indexedUrl(
          `/api/product/${encodeURIComponent(product.id)}/slice?perm=${perm[1]}&layer=0&limit=${limit}&format=f32`,
          fixed,
        ),
      )
        .then((result) => {
          const trace = {
            ...result,
            layer: clamped,
            resolved_layer: clamped,
            source_plane_shape: [1, product.shape[perm[1]]],
            row_indices: [clamped],
          };
          lruSet(state.slices, key, trace, 48, MAX_SLICE_CACHE_BYTES);
          return trace;
        })
        .catch((error) => {
          state.slices.delete(key);
          throw error;
        });
      lruSet(state.slices, key, pending, 48);
    }
    return pending;
  }
  const plane = await getVolume(product, perm, Number.MAX_SAFE_INTEGER, 1),
    rowIndices =
      plane.row_indices ||
      Array.from({ length: plane.rows }, (_, index) => index),
    exactPosition = rowIndices.indexOf(clamped),
    rowPosition =
      exactPosition >= 0
        ? exactPosition
        : rowIndices.reduce(
            (best, value, index) =>
              Math.abs(value - clamped) < Math.abs(rowIndices[best] - clamped)
                ? index
                : best,
            0,
          ),
    columns = Math.min(plane.columns, limit),
    columnPositions = Array.from({ length: columns }, (_, column) =>
      Math.round((column * (plane.columns - 1)) / Math.max(1, columns - 1)),
    ),
    values = Float32Array.from(
      columnPositions,
      (column) => plane.values[rowPosition * plane.columns + column],
    );
  return {
    ...plane,
    rows: 1,
    columns,
    depth: 1,
    layer: clamped,
    resolved_layer: rowIndices[rowPosition],
    source_plane_shape: [1, product.shape[perm[1]]],
    row_indices: [rowIndices[rowPosition]],
    column_indices: columnPositions.map(
      (position) => plane.column_indices?.[position] ?? position,
    ),
    shape: [1, 1, columns],
    values,
  };
}
async function getCoordinates(product, axis) {
  const key = `${product.id}:${axis}`;
  if (state.coordinates.has(key)) return state.coordinates.get(key);
  const url = staticBase
      ? `${staticBase}/coordinates/${product.id}--${axis}.json`
      : `/api/product/${encodeURIComponent(product.id)}/coordinates/${axis}`,
    pending = checkedJson(url)
      .then((result) => {
        state.coordinates.set(key, result);
        return result;
      })
      .catch((error) => {
        state.coordinates.delete(key);
        throw error;
      });
  state.coordinates.set(key, pending);
  return pending;
}
function coordinateValue(descriptor, index) {
  const clamped = Math.max(
    0,
    Math.min(index, (descriptor.length ?? Infinity) - 1),
  );
  if (descriptor.encoding === "linear")
    return descriptor.start + descriptor.step * clamped;
  if (descriptor.encoding === "sampled" && descriptor.indices?.length) {
    const exact = descriptor.indices.indexOf(clamped);
    if (exact >= 0) return descriptor.values[exact];
    let upper = descriptor.indices.findIndex(
      (candidate) => candidate > clamped,
    );
    if (upper < 0) upper = descriptor.indices.length - 1;
    const lower = Math.max(0, upper - 1),
      lowerIndex = descriptor.indices[lower],
      upperIndex = descriptor.indices[upper],
      lowerValue = descriptor.values[lower],
      upperValue = descriptor.values[upper];
    if (
      typeof lowerValue === "number" &&
      typeof upperValue === "number" &&
      upperIndex !== lowerIndex
    )
      return (
        lowerValue +
        ((upperValue - lowerValue) * (clamped - lowerIndex)) /
          (upperIndex - lowerIndex)
      );
    return clamped - lowerIndex <= upperIndex - clamped
      ? lowerValue
      : upperValue;
  }
  return descriptor.values?.[clamped] ?? clamped;
}
function formatCoordinate(value) {
  if (typeof value === "number")
    return Number.isInteger(value)
      ? String(value)
      : Number(value).toPrecision(4);
  return String(value);
}
function coordinateLabel(axis, index) {
  const descriptor = state.coordinates.get(`${state.product.id}:${axis}`);
  if (!descriptor || descriptor instanceof Promise) return String(index);
  const value = coordinateValue(descriptor, index);
  return `${formatCoordinate(value)}${descriptor.units ? ` ${descriptor.units}` : ""}`;
}
function axisRangeLabel(axis) {
  const descriptor = state.coordinates.get(`${state.product.id}:${axis}`);
  if (!descriptor || descriptor instanceof Promise)
    return state.product.axes[axis];
  const first = formatCoordinate(coordinateValue(descriptor, 0)),
    last = formatCoordinate(
      coordinateValue(descriptor, state.product.shape[axis] - 1),
    ),
    units = descriptor.units ? ` ${descriptor.units}` : "";
  return `${state.product.axes[axis]}: ${first} → ${last}${units}`;
}
function axisTitle(axis) {
  const descriptor = state.coordinates.get(`${state.product.id}:${axis}`);
  return `${state.product.axes[axis]}${descriptor && !(descriptor instanceof Promise) && descriptor.units ? ` (${descriptor.units})` : ""}`;
}
function axisTick(axis, fraction) {
  const descriptor = state.coordinates.get(`${state.product.id}:${axis}`),
    size = state.product.shape[axis],
    index = Math.round(fraction * (size - 1));
  if (!descriptor || descriptor instanceof Promise) return String(index);
  return formatCoordinate(coordinateValue(descriptor, index));
}
function syncFixedDimensionControls() {
  const fieldset = $("fixed-dimensions"),
    controls = $("fixed-dimension-controls"),
    axes = [...state.fixedIndices.keys()];
  controls.replaceChildren();
  fieldset.hidden = !axes.length;
  $("fixed-dimension-note").hidden = !staticBase;
  for (const axis of axes) {
    const row = document.createElement("div"),
      label = document.createElement("label"),
      name = document.createElement("span"),
      output = document.createElement("output"),
      input = document.createElement("input"),
      inputId = `fixed-axis-${axis}`,
      updateLabel = () => {
        output.textContent = `${coordinateLabel(axis, +input.value)} · ${+input.value + 1}/${state.product.shape[axis]}`;
        input.setAttribute("aria-valuetext", output.textContent);
      };
    row.className = "fixed-dimension";
    label.htmlFor = inputId;
    name.textContent = state.product.axes[axis];
    output.id = `${inputId}-output`;
    label.append(name, output);
    input.id = inputId;
    input.type = "range";
    input.min = "0";
    input.max = String(state.product.shape[axis] - 1);
    input.value = String(state.fixedIndices.get(axis) || 0);
    input.disabled = Boolean(staticBase);
    input.setAttribute("aria-describedby", output.id);
    input.oninput = () => {
      state.fixedIndices.set(axis, +input.value);
      updateLabel();
      $("cell").textContent = "Click a cell";
      resetVolumeGeometry();
      setVolumeStatus("Loading fixed slice…");
      clearTimeout(qualityTimer);
      qualityTimer = setTimeout(scheduleDraw, 110);
    };
    updateLabel();
    row.append(label, input);
    controls.append(row);
  }
}
function drawSheetAxes(context, geometry) {
  const { x, y, width, height, xAxis, yAxis, light = false } = geometry,
    ticks = [0, 0.25, 0.5, 0.75, 1],
    muted = light ? "#4b5568" : "#91a0b8",
    label = light ? "#283142" : "#b7c2d6",
    strong = light ? "#111827" : "#eef2ff";
  context.save();
  context.strokeStyle = muted;
  context.fillStyle = label;
  context.lineWidth = 1;
  context.font = "11px system-ui";
  context.textAlign = "center";
  context.beginPath();
  context.moveTo(x, y + height + 7);
  context.lineTo(x + width, y + height + 7);
  context.stroke();
  for (const fraction of ticks) {
    const tx = x + fraction * width;
    context.beginPath();
    context.moveTo(tx, y + height + 4);
    context.lineTo(tx, y + height + 11);
    context.stroke();
    context.fillText(axisTick(xAxis, fraction), tx, y + height + 24);
  }
  context.fillStyle = strong;
  context.font = "12px system-ui";
  context.fillText(axisTitle(xAxis), x + width / 2, y + height + 41);
  if (yAxis !== null) {
    context.strokeStyle = muted;
    context.fillStyle = label;
    context.font = "11px system-ui";
    context.textAlign = "right";
    context.beginPath();
    context.moveTo(x - 7, y);
    context.lineTo(x - 7, y + height);
    context.stroke();
    for (const fraction of ticks) {
      const ty = y + fraction * height;
      context.beginPath();
      context.moveTo(x - 11, ty);
      context.lineTo(x - 4, ty);
      context.stroke();
      context.fillText(axisTick(yAxis, fraction), x - 15, ty + 4);
    }
    context.save();
    context.translate(x - 66, y + height / 2);
    context.rotate(-Math.PI / 2);
    context.fillStyle = strong;
    context.font = "12px system-ui";
    context.textAlign = "center";
    context.fillText(axisTitle(yAxis), 0, 0);
    context.restore();
  }
  context.restore();
}
function drawDepthAxis(
  context,
  axis,
  originX,
  originY,
  offsetX,
  offsetY,
  slots,
  selectedPosition,
  selectedLayer,
  light = false,
) {
  if (slots <= 1) return;
  const length = Math.hypot(offsetX, offsetY) || 1,
    normalX = -offsetY / length,
    normalY = offsetX / length,
    startX = originX,
    startY = originY,
    endX = startX + offsetX * (slots - 1),
    endY = startY + offsetY * (slots - 1),
    muted = light ? "#4b5568" : "#91a0b8",
    label = light ? "#283142" : "#b7c2d6",
    strong = light ? "#111827" : "#eef2ff";
  context.save();
  context.strokeStyle = muted;
  context.fillStyle = label;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(startX, startY);
  context.lineTo(endX, endY);
  context.stroke();
  context.font = "11px system-ui";
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    const x = startX + (endX - startX) * fraction,
      y = startY + (endY - startY) * fraction,
      tick = 4;
    context.beginPath();
    context.moveTo(x - normalX * tick, y - normalY * tick);
    context.lineTo(x + normalX * tick, y + normalY * tick);
    context.stroke();
    context.textAlign = normalX < -0.2 ? "right" : "left";
    context.textBaseline =
      normalY < -0.2 ? "bottom" : normalY > 0.2 ? "top" : "middle";
    context.fillText(
      axisTick(axis, fraction),
      x + normalX * 9,
      y + normalY * 9,
    );
  }
  const selectedX = startX + offsetX * selectedPosition,
    selectedY = startY + offsetY * selectedPosition;
  context.strokeStyle = strong;
  context.lineWidth = 2.5;
  context.beginPath();
  context.moveTo(selectedX - normalX * 6, selectedY - normalY * 6);
  context.lineTo(selectedX + normalX * 6, selectedY + normalY * 6);
  context.stroke();
  context.fillStyle = strong;
  context.font = "12px system-ui";
  context.textAlign = normalX < -0.2 ? "right" : "left";
  context.textBaseline = normalY < 0 ? "bottom" : "top";
  context.fillText(
    `${axisTitle(axis)} · selected ${coordinateLabel(axis, selectedLayer)}`,
    selectedX + normalX * 14,
    selectedY + normalY * 14,
  );
  context.restore();
}
function drawAffineImage(context, image, x, y, u, v) {
  context.save();
  context.transform(
    u.x / image.width,
    u.y / image.width,
    v.x / image.height,
    v.y / image.height,
    x,
    y,
  );
  context.drawImage(image, 0, 0);
  context.restore();
}
function strokeAffinePlane(context, x, y, u, v, color, width = 1) {
  context.save();
  context.strokeStyle = color;
  context.lineWidth = width;
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x + u.x, y + u.y);
  context.lineTo(x + u.x + v.x, y + u.y + v.y);
  context.lineTo(x + v.x, y + v.y);
  context.closePath();
  context.stroke();
  context.restore();
}
function drawProjectedAxes(context, geometry, light = false) {
  const { x, y, u, v, xAxis, yAxis } = geometry,
    ink = light ? { muted: "#4b5568", strong: "#111827" } : themeInk();
  context.save();
  context.strokeStyle = ink.muted;
  context.fillStyle = ink.strong;
  context.lineWidth = 1.2;
  context.font = "12px system-ui";
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x + u.x, y + u.y);
  context.moveTo(x, y);
  context.lineTo(x + v.x, y + v.y);
  context.stroke();
  context.textAlign = "center";
  context.fillText(
    axisTitle(xAxis),
    x + u.x / 2 - v.x * 0.08,
    y + u.y / 2 - v.y * 0.08,
  );
  context.save();
  context.translate(x + v.x / 2 - u.x * 0.07, y + v.y / 2 - u.y * 0.07);
  context.rotate(Math.atan2(v.y, v.x) - Math.PI / 2);
  context.fillText(axisTitle(yAxis), 0, 0);
  context.restore();
  context.restore();
}
function canvasSize(canvas) {
  const rect = canvas.getBoundingClientRect(),
    nativeRatio = devicePixelRatio || 1,
    ratio = Math.min(
      nativeRatio,
      Math.sqrt(
        MAX_CANVAS_BACKING_PIXELS /
          Math.max(1, Math.ceil(rect.width) * Math.ceil(rect.height)),
      ),
    );
  if (
    canvas.width !== Math.round(rect.width * ratio) ||
    canvas.height !== Math.round(rect.height * ratio)
  ) {
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  canvas._renderRatio = ratio;
  return { context, width: rect.width, height: rect.height };
}
function scaleBounds(product, log, minFraction = 0, maxFraction = 1) {
  const statsMin =
      product?.stats?.display_min ??
      product?.stats?.value_min ??
      product?.stats?.min ??
      0,
    configuredMin = product?.display_min,
    rawMin = configuredMin ?? statsMin,
    dataMin = log ? Math.max(0, rawMin) : rawMin,
    configuredMax = product?.display_max,
    dataMax = Math.max(
      dataMin + 1e-12,
      configuredMax ??
        product?.stats?.display_max ??
        product?.stats?.value_max ??
        product?.stats?.max ??
        1,
    );
  if (!log)
    return [
      dataMin + (dataMax - dataMin) * minFraction,
      dataMin + (dataMax - dataMin) * maxFraction,
    ];
  const floor = Math.max(dataMin, dataMax * 1e-6, 1e-12),
    lo = Math.log(floor),
    hi = Math.log(dataMax);
  return [
    Math.exp(lo + (hi - lo) * minFraction),
    Math.exp(lo + (hi - lo) * maxFraction),
  ];
}
function displayedPeak(product, slice) {
  const value = product?.stats?.display_max ?? slice?.peak ?? 0;
  return Number.isFinite(value) ? value.toFixed(4) : "n/a";
}
function displayBounds(product = state.product) {
  return scaleBounds(product, state.logScale, state.scaleMin, state.scaleMax);
}
function tracePosition(value, min, max, log) {
  if (log)
    return (
      (Math.log(Math.max(1e-12, value)) - Math.log(Math.max(1e-12, min))) /
      Math.max(
        1e-12,
        Math.log(Math.max(max, min * 1.0001)) - Math.log(Math.max(1e-12, min)),
      )
    );
  return (value - min) / Math.max(1e-12, max - min);
}
function traceColor(fraction) {
  const colors = palette(),
    scaled = Math.max(0, Math.min(1, fraction)) * (colors.length - 1),
    low = Math.floor(scaled),
    high = Math.min(colors.length - 1, low + 1),
    mix = scaled - low;
  return `rgb(${colors[low].map((channel, index) => Math.round(channel + (colors[high][index] - channel) * mix)).join(",")})`;
}
function balancedTraceColors(light = false) {
  return palette().map(([r, g, b]) => {
    const max = Math.max(r, g, b) / 255,
      min = Math.min(r, g, b) / 255,
      delta = max - min,
      lightness = (max + min) / 2,
      saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
    let hue = 0;
    if (delta) {
      if (max === r / 255) hue = 60 * (((g - b) / 255 / delta) % 6);
      else if (max === g / 255) hue = 60 * ((b - r) / 255 / delta + 2);
      else hue = 60 * ((r - g) / 255 / delta + 4);
    }
    if (hue < 0) hue += 360;
    return `hsl(${hue.toFixed(1)} ${Math.max(48, saturation * 100).toFixed(1)}% ${light ? 42 : 62}%)`;
  });
}
function drawTrace(
  context,
  slice,
  x,
  y,
  width,
  height,
  min,
  max,
  log,
  light = false,
  alpha = 1,
  showYAxis = true,
) {
  const ink = light
      ? { muted: "#4b5568", label: "#283142", strong: "#111827" }
      : themeInk(),
    values = slice.values,
    segments = [];
  context.save();
  context.globalAlpha = alpha;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = ink.muted;
  context.lineWidth = 1;
  context.globalAlpha = 0.16 * alpha;
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    const gy = y + fraction * height;
    context.beginPath();
    context.moveTo(x, gy);
    context.lineTo(x + width, gy);
    context.stroke();
  }
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    const gx = x + fraction * width;
    context.beginPath();
    context.moveTo(gx, y);
    context.lineTo(gx, y + height);
    context.stroke();
  }
  let segment = [];
  for (let i = 0; i < values.length; i++) {
    const value = values[i];
    if (!Number.isFinite(value)) {
      if (segment.length) segments.push(segment);
      segment = [];
      continue;
    }
    const fraction = Math.max(
      0,
      Math.min(1, tracePosition(value, min, max, log)),
    );
    segment.push([
      x + (i * width) / Math.max(1, values.length - 1),
      y + height * (1 - fraction),
    ]);
  }
  if (segment.length) segments.push(segment);
  if (segments.length) {
    const gradient = context.createLinearGradient(x, 0, x + width, 0),
      colors = balancedTraceColors(light),
      baseline =
        min < 0 && max > 0
          ? y + height * (1 - tracePosition(0, min, max, false))
          : y + height;
    colors.forEach((color, index) =>
      gradient.addColorStop(index / Math.max(1, colors.length - 1), color),
    );
    for (const points of segments) {
      if (points.length > 1) {
        context.globalAlpha = 0.1 * alpha;
        context.fillStyle = gradient;
        context.beginPath();
        context.moveTo(points[0][0], baseline);
        for (const point of points) context.lineTo(point[0], point[1]);
        context.lineTo(points.at(-1)[0], baseline);
        context.closePath();
        context.fill();
        context.globalAlpha = alpha;
        context.strokeStyle = gradient;
        context.lineWidth = 2;
        context.beginPath();
        context.moveTo(points[0][0], points[0][1]);
        for (const point of points.slice(1)) context.lineTo(point[0], point[1]);
        context.stroke();
      } else {
        context.globalAlpha = alpha;
        context.fillStyle = gradient;
        context.beginPath();
        context.arc(points[0][0], points[0][1], 2.25, 0, Math.PI * 2);
        context.fill();
      }
    }
  }
  context.globalAlpha = 0.55 * alpha;
  context.strokeStyle = ink.muted;
  context.lineWidth = 1;
  context.strokeRect(x, y, width, height);
  if (min < 0 && max > 0) {
    const zeroY = y + height * (1 - tracePosition(0, min, max, false));
    context.globalAlpha = 0.65 * alpha;
    context.beginPath();
    context.moveTo(x, zeroY);
    context.lineTo(x + width, zeroY);
    context.stroke();
  }
  if (showYAxis) {
    context.globalAlpha = alpha;
    context.fillStyle = ink.label;
    context.font = "11px ui-monospace,monospace";
    context.textAlign = "right";
    context.fillText(max.toPrecision(3), x - 8, y + 4);
    context.fillText(min.toPrecision(3), x - 8, y + height + 4);
  }
  context.restore();
}
function bitmapFor(slice, min, max, log, scope) {
  const indexKey = Object.entries(slice.indices || {})
      .sort(([left], [right]) => Number(left) - Number(right))
      .map(([axis, value]) => `${axis}:${value}`)
      .join(";"),
    product = state.run?.products?.find((entry) => entry.id === slice.product),
    binary = isBinaryProduct(product),
    cyclic = !binary && slice.representation === "phase",
    diverging = !cyclic && usesDivergingPalette(min, max, log),
    key = `${state.colorMap}:${scope}:${slice.product}:${slice.permutation.join(",")}:${slice.layer}:${slice.rows}x${slice.columns}:${indexKey}:${min}:${max}:${log}:${cyclic}`,
    cached = state.bitmaps.get(key);
  if (cached) {
    state.bitmaps.delete(key);
    state.bitmaps.set(key, cached);
    return cached;
  }
  const canvas = document.createElement("canvas");
  canvas.width = slice.columns;
  canvas.height = slice.rows;
  const context = canvas.getContext("2d"),
    image = context.createImageData(slice.columns, slice.rows),
    pixels = image.data;
  const logMin = Math.log(Math.max(1e-12, min)),
    logSpan = Math.max(1e-12, Math.log(Math.max(min * 1.0001, max)) - logMin),
    linearSpan = Math.max(1e-12, max - min),
    colors = binary
      ? colorMaps.spviz
      : palette(cyclic ? "cyclic" : diverging ? "diverging" : "sequential");
  for (let i = 0; i < slice.values.length; i++) {
    const value = slice.values[i],
      offset = i * 4;
    if (!Number.isFinite(value) || (!cyclic && !diverging && value <= min)) {
      pixels[offset + 3] = 0;
      continue;
    }
    let strength,
      t = log
        ? (Math.log(Math.max(1e-12, value)) - logMin) / logSpan
        : (value - min) / linearSpan;
    if (diverging) {
      if (value < 0) {
        t = 0.5 * (1 - Math.min(1, Math.abs(value / min)));
        strength = Math.min(1, Math.abs(value / min));
      } else {
        t = 0.5 + 0.5 * Math.min(1, value / max);
        strength = Math.min(1, value / max);
      }
    }
    t = Math.max(0, Math.min(1, t));
    const scaled =
        (cyclic || diverging ? t : Math.pow(t, 0.72)) * (colors.length - 1),
      low = Math.floor(scaled),
      high = Math.min(colors.length - 1, low + 1),
      mix = scaled - low,
      color = colors[low].map((channel, index) =>
        Math.round(channel + (colors[high][index] - channel) * mix),
      ),
      fade = Math.min(1, (diverging ? strength : t) / 0.12),
      alpha = cyclic ? 1 : fade * fade * (3 - 2 * fade);
    pixels[offset] = color[0];
    pixels[offset + 1] = color[1];
    pixels[offset + 2] = color[2];
    pixels[offset + 3] = Math.round(255 * alpha);
  }
  context.putImageData(image, 0, 0);
  lruSet(state.bitmaps, key, canvas, 192, MAX_BITMAP_CACHE_BYTES);
  return canvas;
}
function overviewAppearance() {
  return `${state.colorMap}:${state.overviewAspect}:${state.theme}`;
}
async function drawOverview(product, canvas) {
  const appearance = overviewAppearance(),
    staging = document.createElement("canvas");
  staging.width = 640;
  staging.height = 480;
  const version = (canvas._previewVersion || 0) + 1;
  canvas._previewVersion = version;
  const current = () => canvas._previewVersion === version && overviewAppearance() === appearance;
  const publish = () => {
    if (!current()) return;
    // Keep the old complete image until its replacement is ready. Resizing and
    // copying happen in one synchronous task, so no intermediate frame paints.
    canvas.width = staging.width;
    canvas.height = staging.height;
    canvas.getContext("2d").drawImage(staging, 0, 0);
  };
  const status = canvas.parentElement.querySelector(".product-status");
  status.hidden = false;
  status.textContent = "Loading preview…";
  status.className = "product-status";
  try {
    if ((!staticBase || state.run?.static_export?.previews) && state.colorMap === "auto" && (product.overview_aspect || state.overviewAspect) === (product.overview_aspect || "data")) {
      const image = new Image();
      image.src = staticBase
        ? staticAssetUrl(`${staticBase}/previews/${encodeURIComponent(product.id)}.png`)
        : `/api/product/${encodeURIComponent(product.id)}/preview.png`;
      await image.decode();
      if (!current()) return;
      staging.getContext("2d").drawImage(image, 0, 0, 640, 480);
      publish();
      status.hidden = true;
      status.textContent = "";
      return;
    }
    const axes = (product.view_axes || product.axes.slice(0, 3)).map((name) =>
        product.axes.indexOf(name),
      ),
      depth = axes.length === 3 ? product.shape[axes[0]] : 1,
      count = Math.min(12, depth),
      volume = await getVolume(product, axes, 96, count),
      planeSize = volume.rows * volume.columns,
      slices = Array.from({ length: volume.depth }, (_, position) => ({
        ...volume,
        layer: volume.depth_indices?.[position] ?? position,
        values: volume.values.subarray(
          position * planeSize,
          (position + 1) * planeSize,
        ),
      })),
      context = staging.getContext("2d"), width = staging.width, height = staging.height,
      log = product.representation !== "phase" && product.scale === "log",
      [min, max] = scaleBounds(product, log);
    slices[0] = await getSlice(product, axes, 0, 96);
    if (!current()) return;
    if (axes.length === 1) {
      drawTrace(
        context,
        slices[0],
        48,
        28,
        width - 66,
        height - 62,
        min,
        max,
        log,
        state.theme === "light",
      );
    } else {
      const overviewAspect = product.overview_aspect || state.overviewAspect,
        ratio = width / 640,
        offsetX = 16 * ratio, offsetY = -11 * ratio,
        boxWidth = Math.max(1, width - 190 * ratio - offsetX * (slices.length - 1)),
        boxHeight = Math.max(1, height - 120 * ratio + offsetY * (slices.length - 1)),
        dataAspect = volume.source_plane_shape[1] / Math.max(1, volume.source_plane_shape[0]),
        aspect = overviewAspect === "equal" ? 1 : dataAspect,
        planeWidth = overviewAspect === "fit" ? boxWidth : Math.min(boxWidth, boxHeight * aspect),
        planeHeight = overviewAspect === "fit" ? boxHeight : planeWidth / aspect,
        baseX = (width - planeWidth - offsetX * (slices.length - 1)) / 2 + 35 * ratio,
        baseY = (height - planeHeight + offsetY * (slices.length - 1) - 50 * ratio) / 2 - offsetY * (slices.length - 1);
      context.imageSmoothingEnabled = false;
      for (let i = slices.length - 1; i >= 0; i--) {
        const dx = i * offsetX,
          dy = i * offsetY,
          image = bitmapFor(slices[i], min, max, log, "overview");
        context.save();
        context.globalAlpha = i === 0 ? 1 : 0.25;
        context.drawImage(
          image,
          baseX + dx,
          baseY + dy,
          planeWidth,
          planeHeight,
        );
        context.globalAlpha = 0.32;
        context.strokeStyle = "#6177ff";
        context.lineWidth = 1;
        context.strokeRect(baseX + dx, baseY + dy, planeWidth, planeHeight);
        context.restore();
      }
    }
    publish();
    status.hidden = true;
    status.textContent = "";
  } catch (error) {
    if (!current()) return;
    status.hidden = false;
    status.textContent = `Preview failed · ${error.message} · click to retry`;
    status.className = "product-status error";
    throw error;
  }
}
function isBinaryProduct(product = state.product) {
  return Boolean(
    product && (product.units === "binary" || product.dtype === "bool"),
  );
}
function stopPlayback() {
  clearInterval(state.timer);
  state.timer = null;
  state.playing = false;
  const play = $("play");
  play.textContent = "Play layers";
  play.setAttribute("aria-pressed", "false");
}
function syncScaleLegend() {
  const binary = isBinaryProduct(),
    scale = $("scale"),
    [min, max] = state.product ? displayBounds() : [0, 1],
    cyclic = !binary && state.product?.representation === "phase",
    diverging =
      !binary && !cyclic && usesDivergingPalette(min, max, state.logScale);
  scale.classList.toggle("binary", binary);
  scale.setAttribute(
    "aria-label",
    binary
      ? "Binary value legend: zero is background and one is active"
      : cyclic
        ? "Cyclic phase legend: both ends of the color scale represent the phase wrap"
        : diverging
          ? "Diverging value legend: zero is transparent, with negative and positive values shown in contrasting colors"
          : "Continuous value color scale",
  );
  $("scale-label-low").textContent = binary
    ? "0 · background"
    : cyclic
      ? "−π"
      : diverging
        ? "negative"
        : "low / transparent";
  $("scale-label-high").textContent = binary
    ? "1 · active"
    : cyclic
      ? "+π · wraps"
      : diverging
        ? "positive"
        : "high";
}
function handleViewerError(error) {
  stopPlayback();
  setVolumeStatus(`Unable to load: ${error.message}`, true);
}
// A capture occupies one pipeline column; its views move along the vertical axis.
function groupCaptures(products) {
  const groups = new Map();
  for (const product of products) {
    const id = product.capture_id || product.id;
    if (!groups.has(id)) groups.set(id, { id, name: product.capture_name || product.name, views: [] });
    groups.get(id).views.push(product);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    active: Math.max(0, group.views.findIndex((view) => view.is_primary || view.view_name === view.primary_view)),
  }));
}
function wrapView(index, count) {
  return ((index % count) + count) % count;
}
function updateViewDepth(group) {
  const count = group.cards.length, position = group.position ?? group.active;
  group.cards.forEach((card, index) => {
    const distance = count === 1 ? 0 : wrapView(index - position + count / 2, count) - count / 2,
      t = Math.min(1, Math.abs(distance)),
      focus = 1 - t * t * (3 - 2 * t),
      // Three faces occupy the visible arc; additional choices pass behind it.
      angle = distance * (count === 2 ? Math.PI : 2 * Math.PI / 3),
      depth = (1 - Math.cos(angle)) / 2,
      y = count === 2 ? -80 * depth + 30 * Math.sin(angle) : 85 * Math.sin(angle),
      rear = count <= 2 ? 1 : Math.max(0, Math.min(1, (1.5 - Math.abs(distance)) * 4));
    card.style.setProperty("--view-x", "0px");
    card.style.setProperty("--view-shift", `${y}px`);
    card.style.setProperty("--view-scale", String(0.25 + 0.75 * focus));
    card.style.setProperty("--view-opacity", String(rear));
    card.style.setProperty("--view-tilt", "0deg");
    card.style.zIndex = String(1 + Math.round(focus * 100));
    card.style.visibility = rear === 0 ? "hidden" : "visible";
    card.style.pointerEvents = rear < 0.2 ? "none" : "auto";
  });
}
function markPrimary(group, index) {
  group.active = wrapView(index, group.views.length);
  group.cards.forEach((card, i) => {
    card.classList.toggle("primary", i === group.active);
    card.setAttribute("aria-current", String(i === group.active));
  });
  group.viewList.value = String(group.active);
  group.previous.disabled = group.next.disabled = group.views.length < 2;
  group.label.textContent = `${group.views[group.active].view_name || "Default"} · ${group.active + 1}/${group.views.length}`;
}
function animateViewPosition(group, target, behavior = "smooth") {
  cancelAnimationFrame(group.animation);
  group.target = target;
  const start = group.position ?? group.active;
  if (behavior === "instant" || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    group.position = target;
    updateViewDepth(group);
    return;
  }
  const started = performance.now();
  const frame = now => {
    if (!group.viewport.isConnected) return;
    const t = Math.min(1, (now - started) / 260), ease = 1 - (1 - t) ** 3;
    group.position = start + (target - start) * ease;
    updateViewDepth(group);
    if (t < 1) group.animation = requestAnimationFrame(frame);
  };
  group.animation = requestAnimationFrame(frame);
}
function centerView(group, index, behavior = "smooth") {
  const count = group.views.length, base = group.target ?? group.position ?? group.active,
    requested = index - group.active;
  let step = wrapView(index - wrapView(base, count) + count / 2, count) - count / 2;
  // With two faces either path is equally short; honor the arrow direction.
  if (Math.abs(step) === count / 2 && requested > 0) step = Math.abs(step);
  animateViewPosition(group, Math.round(base + step), behavior);
}
function promoteView(group, index, focus = false) {
  clearTimeout(group.settleTimer);
  centerView(group, index);
  index = wrapView(index, group.views.length);
  markPrimary(group, index);
  if (focus) group.cards[index].focus({ preventScroll: true });
  selectProduct(group.views[index]).catch(handleViewerError);
}
function settleView(group) {
  const target = Math.round(group.position), index = wrapView(target, group.views.length);
  animateViewPosition(group, target);
  if (index !== group.active) {
    markPrimary(group, index);
    selectProduct(group.views[index]).catch(handleViewerError);
  }
}
function buildCaptureStack(group) {
  const section = document.createElement("section"),
    heading = document.createElement("h3"),
    choices = document.createElement("select"),
    viewport = document.createElement("div"),
    controls = document.createElement("div"),
    previous = document.createElement("button"),
    next = document.createElement("button"),
    label = document.createElement("span");
  section.className = "tap-stack";
  section.setAttribute("aria-label", group.name);
  heading.textContent = group.name;
  choices.className = "view-list";
  choices.size = Math.min(3, Math.max(2, group.views.length));
  choices.setAttribute("aria-label", `Views of ${group.name}`);
  choices.onchange = () => promoteView(group, Number(choices.value));
  viewport.className = "view-carousel";
  viewport.setAttribute("aria-label", `${group.name} views`);
  viewport.setAttribute("role", "group");
  controls.className = "view-controls";
  label.setAttribute("aria-live", "polite");
  previous.type = next.type = "button";
  previous.textContent = "↑";
  next.textContent = "↓";
  previous.setAttribute("aria-label", `Previous view of ${group.name}`);
  next.setAttribute("aria-label", `Next view of ${group.name}`);
  previous.onclick = () => promoteView(group, group.active - 1);
  next.onclick = () => promoteView(group, group.active + 1);
  Object.assign(group, { viewport, previous, next, label, cards: [], viewList: choices, position: group.active, target: group.active });
  group.views.forEach((product, index) => {
    const choice = document.createElement("option");
    choice.value = String(index);
    choice.textContent = product.view_name || "Default";
    choices.append(choice);
    const button = document.createElement("button"),
      canvas = document.createElement("canvas"),
      status = document.createElement("span"),
      title = document.createElement("strong"),
      details = document.createElement("span");
    button.className = "product";
    button.type = "button";
    button.dataset.productId = product.id;
    button.setAttribute("aria-label", `${group.name}: ${product.view_name || "Default"}`);
    button.setAttribute("aria-pressed", "false");
    canvas.setAttribute("aria-hidden", "true");
    status.className = "product-status";
    status.textContent = "Loading preview…";
    title.textContent = product.view_name || "Default view";
    details.textContent = `${product.shape.join(" × ")} · ${product.dtype}`;
    button.append(canvas, status, title, details);
    button.onclick = () => {
      if (group.suppressClick) { group.suppressClick = false; return; }
      if (status.classList.contains("error")) drawOverview(product, canvas).catch(() => {});
      promoteView(group, index);
    };
    viewport.append(button);
    group.cards.push(button);
  });
  viewport.addEventListener("keydown", (event) => {
    const index = { ArrowUp: group.active - 1, ArrowDown: group.active + 1, Home: 0, End: group.views.length - 1 }[event.key];
    if (index === undefined) return;
    event.preventDefault();
    promoteView(group, index, true);
  });
  viewport.addEventListener("wheel", event => {
    if (group.views.length < 2 || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    event.preventDefault();
    cancelAnimationFrame(group.animation);
    clearTimeout(group.settleTimer);
    const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 240 : 1);
    group.position += Math.max(-1, Math.min(1, pixels / 180));
    group.target = group.position;
    updateViewDepth(group);
    group.settleTimer = setTimeout(() => settleView(group), 110);
  }, { passive: false });
  viewport.addEventListener("pointerdown", event => {
    if (group.views.length < 2 || event.button !== 0) return;
    cancelAnimationFrame(group.animation);
    clearTimeout(group.settleTimer);
    group.drag = { id: event.pointerId, y: event.clientY, start: group.position };
    group.suppressClick = false;
  });
  viewport.addEventListener("pointermove", event => {
    if (!group.drag || group.drag.id !== event.pointerId) return;
    const delta = group.drag.y - event.clientY;
    if (Math.abs(delta) > 6) {
      group.suppressClick = true;
      viewport.setPointerCapture(event.pointerId);
    }
    if (!group.suppressClick) return;
    group.position = group.drag.start + delta / 180;
    group.target = group.position;
    updateViewDepth(group);
  });
  const release = event => {
    if (!group.drag || group.drag.id !== event.pointerId) return;
    group.drag = null;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    if (group.suppressClick) settleView(group);
  };
  viewport.addEventListener("pointerup", release);
  viewport.addEventListener("pointercancel", release);
  label.className = "view-announcement";
  controls.append(previous, choices, next, label);
  controls.hidden = group.views.length === 1;
  section.append(heading, viewport, controls);
  markPrimary(group, group.active);
  return section;
}
let overviewObserver;
const overviewQueue = [];
let overviewWorkers = 0;
function queueOverview(product, canvas) {
  if (canvas.dataset.queued) {
    canvas._previewVersion = (canvas._previewVersion || 0) + 1;
    canvas.dataset.redraw = "true";
    return;
  }
  canvas.dataset.queued = "true";
  overviewQueue.push({ product, canvas });
  drainOverviews();
}
function drainOverviews() {
  while (overviewWorkers < 2 && overviewQueue.length) {
    const { product, canvas } = overviewQueue.shift();
    if (!canvas.isConnected) continue;
    overviewWorkers++;
    drawOverview(product, canvas).catch(() => {}).finally(() => {
      delete canvas.dataset.queued;
      overviewWorkers--;
      if (canvas.dataset.redraw) {
        delete canvas.dataset.redraw;
        queueOverview(product, canvas);
      }
      drainOverviews();
    });
  }
}
function redrawOverviews() {
  overviewObserver?.disconnect();
  overviewObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const canvas = entry.target;
      overviewObserver.unobserve(canvas);
      const product = state.run.products.find(product => product.id === canvas.parentElement.dataset.productId);
      if (product) queueOverview(product, canvas);
    }
  }, { rootMargin: "200px" });
  document.querySelectorAll(".product canvas").forEach(canvas => {
    canvas._previewVersion = (canvas._previewVersion || 0) + 1;
    overviewObserver.observe(canvas);
  });
}

async function build() {
  const inspector = $("inspector"),
    previousInspectorHidden = inspector.hidden;
  inspector.hidden = true;
  clearVolumeCanvas();
  $("cell").textContent = "Click a cell";
  setVolumeStatus("Loading run…");
  try {
    state.run = await checkedJson(
      staticBase ? `${staticBase}/run.json` : "/api/run",
    );
    if (!state.run || !Array.isArray(state.run.products))
      throw new Error("Run manifest does not contain a product list");
    for (const cache of [state.volumes, state.rawVolumes, state.slices, state.bitmaps, state.coordinates]) cache.clear();
    $("run-name").textContent = state.run.name;
    $("run-meta").textContent =
      `${state.run.capture_count ?? state.run.products.length} captured products · ${state.run.products.length} plots · ${state.run.created_at}`;
    if (embeddedProduct) {
      const product = state.run.products.find((entry) => entry.id === embeddedProduct);
      if (!product) throw new Error("Comparison product is unavailable");
      await selectProduct(product);
      window.parent.postMessage({ channel: "spviz-comparison", type: "ready" }, location.origin);
      return;
    }
    const pipeline = $("pipeline");
    pipeline.replaceChildren();
    if (!state.run.products.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "This run does not contain any captured products yet.";
      pipeline.append(empty);
      $("inspector").hidden = true;
      return;
    }
    state.captureGroups = groupCaptures(state.run.products);
    state.captureGroups.forEach((group, index) => {
      if (index) {
        const product = group.views[0],
          upstream = product.upstream || [],
          connected = upstream.includes(state.captureGroups[index - 1].id),
          edge = document.createElement("div"),
          label = document.createElement("span");
        edge.className = connected ? "edge" : "edge disconnected";
        const names = upstream.map((id) => state.captureGroups.find((entry) => entry.id === id)?.name || id);
        label.textContent = connected
          ? product.operation || "transform"
          : names.length ? `${product.operation || "transform"} · from ${names.join(", ")}` : "independent capture";
        edge.setAttribute("role", "img");
        edge.setAttribute("aria-label", names.length ? `${group.name} receives ${names.join(", ")}` : `${group.name} has no recorded upstream product`);
        edge.append(label);
        pipeline.append(edge);
      }
      pipeline.append(buildCaptureStack(group));
    });
    for (const group of state.captureGroups) centerView(group, group.active, "instant");
    redrawOverviews();
    const initial = state.captureGroups[0];
    await selectProduct(initial.views[initial.active]);
  } catch (error) {
    inspector.hidden = previousInspectorHidden;
    throw error;
  }
}
async function selectProduct(product) {
  if (!product) throw new Error("No data product was selected");
  stopPlayback();
  clearTimeout(qualityTimer);
  $("inspector").hidden = false;
  clearVolumeCanvas();
  setVolumeStatus("Loading data…");
  $("cell").textContent = "Click a cell";
  state.product = product;
  state.viewAxes = (product.view_axes || product.axes.slice(0, 3)).map((name) =>
    product.axes.indexOf(name),
  );
  state.fixedIndices = new Map(
    product.axes
      .map((_, axis) => axis)
      .filter((axis) => !state.viewAxes.includes(axis))
      .map((axis) => [axis, 0]),
  );
  state.perm = [...state.viewAxes];
  state.layer = 0;
  state.pixelDensity = 96;
  state.scaleMin = 0;
  state.scaleMax = 1;
  state.logScale = product.scale === "log";
  state.viewMode = "surface";
  state.aspect = product.overview_aspect || state.overviewAspect;
  state.yaw = 0.3;
  state.pitch = 0;
  state.isolate = false;
  state.opacity = 0.25;
  state.currentOpacity = 1;
  state.opacityLinked = false;
  $("isolate").checked = false;
  $("opacity").value = 25;
  $("current-opacity").value = 100;
  $("link-opacity").textContent = "Link opacity: off";
  $("link-opacity").setAttribute("aria-pressed", "false");
  $("opacity-output").textContent = "25%";
  $("current-opacity-output").textContent = "100%";
  const representation = product.representation || "auto",
    binary = isBinaryProduct(product),
    cyclic = representation === "phase";
  if (binary || cyclic) state.logScale = false;
  $("minimum").value = 0;
  $("maximum").value = 100;
  $("minimum").disabled = binary;
  $("maximum").disabled = binary;
  $("log-scale").disabled = binary || cyclic;
  $("log-scale").checked = state.logScale;
  syncScaleLabels();
  updateScale();
  syncScaleLegend();
  const viewSelect = $("view-mode");
  viewSelect.value = "surface";
  viewSelect.querySelector('option[value="slices"]').disabled =
    state.viewAxes.length !== 2;
  $("aspect").value = state.aspect;
  document.querySelectorAll(".product").forEach((element) => {
    const selected = element.dataset.productId === product.id;
    element.classList.toggle("selected", selected);
    element.setAttribute("aria-pressed", String(selected));
  });
  $("inspect-title").textContent = product.name;
  $("inspect-description").textContent =
    product.metadata?.description || `${product.axes.join(" × ")} data product`;
  $("volume").setAttribute(
    "aria-label",
    `Interactive view of ${product.name}. Drag to adjust stack orientation; use arrow keys for precise movement and Home to reset.`,
  );
  $("shape").textContent =
    `${product.shape.join(" × ")} · ${product.axes.join(" × ")}`;
  $("payload").textContent = bytes(product.bytes);
  $("representation").textContent =
    representation === "auto"
      ? product.dtype.includes("complex")
        ? "magnitude (automatic)"
        : "real values (automatic)"
      : representation.replace("_", " ");
  const select = $("permutation");
  select.replaceChildren();
  for (const order of permutations(state.viewAxes.length)) {
    const axes = order.map((index) => state.viewAxes[index]),
      option = document.createElement("option");
    option.value = axes.join(",");
    option.textContent = axes.map((index) => product.axes[index]).join(" × ");
    select.append(option);
  }
  await Promise.allSettled(
    product.axes.map((_, axis) => getCoordinates(product, axis)),
  );
  if (product !== state.product) return;
  syncFixedDimensionControls();
  syncLayer();
  scheduleDraw();
}
function syncLayer(metadata = null) {
  const sliced2D = state.perm.length === 2 && state.viewMode === "slices",
    depth =
      state.perm.length === 3 || sliced2D
        ? state.product.shape[state.perm[0]]
        : 1,
    planeAxes = state.perm.slice(state.perm.length === 3 || sliced2D ? 1 : 0),
    nativeMaximum = Math.max(
      ...planeAxes.map((axis) => state.product.shape[axis]),
      1,
    ),
    exportedShape = metadata?.static_export?.exported_plane_shape,
    exportedMaximum = exportedShape
      ? sliced2D || state.perm.length === 1
        ? exportedShape.at(-1)
        : Math.max(...exportedShape)
      : nativeMaximum,
    visualMaximum = staticBase
      ? Math.min(nativeMaximum, exportedMaximum)
      : Math.min(nativeMaximum, 1024),
    isSingleTrace = state.perm.length === 1;
  $("layer").max = depth - 1;
  state.layer = Math.min(state.layer, depth - 1);
  $("layer").value = state.layer;
  $("layer-output").textContent =
    state.perm.length === 3 || sliced2D
      ? `${state.product.axes[state.perm[0]]} ${coordinateLabel(state.perm[0], state.layer)} · ${state.layer + 1}/${depth}`
      : "single plane";
  if (
    (state.perm.length === 3 || sliced2D) &&
    Number.isInteger(metadata?.resolved_layer) &&
    metadata.resolved_layer !== state.layer
  )
    $("layer-output").textContent +=
      ` · nearest exported ${coordinateLabel(state.perm[0], metadata.resolved_layer)}`;
  $("layer").setAttribute("aria-valuetext", $("layer-output").textContent);
  $("quality").max = visualMaximum;
  state.pixelDensity = Math.min(state.pixelDensity, visualMaximum);
  $("quality").value = state.pixelDensity;
  $("quality-output").textContent =
    visualMaximum < nativeMaximum
      ? `${state.pixelDensity} / ${visualMaximum} visual px · ${nativeMaximum} native`
      : `${state.pixelDensity} / ${nativeMaximum} px`;
  $("quality").setAttribute("aria-valuetext", $("quality-output").textContent);
  const playable = !isSingleTrace && depth > 1;
  if (!playable && state.playing) stopPlayback();
  $("play").disabled = !playable;
  $("save-animation").disabled = !playable;
  $("save-layer").textContent =
    isSingleTrace || sliced2D ? "Save plot PNG" : "Save layer PNG";
}
function syncScaleLabels() {
  if (!state.product) return;
  if (isBinaryProduct()) {
    $("minimum-output").textContent = "0";
    $("maximum-output").textContent = "1";
    return;
  }
  const [min, max] = displayBounds(),
    units = state.product.units ? ` ${state.product.units}` : "";
  if (state.logScale) {
    const factor =
      state.product.representation === "power" ||
      (state.product.units || "").toLowerCase().includes("power")
        ? 10
        : 20;
    $("minimum-output").textContent =
      `${(factor * Math.log10(min)).toFixed(1)} dB`;
    $("maximum-output").textContent =
      `${(factor * Math.log10(max)).toFixed(1)} dB`;
  } else {
    $("minimum-output").textContent = `${min.toPrecision(3)}${units}`;
    $("maximum-output").textContent = `${max.toPrecision(3)}${units}`;
  }
}
function scheduleDraw() {
  state.renderVersion++;
  if (state.frame) return;
  state.frame = requestAnimationFrame(() => {
    state.frame = 0;
    publishComparisonGeometry();
    drawVolume(state.renderVersion);
  });
}
function scheduleExpensiveDraw(message, delay = 90) {
  resetVolumeGeometry();
  setVolumeStatus(message);
  clearTimeout(qualityTimer);
  qualityTimer = setTimeout(scheduleDraw, delay);
}
async function drawVolume(version) {
  try {
    await renderVolume(version);
    if (version === state.renderVersion && $("volume")._geometry) {
      rememberRenderedView();
    }
  } catch (error) {
    if (version === state.renderVersion) {
      state.heldFrame = null;
      handleViewerError(error);
    }
  }
}
async function renderVolume(version) {
  const canvas = $("volume"),
    product = state.product;
  if (!product) return;
  resetVolumeGeometry();
  const perm = [...state.perm],
    selectedLayer = state.layer,
    sliced2D = perm.length === 2 && state.viewMode === "slices",
    depth = perm.length === 3 ? product.shape[perm[0]] : 1,
    count = Math.min(12, depth);
  setVolumeStatus("Loading data…");
  let contextLayers = [],
    contextSlices = [],
    selectedSlice;
  try {
    if (sliced2D) {
      const [exactTrace, plane] = await Promise.all([
        get2DTrace(product, perm, selectedLayer),
        state.isolate
          ? Promise.resolve(null)
          : getVolume(product, perm, contextQualityLimit(), 1),
      ]);
      selectedSlice = exactTrace;
      if (plane) {
        const shown = Math.min(12, plane.rows),
          positions = [
            ...new Set(
              Array.from({ length: shown }, (_, index) =>
                Math.round((index * (plane.rows - 1)) / Math.max(1, shown - 1)),
              ),
            ),
          ];
        contextLayers = positions.map(
          (position) => plane.row_indices?.[position] ?? position,
        );
        contextSlices = positions.map((position, index) => ({
          ...plane,
          depth: 1,
          layer: contextLayers[index],
          rows: 1,
          row_indices: [contextLayers[index]],
          values: plane.values.subarray(
            position * plane.columns,
            (position + 1) * plane.columns,
          ),
        }));
      }
    } else if (perm.length === 3 && !state.isolate) {
      const [exactSlice, volume] = await Promise.all([
          getSlice(product, perm, selectedLayer),
          getVolume(product, perm, contextQualityLimit(), count),
        ]),
        planeSize = volume.rows * volume.columns;
      selectedSlice = exactSlice;
      contextLayers = Array.from(
        { length: volume.depth },
        (_, position) => volume.depth_indices?.[position] ?? position,
      );
      contextSlices = contextLayers.map((layer, position) => ({
        ...volume,
        layer,
        values: volume.values.subarray(
          position * planeSize,
          (position + 1) * planeSize,
        ),
      }));
    } else {
      selectedSlice = await getSlice(product, perm, selectedLayer);
    }
  } catch (error) {
    if (version === state.renderVersion) handleViewerError(error);
    return;
  }
  if (version !== state.renderVersion || product !== state.product) return;
  setVolumeStatus("");
  syncLayer(selectedSlice);
  const displayedLayer =
    selectedSlice.resolved_layer ?? selectedSlice.layer ?? selectedLayer;
  if (perm.length === 1) {
    const { context, width, height } = canvasSize(canvas),
      [displayMin, displayMax] = displayBounds(product),
      x = 92,
      y = 42,
      planeWidth = Math.max(100, width - 135),
      planeHeight = Math.max(120, height - 125),
      light = state.theme === "light";
    drawTrace(
      context,
      selectedSlice,
      x,
      y,
      planeWidth,
      planeHeight,
      displayMin,
      displayMax,
      state.logScale,
      light,
    );
    drawSheetAxes(context, {
      x,
      y,
      width: planeWidth,
      height: planeHeight,
      xAxis: perm[0],
      yAxis: null,
      light,
    });
    $("peak").textContent = displayedPeak(product, selectedSlice);
    canvas._geometry = {
      product,
      originX: x,
      originY: y,
      planeWidth,
      planeHeight,
      rows: 1,
      columns: selectedSlice.columns,
      cellWidth: planeWidth / selectedSlice.columns,
      cellHeight: planeHeight,
      sourceRows: 1,
      sourceColumns: selectedSlice.source_plane_shape[1],
      rowIndices: [0],
      columnIndices: selectedSlice.column_indices,
      values: selectedSlice.values,
      xAxis: perm[0],
      yAxis: null,
      layerAxis: null,
    };
    return;
  }
  if (perm.length === 2 && state.viewMode === "slices") {
    const { context, width, height } = canvasSize(canvas),
      [displayMin, displayMax] = displayBounds(product),
      rowCount = product.shape[perm[0]],
      shown = state.isolate ? 1 : contextLayers.length,
      traceWidth = Math.max(120, width - 210),
      traceHeight = Math.max(100, height - 190),
      offsetX = Math.max(-7, Math.min(22, 11 - 24 * Math.sin(state.yaw - 0.3))),
      offsetY = Math.max(-24, Math.min(14, -8 + 30 * Math.sin(state.pitch))),
      stackDx = offsetX * (shown - 1),
      stackDy = offsetY * (shown - 1),
      originX =
        (width - traceWidth - Math.abs(stackDx)) / 2 - Math.min(0, stackDx),
      originY =
        (height - traceHeight - Math.abs(stackDy) - 50) / 2 -
        Math.min(0, stackDy),
      light = state.theme === "light";
    for (let i = contextLayers.length - 1; i >= 0; i--) {
      const row = contextLayers[i];
      if (row === displayedLayer) continue;
      drawTrace(
        context,
        contextSlices[i],
        originX + offsetX * i,
        originY + offsetY * i,
        traceWidth,
        traceHeight,
        displayMin,
        displayMax,
        state.logScale,
        light,
        state.opacity,
        false,
      );
    }
    const selectedPosition =
        state.isolate || rowCount <= 1
          ? 0
          : (displayedLayer / (rowCount - 1)) * (shown - 1),
      selectedX = originX + offsetX * selectedPosition,
      selectedY = originY + offsetY * selectedPosition;
    drawTrace(
      context,
      selectedSlice,
      selectedX,
      selectedY,
      traceWidth,
      traceHeight,
      displayMin,
      displayMax,
      state.logScale,
      light,
      state.currentOpacity,
    );
    drawSheetAxes(context, {
      x: selectedX,
      y: selectedY,
      width: traceWidth,
      height: traceHeight,
      xAxis: perm[1],
      yAxis: null,
      light,
    });
    if (!state.isolate)
      drawDepthAxis(
        context,
        perm[0],
        originX,
        originY,
        offsetX,
        offsetY,
        shown,
        selectedPosition,
        displayedLayer,
        light,
      );
    $("peak").textContent = displayedPeak(product, selectedSlice);
    canvas._geometry = {
      product,
      originX: selectedX,
      originY: selectedY,
      planeWidth: traceWidth,
      planeHeight: traceHeight,
      rows: 1,
      columns: selectedSlice.columns,
      cellWidth: traceWidth / selectedSlice.columns,
      cellHeight: traceHeight,
      sourceRows: 1,
      sourceColumns: product.shape[perm[1]],
      rowIndices: [displayedLayer],
      columnIndices: selectedSlice.column_indices,
      values: selectedSlice.values,
      xAxis: perm[1],
      yAxis: null,
      layerAxis: perm[0],
      layerIndex: displayedLayer,
      stackOriginX: state.isolate ? null : originX,
      stackOriginY: state.isolate ? null : originY,
      offsetX,
      offsetY,
      slots: state.isolate ? 1 : shown,
      selectedPosition,
    };
    return;
  }
  const { context, width, height } = canvasSize(canvas),
    rows = selectedSlice.rows,
    columns = selectedSlice.columns,
    sourceRows = selectedSlice.source_plane_shape[0],
    sourceColumns = selectedSlice.source_plane_shape[1],
    slots = state.isolate || perm.length < 3 ? 1 : contextSlices.length,
    offsetX = Math.max(-14, Math.min(32, 16 - 36 * Math.sin(state.yaw - 0.3))),
    offsetY = Math.max(-32, Math.min(18, -11 + 40 * Math.sin(state.pitch))),
    availableWidth = Math.max(
      80,
      width - 190 - Math.abs(offsetX) * (slots - 1),
    ),
    availableHeight = Math.max(
      80,
      height - 120 - Math.abs(offsetY) * (slots - 1),
    ),
    sourceAspect = state.aspect === "equal" ? 1 : sourceColumns / sourceRows,
    planeWidth =
      state.aspect === "fit"
        ? availableWidth
        : availableWidth / availableHeight > sourceAspect
          ? availableHeight * sourceAspect
          : availableWidth,
    planeHeight =
      state.aspect === "fit" ? availableHeight : planeWidth / sourceAspect,
    cellWidth = planeWidth / columns,
    cellHeight = planeHeight / rows,
    stackWidth = planeWidth + Math.abs(offsetX) * (slots - 1),
    stackDy = offsetY * (slots - 1),
    stackHeight = planeHeight + Math.abs(stackDy),
    originX =
      (width - stackWidth) / 2 +
      35 +
      (offsetX < 0 ? Math.abs(offsetX) * (slots - 1) : 0),
    originY = (height - stackHeight - 50) / 2 - Math.min(0, stackDy),
    [displayMin, displayMax] = displayBounds(product),
    bitmapScope = `detail:${state.scaleMin}:${state.scaleMax}:${state.logScale}`,
    ink = themeInk(),
    useLayerBlur =
      planeWidth * planeHeight * (canvas._renderRatio || 1) ** 2 < 650_000;
  context.imageSmoothingEnabled = false;
  function drawContextLayer(i, inFront) {
    const layer = contextLayers[i];
    if (layer === displayedLayer) return;
    const distance = Math.abs(layer - displayedLayer) / Math.max(1, depth - 1),
      image = bitmapFor(
        contextSlices[i],
        displayMin,
        displayMax,
        state.logScale,
        bitmapScope,
      );
    context.save();
    context.globalAlpha = state.opacity;
    if (useLayerBlur)
      context.filter = `blur(${(0.45 + distance * 1.25 + (inFront ? 0.25 : 0)).toFixed(2)}px)`;
    context.drawImage(
      image,
      originX + offsetX * i,
      originY + offsetY * i,
      planeWidth,
      planeHeight,
    );
    context.restore();
    context.save();
    context.globalAlpha = state.opacity;
    context.strokeStyle = ink.line;
    context.strokeRect(
      originX + offsetX * i,
      originY + offsetY * i,
      planeWidth,
      planeHeight,
    );
    context.restore();
  }
  if (!state.isolate) {
    for (let i = contextSlices.length - 1; i >= 0; i--)
      if (contextLayers[i] > displayedLayer) drawContextLayer(i, false);
  }
  const selectedPosition = state.isolate
      ? 0
      : depth <= 1
        ? 0
        : (displayedLayer / (depth - 1)) * (slots - 1),
    selectedX = originX + offsetX * selectedPosition,
    selectedY = originY + offsetY * selectedPosition,
    selectedBitmap = bitmapFor(
      selectedSlice,
      displayMin,
      displayMax,
      state.logScale,
      bitmapScope,
    );
  context.save();
  context.globalAlpha = state.currentOpacity;
  context.drawImage(
    selectedBitmap,
    selectedX,
    selectedY,
    planeWidth,
    planeHeight,
  );
  context.restore();
  if (!state.isolate) {
    context.strokeStyle = ink.strong;
    context.lineWidth = 2.5;
    context.strokeRect(selectedX, selectedY, planeWidth, planeHeight);
  }
  if (!state.isolate) {
    for (let i = contextSlices.length - 1; i >= 0; i--)
      if (contextLayers[i] < displayedLayer) drawContextLayer(i, true);
  }
  const xAxis = perm[perm.length - 1],
    yAxis = perm.length > 1 ? perm[perm.length - 2] : null,
    light = state.theme === "light";
  drawSheetAxes(context, {
    x: selectedX,
    y: selectedY,
    width: planeWidth,
    height: planeHeight,
    xAxis,
    yAxis,
    light,
  });
  if (perm.length === 3 && !state.isolate)
    drawDepthAxis(
      context,
      perm[0],
      originX,
      originY,
      offsetX,
      offsetY,
      slots,
      selectedPosition,
      displayedLayer,
      light,
    );
  $("peak").textContent = displayedPeak(product, selectedSlice);
  canvas._geometry = {
    product,
    originX: selectedX,
    originY: selectedY,
    planeWidth,
    planeHeight,
    rows,
    columns,
    cellWidth,
    cellHeight,
    sourceRows: selectedSlice.source_plane_shape[0],
    sourceColumns: selectedSlice.source_plane_shape[1],
    rowIndices: selectedSlice.row_indices,
    columnIndices: selectedSlice.column_indices,
    values: selectedSlice.values,
    xAxis,
    yAxis,
    layerAxis: perm.length === 3 ? perm[0] : null,
    layerIndex: displayedLayer,
    stackOriginX: originX,
    stackOriginY: originY,
    offsetX,
    offsetY,
    slots,
    selectedPosition,
  };
}
$("permutation").onchange = (event) => {
  stopPlayback();
  clearTimeout(qualityTimer);
  state.perm = event.target.value.split(",").map(Number);
  state.layer = 0;
  syncLayer();
  scheduleDraw();
};
$("layer").oninput = (event) => {
  state.layer = +event.target.value;
  syncLayer();
  resetVolumeGeometry();
  setVolumeStatus("Loading layer…");
  clearTimeout(qualityTimer);
  qualityTimer = setTimeout(scheduleDraw, 45);
};
$("isolate").onchange = (event) => {
  state.isolate = event.target.checked;
  $("opacity").disabled = state.isolate;
  scheduleDraw();
};
$("opacity").oninput = (event) => {
  state.opacity = +event.target.value / 100;
  $("opacity-output").textContent = `${event.target.value}%`;
  if (state.opacityLinked) {
    state.currentOpacity = state.opacity;
    $("current-opacity").value = event.target.value;
    $("current-opacity-output").textContent = `${event.target.value}%`;
  }
  scheduleDraw();
};
$("minimum").oninput = (event) => {
  let value = +event.target.value,
    maximum = +$("maximum").value;
  if (value >= maximum) {
    value = maximum - 1;
    event.target.value = value;
  }
  state.scaleMin = value / 100;
  syncScaleLabels();
  updateScale();
  syncScaleLegend();
  scheduleExpensiveDraw("Updating colors…");
};
$("maximum").oninput = (event) => {
  let value = +event.target.value,
    minimum = +$("minimum").value;
  if (value <= minimum) {
    value = minimum + 1;
    event.target.value = value;
  }
  state.scaleMax = value / 100;
  syncScaleLabels();
  updateScale();
  syncScaleLegend();
  scheduleExpensiveDraw("Updating colors…");
};
$("log-scale").onchange = (event) => {
  state.logScale = event.target.checked;
  syncScaleLabels();
  updateScale();
  syncScaleLegend();
  scheduleDraw();
};
$("current-opacity").oninput = (event) => {
  state.currentOpacity = +event.target.value / 100;
  $("current-opacity-output").textContent = `${event.target.value}%`;
  if (state.opacityLinked) {
    state.opacity = state.currentOpacity;
    $("opacity").value = event.target.value;
    $("opacity-output").textContent = `${event.target.value}%`;
  }
  scheduleDraw();
};
$("link-opacity").onclick = (event) => {
  state.opacityLinked = !state.opacityLinked;
  event.target.setAttribute("aria-pressed", String(state.opacityLinked));
  event.target.textContent = `Link opacity: ${state.opacityLinked ? "on" : "off"}`;
  if (state.opacityLinked) {
    state.opacity = state.currentOpacity;
    $("opacity").value = Math.round(state.opacity * 100);
    $("opacity-output").textContent = `${Math.round(state.opacity * 100)}%`;
  }
  scheduleDraw();
};
$("quality").oninput = (event) => {
  state.pixelDensity = +event.target.value;
  $("quality-output").textContent =
    `${state.pixelDensity} / ${event.target.max} px`;
  scheduleExpensiveDraw("Updating detail…", 110);
};
$("aspect").onchange = (event) => {
  state.aspect = event.target.value;
  scheduleDraw();
};
$("view-mode").onchange = (event) => {
  stopPlayback();
  clearTimeout(qualityTimer);
  state.viewMode = event.target.value;
  state.layer = 0;
  syncLayer();
  scheduleDraw();
};
$("overview-aspect").onchange = (event) => {
  state.overviewAspect = event.target.value;
  redrawOverviews();
};
function startPlayback() {
  clearInterval(state.timer);
  state.playing = true;
  $("play").textContent = "Pause layers";
  $("play").setAttribute("aria-pressed", "true");
  state.timer = setInterval(() => {
    if (!state.product) return stopPlayback();
    const sliced2D = state.perm.length === 2 && state.viewMode === "slices",
      depth =
        state.perm.length === 3 || sliced2D
          ? state.product.shape[state.perm[0]]
          : 1;
    if (depth <= 1) return stopPlayback();
    state.layer = (state.layer + 1) % depth;
    syncLayer();
    scheduleDraw();
  }, 450);
}
$("play").onclick = () => {
  if (state.playing) stopPlayback();
  else startPlayback();
  scheduleDraw();
};

function redrawAppearance() {
  document.documentElement.dataset.theme = state.theme;
  updateScale();
  redrawOverviews();
  scheduleDraw();
}
$("theme").onchange = (event) => {
  state.theme = event.target.value;
  redrawAppearance();
};
$("colormap").onchange = (event) => {
  state.colorMap = event.target.value;
  redrawAppearance();
};
function resetOrientation() {
  state.yaw = 0.3;
  state.pitch = 0;
  scheduleDraw();
}
function sampledSourceIndex(indices, position, count, sourceCount) {
  if (Array.isArray(indices) && Number.isFinite(indices[position]))
    return indices[position];
  return count <= 1
    ? 0
    : Math.round((position * (sourceCount - 1)) / (count - 1));
}
function inspectCanvasCell(event) {
  if (suppressCanvasClick) {
    suppressCanvasClick = false;
    return;
  }
  const geometry = event.currentTarget._geometry;
  if (!geometry?.values || geometry.product !== state.product) return;
  const rect = event.currentTarget.getBoundingClientRect(),
    dx = event.clientX - rect.left - geometry.originX,
    dy = event.clientY - rect.top - geometry.originY;
  let column, row;
  if (geometry.u) {
    const determinant =
      geometry.u.x * geometry.v.y - geometry.u.y * geometry.v.x;
    if (Math.abs(determinant) < 1e-6) return;
    const across = (dx * geometry.v.y - dy * geometry.v.x) / determinant,
      down = (geometry.u.x * dy - geometry.u.y * dx) / determinant;
    column = Math.floor(across * geometry.columns);
    row = Math.floor(down * geometry.rows);
  } else {
    column = Math.floor(dx / geometry.cellWidth);
    row = Math.floor(dy / geometry.cellHeight);
  }
  if (
    column < 0 ||
    column >= geometry.columns ||
    row < 0 ||
    row >= geometry.rows
  )
    return;
  const sourceX = sampledSourceIndex(
      geometry.columnIndices,
      column,
      geometry.columns,
      geometry.sourceColumns,
    ),
    sourceY = sampledSourceIndex(
      geometry.rowIndices,
      row,
      geometry.rows,
      geometry.sourceRows,
    ),
    parts = [
      `${state.product.axes[geometry.xAxis]} ${coordinateLabel(geometry.xAxis, sourceX)}`,
    ];
  if (geometry.yAxis !== null)
    parts.push(
      `${state.product.axes[geometry.yAxis]} ${coordinateLabel(geometry.yAxis, sourceY)}`,
    );
  if (geometry.layerAxis !== null)
    parts.push(
      `${state.product.axes[geometry.layerAxis]} ${coordinateLabel(geometry.layerAxis, geometry.layerIndex ?? state.layer)}`,
    );
  const value = Number(geometry.values[row * geometry.columns + column]),
    formatted = isBinaryProduct()
      ? value
        ? "1 (active)"
        : "0 (background)"
      : Number.isFinite(value)
        ? value.toPrecision(6)
        : String(value);
  $("cell").textContent = `${parts.join(" · ")} = ${formatted}`;
}
let dragging = false,
  lastX = 0,
  lastY = 0,
  dragDistance = 0,
  suppressCanvasClick = false;
$("volume").onpointerdown = (event) => {
  if (event.button !== 0) return;
  dragging = true;
  dragDistance = 0;
  suppressCanvasClick = false;
  lastX = event.clientX;
  lastY = event.clientY;
  $("volume").setPointerCapture(event.pointerId);
};
$("volume").onpointermove = (event) => {
  if (dragging) {
    const deltaX = event.clientX - lastX,
      deltaY = event.clientY - lastY;
    dragDistance += Math.hypot(deltaX, deltaY);
    if (dragDistance > 4) suppressCanvasClick = true;
    state.yaw = Math.max(-1.25, Math.min(1.25, state.yaw + deltaX * 0.006));
    state.pitch = Math.max(-0.8, Math.min(0.8, state.pitch - deltaY * 0.006));
    lastX = event.clientX;
    lastY = event.clientY;
    scheduleDraw();
  }
};
$("volume").onpointerup = () => {
  dragging = false;
  if (suppressCanvasClick)
    setTimeout(() => {
      suppressCanvasClick = false;
    }, 0);
};
$("volume").onpointercancel = () => {
  dragging = false;
  suppressCanvasClick = false;
};
$("volume").ondblclick = (event) => {
  event.preventDefault();
  resetOrientation();
};
$("volume").onclick = inspectCanvasCell;
$("volume").onkeydown = (event) => {
  const step = event.shiftKey ? 0.16 : 0.06;
  if (event.key === "Home") resetOrientation();
  else if (event.key === "ArrowLeft")
    state.yaw = Math.max(-1.25, state.yaw - step);
  else if (event.key === "ArrowRight")
    state.yaw = Math.min(1.25, state.yaw + step);
  else if (event.key === "ArrowUp")
    state.pitch = Math.min(0.8, state.pitch + step);
  else if (event.key === "ArrowDown")
    state.pitch = Math.max(-0.8, state.pitch - step);
  else return;
  event.preventDefault();
  if (event.key !== "Home") scheduleDraw();
};
$("reset-view").onclick = resetOrientation;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPlayback();
});
function exportName(suffix, extension) {
  const base = (
    state.product?.file
      ?.split("/")
      .pop()
      .replace(/\.npy$/, "") || state.run.name
  )
    .replace(/[^a-z0-9_-]+/gi, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
  return `${base}-${suffix}.${extension}`;
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob),
    anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function saveCanvas(canvas, filename) {
  canvas.toBlob((blob) => {
    if (blob) downloadBlob(blob, filename);
  }, "image/png");
}
async function saveSelectedLayer() {
  const product = state.product,
    sliced2D = state.perm.length === 2 && state.viewMode === "slices",
    slice = await getDisplayedSlice(product, state.perm, state.layer),
    [min, max] = displayBounds(product),
    canvas = document.createElement("canvas");
  canvas.width = 1200;
  canvas.height = 820;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#111827";
  context.font = "600 24px system-ui";
  context.fillText(
    `${product.name} · ${state.perm.length === 3 || sliced2D ? coordinateLabel(state.perm[0], state.layer) : "selected view"}`,
    110,
    52,
  );
  const width = 850,
    height = 600,
    x = 190,
    y = 90;
  context.imageSmoothingEnabled = false;
  if (state.perm.length === 1 || sliced2D) {
    drawTrace(
      context,
      slice,
      x,
      y,
      width,
      height,
      min,
      max,
      state.logScale,
      true,
    );
  } else {
    const image = bitmapFor(
      slice,
      min,
      max,
      state.logScale,
      `export:${state.scaleMin}:${state.scaleMax}:${state.logScale}`,
    );
    context.save();
    context.globalAlpha = state.currentOpacity;
    context.drawImage(image, x, y, width, height);
    context.restore();
    context.strokeStyle = "#111827";
    context.lineWidth = 2;
    context.strokeRect(x, y, width, height);
  }
  drawSheetAxes(context, {
    x,
    y,
    width,
    height,
    xAxis: state.perm[state.perm.length - 1],
    yAxis:
      state.perm.length > 1 && !sliced2D
        ? state.perm[state.perm.length - 2]
        : null,
    light: true,
  });
  saveCanvas(canvas, exportName(`layer-${state.layer}`, "png"));
}
function saveStack() {
  const source = $("volume"),
    rect = source.getBoundingClientRect(),
    canvas = document.createElement("canvas");
  canvas.width = Math.round(rect.width);
  canvas.height = Math.round(rect.height);
  const context = canvas.getContext("2d"),
    geometry = source._geometry;
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  if (geometry) {
    if (geometry.u)
      drawProjectedAxes(
        context,
        {
          x: geometry.originX,
          y: geometry.originY,
          u: geometry.u,
          v: geometry.v,
          xAxis: geometry.xAxis,
          yAxis: geometry.yAxis,
        },
        true,
      );
    else
      drawSheetAxes(context, {
        x: geometry.originX,
        y: geometry.originY,
        width: geometry.planeWidth,
        height: geometry.planeHeight,
        xAxis: geometry.xAxis,
        yAxis: geometry.yAxis,
        light: true,
      });
    if (geometry.layerAxis !== null && !state.isolate)
      drawDepthAxis(
        context,
        geometry.layerAxis,
        geometry.stackOriginX,
        geometry.stackOriginY,
        geometry.offsetX,
        geometry.offsetY,
        geometry.slots,
        geometry.selectedPosition,
        state.layer,
        true,
      );
  }
  saveCanvas(canvas, exportName("stack", "png"));
}
function comparisonGeometry() {
  return {
    shape: [...state.product.shape], viewAxes: [...state.viewAxes],
    perm: [...state.perm], layer: state.layer, yaw: state.yaw, pitch: state.pitch,
    isolate: state.isolate, viewMode: state.viewMode, aspect: state.aspect,
    fixedIndices: [...state.fixedIndices],
  };
}
function compatibleGeometry(a, b) {
  return a && b && JSON.stringify(a.shape) === JSON.stringify(b.shape)
    && JSON.stringify([...a.viewAxes].sort()) === JSON.stringify([...b.viewAxes].sort());
}
function comparisonSettings() {
  return {
    ...comparisonGeometry(), scaleMin: state.scaleMin, scaleMax: state.scaleMax,
    logScale: state.logScale, opacity: state.opacity, currentOpacity: state.currentOpacity,
    pixelDensity: state.pixelDensity, theme: state.theme, colorMap: state.colorMap,
    opacityLinked: state.opacityLinked, playing: state.playing,
  };
}
function applyComparisonSettings(settings) {
  if (!state.product || !compatibleGeometry(settings, comparisonGeometry())) return;
  const perm = settings.perm;
  if (!Array.isArray(perm) || perm.length !== state.viewAxes.length
      || new Set(perm).size !== perm.length || perm.some(axis => !state.viewAxes.includes(axis))) return;
  if (!Number.isFinite(settings.yaw) || !Number.isFinite(settings.pitch)
      || !Number.isInteger(settings.layer) || !["surface", "slices"].includes(settings.viewMode)
      || !["data", "equal", "fit"].includes(settings.aspect) || typeof settings.isolate !== "boolean") return;
  stopPlayback();
  state.perm = [...perm];
  state.yaw = Math.max(-1.25, Math.min(1.25, settings.yaw));
  state.pitch = Math.max(-0.8, Math.min(0.8, settings.pitch));
  state.viewMode = perm.length === 2 ? settings.viewMode : "surface";
  state.aspect = settings.aspect;
  state.isolate = settings.isolate;
  const depth = perm.length === 3 || state.viewMode === "slices" ? state.product.shape[perm[0]] : 1;
  state.layer = Math.max(0, Math.min(depth - 1, settings.layer));
  for (const [axis, index] of settings.fixedIndices || []) {
    if (state.fixedIndices.has(axis) && Number.isInteger(index) && index >= 0 && index < state.product.shape[axis])
      state.fixedIndices.set(axis, index);
  }
  $("permutation").value = perm.join(",");
  $("view-mode").value = state.viewMode;
  $("aspect").value = state.aspect;
  $("isolate").checked = state.isolate;
  $("opacity").disabled = state.isolate;
  {
    for (const key of ["scaleMin", "scaleMax", "opacity", "currentOpacity"])
      if (Number.isFinite(settings[key])) state[key] = Math.max(0, Math.min(1, settings[key]));
    state.logScale = Boolean(settings.logScale) && !isBinaryProduct() && state.product.representation !== "phase";
    if (Number.isFinite(settings.pixelDensity)) state.pixelDensity = Math.max(1, settings.pixelDensity);
    if (["dark", "light"].includes(settings.theme)) state.theme = settings.theme;
    if (settings.colorMap === "auto" || Object.hasOwn(colorMaps, settings.colorMap)) state.colorMap = settings.colorMap;
    document.documentElement.dataset.theme = state.theme;
    $("theme").value = state.theme;
    $("colormap").value = state.colorMap;
    $("minimum").value = state.scaleMin * 100;
    $("maximum").value = state.scaleMax * 100;
    $("log-scale").checked = state.logScale;
    $("opacity").value = Math.round(state.opacity * 100);
    $("opacity-output").textContent = `${Math.round(state.opacity * 100)}%`;
    $("current-opacity").value = Math.round(state.currentOpacity * 100);
    $("current-opacity-output").textContent = `${Math.round(state.currentOpacity * 100)}%`;
    state.opacityLinked = Boolean(settings.opacityLinked);
    $("link-opacity").setAttribute("aria-pressed", String(state.opacityLinked));
    $("link-opacity").textContent = `Link opacity: ${state.opacityLinked ? "on" : "off"}`;
    state.playing = Boolean(settings.playing);
    $("play").textContent = state.playing ? "Pause layers" : "Play layers";
    $("play").setAttribute("aria-pressed", String(state.playing));
    syncScaleLabels(); updateScale(); syncScaleLegend();
  }
  syncFixedDimensionControls();
  syncLayer();
  // A linked update must not echo back and restart its source's playback.
  lastComparisonGeometry = JSON.stringify(comparisonSettings());
  scheduleDraw();
}
function publishComparisonGeometry() {
  if (!embeddedProduct || !comparisonInitialized || !state.product) return;
  const geometry = comparisonSettings(), serialized = JSON.stringify(geometry);
  if (serialized === lastComparisonGeometry) return;
  // A user interacting with a playing follower takes over as the clock source.
  if (state.playing && !state.timer) startPlayback();
  lastComparisonGeometry = serialized;
  window.parent.postMessage({ channel: "spviz-comparison", type: "geometry", geometry }, location.origin);
}
function rememberRenderedView() {
  state.heldFrame = { product: state.product, settings: comparisonSettings() };
  $("hold-comparison").disabled = false;
}
function syncComparison() {
  $("comparison").hidden = state.heldViews.size === 0;
  $("comparison-count").textContent = `(${state.heldViews.size})`;
  const linking = $("link-comparison").checked;
  for (const panel of state.heldViews.values()) {
    const matches = [...state.heldViews.values()].filter(other => other !== panel && compatibleGeometry(other.settings, panel.settings));
    panel.status.textContent = linking && matches.length ? `Linked with ${matches.length} compatible viewer${matches.length === 1 ? "" : "s"}` : "Independent viewer";
  }
}
function removeHeldView(id) {
  const panel = state.heldViews.get(id);
  if (!panel) return;
  panel.iframe.src = "about:blank";
  panel.card.remove();
  state.heldViews.delete(id);
  syncComparison();
}
function clearComparison() {
  for (const id of state.heldViews.keys()) removeHeldView(id);
  $("comparison-status").textContent = "Comparison cleared.";
}
function sendComparison(panel, type, settings) {
  panel.iframe.contentWindow.postMessage({ channel: "spviz-comparison", type, settings }, location.origin);
}
function relayComparison(source) {
  if (!$("link-comparison").checked) return;
  for (const panel of state.heldViews.values()) {
    if (panel !== source && compatibleGeometry(source.settings, panel.settings)) {
      const geometry = structuredClone(source.settings);
      Object.assign(panel.settings, geometry);
      if (panel.ready) sendComparison(panel, "geometry", geometry);
    }
  }
}
function holdForComparison() {
  if (!state.heldFrame || embeddedProduct) return;
  const { product, settings } = state.heldFrame,
    id = ++state.nextHeldView,
    card = document.createElement("article"),
    header = document.createElement("header"),
    title = document.createElement("h3"),
    remove = document.createElement("button"),
    status = document.createElement("p"),
    iframe = document.createElement("iframe"),
    url = new URL(location.href);
  url.searchParams.set("viewer", product.id);
  url.hash = "";
  iframe.title = `Interactive comparison ${id}: ${product.name}`;
  iframe.src = url.href;
  card.className = "comparison-card";
  card.setAttribute("role", "listitem");
  title.textContent = `${id}. ${product.name}`;
  remove.type = "button";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", `Remove viewer ${id}: ${product.name}`);
  remove.onclick = () => removeHeldView(id);
  header.append(title, remove);
  card.append(header, status, iframe);
  const panel = { card, iframe, status, settings: structuredClone(settings), ready: false };
  panel.settings.playing = false;
  const match = [...state.heldViews.values()].find(other => compatibleGeometry(other.settings, settings));
  state.heldViews.set(id, panel);
  if (match) relayComparison(match);
  $("comparison-views").append(card);
  syncComparison();
  $("comparison-status").textContent = `Added ${product.name}. ${state.heldViews.size} interactive viewers.`;
}
window.addEventListener("message", event => {
  const data = event.data;
  if (event.origin !== location.origin || data?.channel !== "spviz-comparison") return;
  if (embeddedProduct) {
    if (event.source !== window.parent || !state.product) return;
    if (data.type === "initialize") {
      applyComparisonSettings(data.settings, true);
      comparisonInitialized = true;
    } else if (data.type === "geometry" && comparisonInitialized) applyComparisonSettings(data.settings);
    else if (data.type === "unlink") {
      stopPlayback();
      scheduleDraw();
    }
    return;
  }
  const panel = [...state.heldViews.values()].find(entry => entry.iframe.contentWindow === event.source);
  if (!panel) return;
  if (data.type === "ready") {
    panel.ready = true;
    sendComparison(panel, "initialize", panel.settings);
  } else if (data.type === "geometry" && compatibleGeometry(data.geometry, panel.settings)) {
    Object.assign(panel.settings, data.geometry);
    relayComparison(panel);
  }
});
$("hold-comparison").onclick = holdForComparison;
$("clear-comparison").onclick = clearComparison;
$("link-comparison").onchange = () => {
  if (!$("link-comparison").checked) {
    for (const panel of state.heldViews.values()) {
      panel.settings.playing = false;
      if (panel.ready) sendComparison(panel, "unlink", {});
    }
  }
  const synchronized = [];
  for (const panel of state.heldViews.values()) {
    if (!synchronized.some(previous => compatibleGeometry(previous.settings, panel.settings))) {
      relayComparison(panel);
      synchronized.push(panel);
    }
  }
  syncComparison();
};

function saveFullChain() {
  const groups = state.captureGroups,
    above = Math.max(0, ...groups.map((group) => group.active)),
    below = Math.max(0, ...groups.map((group) => group.views.length - group.active - 1)),
    width = Math.max(300, groups.length * 330),
    height = 80 + (above + below + 1) * 280,
    canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  context.fillStyle = "#111827";
  context.font = "600 22px system-ui";
  context.fillText(state.run.name, 24, 34);
  groups.forEach((group, column) => {
    const x = 20 + column * 330;
    group.views.forEach((product, index) => {
      const y = 55 + (above + index - group.active) * 280;
      context.globalAlpha = index === group.active ? 1 : 0.65;
      context.drawImage(group.cards[index].querySelector("canvas"), x, y, 220, 200);
      context.fillStyle = "#111827";
      context.font = "600 14px system-ui";
      context.textAlign = "center";
      context.fillText(product.name, x + 110, y + 225, 250);
      context.fillStyle = "#4b5568";
      context.font = "12px ui-monospace,monospace";
      context.fillText(`${product.shape.join(" × ")}${index === group.active ? " · primary" : ""}`, x + 110, y + 246);
    });
    context.globalAlpha = 1;
    if (groups[column + 1]?.views[0].upstream?.includes(group.id)) {
      const y = 155 + above * 280;
      context.strokeStyle = "#4b5568";
      context.beginPath();
      context.moveTo(x + 230, y);
      context.lineTo(x + 315, y);
      context.stroke();
      context.fillStyle = "#4b5568";
      context.beginPath();
      context.moveTo(x + 315, y);
      context.lineTo(x + 305, y - 6);
      context.lineTo(x + 305, y + 6);
      context.fill();
    }
  });
  saveCanvas(canvas, `${state.run.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}-chain.png`);
}
async function saveAnimation() {
  const button = $("save-animation"),
    product = state.product,
    perm = [...state.perm],
    depth = perm.length === 3 ? product.shape[perm[0]] : 1,
    frameCount = Math.min(depth, 80),
    frameLayers = Array.from({ length: frameCount }, (_, i) =>
      Math.round((i * (depth - 1)) / Math.max(1, frameCount - 1)),
    ),
    width = 800,
    height = 620,
    frameCanvas = document.createElement("canvas");
  frameCanvas.width = width;
  frameCanvas.height = height;
  const context = frameCanvas.getContext("2d"),
    encoder = new SpvizGifEncoder(width, height, 10),
    [min, max] = displayBounds(product),
    x = 130,
    y = 80,
    planeWidth = 610,
    planeHeight = 440,
    xAxis = perm[perm.length - 1],
    yAxis = perm.length > 1 ? perm[perm.length - 2] : null;
  button.disabled = true;
  try {
    for (let frame = 0; frame < frameLayers.length; frame++) {
      const layer = frameLayers[frame],
        slice = await getSlice(product, perm, layer),
        image = bitmapFor(
          slice,
          min,
          max,
          state.logScale,
          `gif:${state.scaleMin}:${state.scaleMax}:${state.logScale}`,
        );
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);
      context.fillStyle = "#111827";
      context.font = "600 20px system-ui";
      context.textAlign = "left";
      context.fillText(product.name, 40, 35);
      context.fillStyle = "#4b5568";
      context.font = "13px system-ui";
      context.fillText(
        perm.length === 3
          ? `${axisTitle(perm[0])}: ${coordinateLabel(perm[0], layer)} · frame ${frame + 1}/${frameLayers.length}`
          : "single plane",
        40,
        57,
      );
      context.imageSmoothingEnabled = false;
      context.save();
      context.globalAlpha = state.currentOpacity;
      context.drawImage(image, x, y, planeWidth, planeHeight);
      context.restore();
      context.strokeStyle = "#111827";
      context.lineWidth = 2;
      context.strokeRect(x, y, planeWidth, planeHeight);
      drawSheetAxes(context, {
        x,
        y,
        width: planeWidth,
        height: planeHeight,
        xAxis,
        yAxis,
        light: true,
      });
      encoder.addFrame(context.getImageData(0, 0, width, height).data);
      button.textContent = `Encoding GIF… ${frame + 1}/${frameLayers.length}`;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    downloadBlob(encoder.finish(), exportName("layer-sweep", "gif"));
  } finally {
    button.disabled = false;
    button.textContent = "Save animation GIF";
  }
}
async function saveSliceAnimation() {
  const button = $("save-animation"),
    product = state.product,
    depth = product.shape[state.perm[0]],
    frameCount = Math.min(depth, 80),
    layers = Array.from({ length: frameCount }, (_, i) =>
      Math.round((i * (depth - 1)) / Math.max(1, frameCount - 1)),
    ),
    width = 800,
    height = 620,
    frameCanvas = document.createElement("canvas");
  frameCanvas.width = width;
  frameCanvas.height = height;
  const context = frameCanvas.getContext("2d"),
    encoder = new SpvizGifEncoder(width, height, 10),
    [min, max] = displayBounds(product),
    x = 130,
    y = 80,
    traceWidth = 610,
    traceHeight = 440;
  button.disabled = true;
  try {
    for (let frame = 0; frame < layers.length; frame++) {
      const layer = layers[frame],
        slice = await getDisplayedSlice(product, state.perm, layer);
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);
      context.fillStyle = "#111827";
      context.font = "600 20px system-ui";
      context.fillText(product.name, 40, 35);
      context.fillStyle = "#4b5568";
      context.font = "13px system-ui";
      context.fillText(
        `${axisTitle(state.perm[0])}: ${coordinateLabel(state.perm[0], layer)} · frame ${frame + 1}/${layers.length}`,
        40,
        57,
      );
      drawTrace(
        context,
        slice,
        x,
        y,
        traceWidth,
        traceHeight,
        min,
        max,
        state.logScale,
        true,
      );
      drawSheetAxes(context, {
        x,
        y,
        width: traceWidth,
        height: traceHeight,
        xAxis: state.perm[1],
        yAxis: null,
        light: true,
      });
      encoder.addFrame(context.getImageData(0, 0, width, height).data);
      button.textContent = `Encoding GIF… ${frame + 1}/${layers.length}`;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    downloadBlob(encoder.finish(), exportName("slice-sweep", "gif"));
  } finally {
    button.disabled = false;
    button.textContent = "Save animation GIF";
  }
}
$("save-layer").onclick = () => saveSelectedLayer().catch(handleViewerError);
$("save-stack").onclick = saveStack;
$("save-chain").onclick = saveFullChain;
$("save-animation").onclick = () =>
  (state.perm.length === 2 && state.viewMode === "slices"
    ? saveSliceAnimation()
    : saveAnimation()
  ).catch(handleViewerError);
const resizeObserver = new ResizeObserver(scheduleDraw);
resizeObserver.observe($("volume").parentElement);
let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    redrawOverviews();
    scheduleDraw();
  }, 100);
});
updateScale();
build().catch((error) => {
  stopPlayback();
  $("inspector").hidden = true;
  $("run-meta").textContent = `Unable to load run: ${error.message}`;
  setVolumeStatus(`Unable to load run: ${error.message}`, true);
});
