#!/usr/bin/env node
/**
 * warm-demo-cdn.mjs — pre-seed the demo.honua.io CDN for the demo scene presets.
 *
 * WHY: CloudFront (with Origin Shield, cloudfront.tf) caches every tile for
 * 24 h, but the FIRST visitor per tile per day still pays one origin trip.
 * The honua.io demo pages use fixed scene cameras, so the canonical viewports
 * are a small, enumerable set of tiles — one pass through the CDN after each
 * reseed / invalidation fills the shield for everyone.
 *
 * WHEN: after `aws cloudfront create-invalidation` (see README "reseed" notes)
 * or after a data reseed. Safe to run any time — already-warm entries just
 * report "Hit".
 *
 * WHAT it warms (everything a first visit actually fetches, nothing else):
 *   1. Dynamic MVT tiles (/ogc/tiles/…) for each vector layer in
 *      assets/demo/layers.json that has NO pmtiles block — i.e. only while
 *      the dynamic route is the visitor lane. Once a layer gains a static
 *      PMTiles archive (demo.js prefers those), it drops out automatically.
 *   2. The first 16 KiB range of every PMTiles archive (basemap, imagery,
 *      hillshade, terrain, and any vector archives) — the header+root
 *      directory read every pmtiles client issues before anything else.
 *   3. The first SDF glyph ranges for the label fontstack.
 *
 * Scene cameras come from two places:
 *   - assets/demos/imagery-terrain/config.json (fetched — studio scenes);
 *   - the SCENES array in assets/demo/demo.js (hardcoded below with
 *     provenance — they are code, not config; update on scene changes).
 *
 * Usage:  node warm-demo-cdn.mjs [--site https://honua.io] [--dry-run]
 * Exits non-zero if more than 10% of requests fail.
 */

const args = process.argv.slice(2);
const SITE = args.includes("--site") ? args[args.indexOf("--site") + 1] : "https://honua.io";
const DRY = args.includes("--dry-run");
const CONCURRENCY = 6;
const REQUEST_TIMEOUT_MS = 20000; // per-request bound; Node's global fetch has no default timeout
const VIEWPORT = { w: 1600, h: 1000 }; // generous desktop viewport
const PER_LAYER_SCENE_CAP = 80;
const TOTAL_CAP = 800;

/* Fixed cameras from assets/demo/demo.js SCENES (kept in sync by hand) plus
 * the page's initial island-wide view. zoom is MapLibre map zoom. */
const DEMO_JS_CAMERAS = [
  { name: "demo: initial / place-names", center: [-156.68, 20.87], zoom: 9 },
  { name: "demo: parcels-zoning", center: [-156.498, 20.885], zoom: 14 },
  { name: "demo: coastal-risk", center: [-156.46, 20.77], zoom: 12.4 },
  { name: "demo: terrain", center: [-156.22, 20.74], zoom: 11.2 },
  { name: "demo: imagery", center: [-156.47, 20.885], zoom: 13 },
];

/* ── tile math (WebMercator) ────────────────────────────────────── */

function lngLatToWorldPx(lng, lat, zoom) {
  const scale = 256 * 2 ** zoom;
  const x = ((lng + 180) / 360) * scale;
  const s = Math.sin((lat * Math.PI) / 180);
  const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale;
  return [x, y];
}

/* Visible tile range for a camera at integer tile zoom z (512px vector
 * tiles: MapLibre requests tile zoom = floor(map zoom)). */
function visibleTiles(camera, z) {
  const [cx, cy] = lngLatToWorldPx(camera.center[0], camera.center[1], camera.zoom);
  const pxPerTile = 256 * 2 ** (camera.zoom - z); // world px (at map zoom) covered by one z-tile
  const n = 2 ** z;
  const tiles = [];
  const minX = Math.floor((cx - VIEWPORT.w / 2) / pxPerTile);
  const maxX = Math.floor((cx + VIEWPORT.w / 2) / pxPerTile);
  const minY = Math.floor((cy - VIEWPORT.h / 2) / pxPerTile);
  const maxY = Math.floor((cy + VIEWPORT.h / 2) / pxPerTile);
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) {
      if (x >= 0 && y >= 0 && x < n && y < n) tiles.push([x, y]);
    }
  }
  return tiles;
}

/* ── build the warm list from the live contracts ────────────────── */

async function fetchJson(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return res.json();
}

const layers = await fetchJson(`${SITE}/assets/demo/layers.json`);
const base = layers.server.baseUrl;

let cameras = [...DEMO_JS_CAMERAS];
try {
  const studio = await fetchJson(`${SITE}/assets/demos/imagery-terrain/config.json`);
  for (const [id, cam] of Object.entries(studio.scenes ?? {})) {
    if (Array.isArray(cam.center)) cameras.push({ name: `studio: ${id}`, center: cam.center, zoom: cam.zoom });
  }
} catch {
  console.warn("studio config not reachable — warming demo.js scenes only");
}

const targets = []; // { url, label, headers? }
const seen = new Set();
function add(url, label, headers) {
  if (seen.has(url) || targets.length >= TOTAL_CAP) return;
  seen.add(url);
  targets.push({ url, label, headers });
}

// 1. dynamic MVT tiles — only for layers whose visitor lane IS the dynamic route
for (const layer of layers.layers ?? []) {
  if (layer.render !== "mvt" || !layer.tiles?.tileTemplate) continue;
  if (layer.pmtiles?.proxyUrl) continue; // static archive is the visitor lane
  const srcMax = typeof layer.tiles.maxzoom === "number" ? layer.tiles.maxzoom : 15;
  for (const camera of cameras) {
    const z = Math.min(srcMax, Math.floor(camera.zoom));
    if (typeof layer.minzoom === "number" && Math.floor(camera.zoom) < layer.minzoom) continue; // layer not drawn → not fetched
    let added = 0;
    for (const [x, y] of visibleTiles(camera, z)) {
      if (added++ >= PER_LAYER_SCENE_CAP) break;
      add(
        base + layer.tiles.tileTemplate.replace("{z}", z).replace("{x}", x).replace("{y}", y),
        `${layer.id} z${z} (${camera.name})`
      );
    }
  }
}

// 2. PMTiles archive headers — the first read every client makes
const archiveUrls = new Set();
if (layers.basemap?.proxyUrl) archiveUrls.add(layers.basemap.proxyUrl);
for (const b of layers.bases ?? []) {
  if (b.pmtiles?.proxyUrl) archiveUrls.add(b.pmtiles.proxyUrl);
  if (b.hillshade?.pmtiles?.proxyUrl) archiveUrls.add(b.hillshade.pmtiles.proxyUrl);
}
for (const l of layers.layers ?? []) {
  if (l.pmtiles?.proxyUrl) archiveUrls.add(l.pmtiles.proxyUrl);
}
for (const url of archiveUrls) {
  add(url, "pmtiles header", { range: "bytes=0-16383" });
}

// 3. first glyph ranges for the label fontstack
if (layers.server.glyphs) {
  for (const range of ["0-255", "256-511"]) {
    add(layers.server.glyphs.replace("{fontstack}", "NotoSansRegular").replace("{range}", range), "glyphs");
  }
}

console.log(`${targets.length} URLs to warm (${cameras.length} scene cameras, ${archiveUrls.size} archives)${DRY ? " [dry-run]" : ""}`);
if (DRY) {
  for (const t of targets) console.log(`  ${t.label.padEnd(40)} ${t.url}`);
  process.exit(0);
}

/* ── run with bounded concurrency ───────────────────────────────── */

let ok = 0, fail = 0, hits = 0, misses = 0;
const queue = [...targets];
async function worker() {
  for (;;) {
    const t = queue.shift();
    if (!t) return;
    try {
      const res = await fetch(t.url, {
        headers: t.headers ?? {},
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
      const cache = res.headers.get("x-cache") ?? "";
      const isHit = /hit/i.test(cache);
      if (res.ok) {
        ok++;
        isHit ? hits++ : misses++;
      } else {
        fail++;
        console.warn(`  HTTP ${res.status}  ${t.label}  ${t.url}`);
      }
      await res.arrayBuffer(); // drain so the CDN actually stores the body
    } catch (err) {
      fail++;
      console.warn(`  FAIL  ${t.label}  ${t.url}  (${err.message})`);
    }
  }
}
const started = Date.now();
await Promise.all(Array.from({ length: CONCURRENCY }, worker));
console.log(
  `warmed ${ok}/${targets.length} in ${((Date.now() - started) / 1000).toFixed(1)}s — ` +
    `${misses} filled (Miss), ${hits} already warm (Hit), ${fail} failed`
);
process.exit(fail > targets.length * 0.1 ? 1 : 0);
