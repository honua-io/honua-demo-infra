import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { inspectPng, selectWmsBindings, validateWmsCapabilities } from "./live-demo-canary.mjs";

const script = fileURLToPath(new URL("./live-demo-canary.mjs", import.meta.url));
const imageDigest = `sha256:${"a".repeat(64)}`;
const sourceCommit = "b".repeat(40);
const stacServerCommit = "73eaa60997e4aae9b57772c717c27e17a03b3fc6";
const stacCollectionId = "90810";

test("planned WMS canary binds deployment, manifest, capabilities, and semantic PNG", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "honua-wms-canary-"));
  const manifestBytes = Buffer.from(`${JSON.stringify(fixtureManifest())}\n`);
  const png = createPng(512, 512, (x, y) => (x < 256 && y < 256 ? [0, 130, 190, 210] : [0, 0, 0, 0]));
  const server = http.createServer((request, response) => handleRequest(request, response, manifestBytes, png));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const evidencePath = path.join(temp, "receipt.json");

  try {
    await runCanary({
      HONUA_DEMO_BASE_URL: `http://127.0.0.1:${port}`,
      HONUA_DEMO_TIMEOUT_MS: "2000",
      HONUA_DEMO_CANARY_EVIDENCE_PATH: evidencePath,
      HONUA_DEMO_WMS_ADMISSION: "planned",
      HONUA_DEMO_WMS_SERVER_IMAGE_DIGEST: imageDigest,
      HONUA_DEMO_WMS_SERVER_COMMIT: sourceCommit,
      HONUA_DEMO_WMS_SERVER_ARCHITECTURE: "arm64",
    });
    const receipt = JSON.parse(await readFile(evidencePath, "utf8"));
    assert.equal(receipt.summary.failed, 0);
    assert.equal(receipt.wms.bindingCount, 1);
    assert.equal(receipt.wms.deployment.imageDigest, imageDigest);
    assert.equal(receipt.manifest.sha256, createHash("sha256").update(manifestBytes).digest("hex"));
    const map = receipt.results.find((result) => result.name === "maui-flood-hazard:wms-map");
    assert.equal(map.semantic.width, 512);
    assert.equal(map.semantic.height, 512);
    assert.equal(map.semantic.distinctColors, 2);
    assert.ok(map.semantic.nonTransparentPixels > 64);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(temp, { recursive: true, force: true });
  }
});

test("PNG semantic gate rejects a same-color blank map", () => {
  const blank = createPng(8, 8, () => [255, 255, 255, 255]);
  assert.throws(
    () => inspectPng(blank, { width: 8, height: 8, minDistinctColors: 2, minNonTransparentPixels: 1 }),
    /distinct color/u,
  );
});

test("explicit live WMS admission requires at least one advertised live binding", () => {
  assert.throws(() => selectWmsBindings(fixtureManifest(), "live"), /at least one advertised live WMS binding/u);
});

test("optional-live WMS admission permits no binding without selecting planned WMS", () => {
  assert.deepEqual(selectWmsBindings(fixtureManifest(), "optional-live"), []);
});

test("capabilities gate requires the governed layer and rejects exceptions", () => {
  validateWmsCapabilities(
    '<?xml version="1.0"?><WMS_Capabilities><Capability><Layer><Name>maui-flood-hazard</Name></Layer></Capability></WMS_Capabilities>',
    "maui-flood-hazard",
  );
  assert.throws(
    () => validateWmsCapabilities("<WMS_Capabilities><ServiceException>broken</ServiceException></WMS_Capabilities>", "maui-flood-hazard"),
    /exception/u,
  );
});

function fixtureManifest() {
  const wms = {
    path: "/rest/services/maui-flood-hazard/MapServer/WMS",
    layerName: "maui-flood-hazard",
    capabilitiesVersion: "1.3.0",
    expectedMap: {
      version: "1.1.1", srs: "EPSG:4326", bbox: [-157.4, 20.45, -155.95, 21.25],
      width: 512, height: 512, format: "image/png", transparent: true,
      minDistinctColors: 2, minNonTransparentPixels: 64,
    },
  };
  return {
    format: "honua.demo-services.v1",
    schemaVersion: "1.1.0",
    sources: {
      stacSeed: `https://raw.githubusercontent.com/honua-io/honua-server/${stacServerCommit}/tests/seed/demo-stac-imagery-v1.sql`,
    },
    services: [
      { id: "maui-flood-hazard", protocols: {} },
      {
        id: "demo-stac",
        protocols: {
          stac: {
            path: "/stac",
            collectionsPath: "/stac/collections",
            searchPath: "/stac/search",
            collections: [{ id: stacCollectionId, path: `/stac/collections/${stacCollectionId}` }],
          },
        },
      },
    ],
    releaseContracts: {
      wms: {
        status: "planned", definitionSha256: "c".repeat(64),
        requiredServer: { imageDigest, sourceCommit, platform: { os: "linux", architecture: "arm64" } },
      },
    },
    releaseCandidates: { wms: { status: "planned", bindings: [{ serviceId: "maui-flood-hazard", wms }] } },
  };
}

function handleRequest(request, response, manifestBytes, png) {
  if (request.url === "/") {
    response.setHeader("content-type", "text/html");
    response.end('<!doctype html><main><h1>Honua demo environment</h1><a href="/healthz/live"></a><a href="/healthz/ready"></a><a href="/api/v1/capabilities/manifest"></a><a href="/demo-services.v1.json"></a><a href="/stac"></a><a href="https://samples.honua.io/"></a></main>');
  } else if (request.url === "/demo-services.v1.json") {
    response.setHeader("content-type", "application/json");
    response.end(manifestBytes);
  } else if (request.url === "/api/v1/capabilities/manifest") {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ server: { deploymentRevision: sourceCommit } }));
  } else if (request.url === "/healthz/ready") {
    response.end("ready");
  } else if (request.url === "/stac") {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ type: "Catalog" }));
  } else if (request.url === "/stac/collections") {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ collections: [{ id: stacCollectionId }] }));
  } else if (request.url === `/stac/collections/${stacCollectionId}`) {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ type: "Collection", id: stacCollectionId }));
  } else if (request.url === `/stac/collections/${stacCollectionId}/items?limit=2` || (request.method === "POST" && request.url === "/stac/search")) {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({
      type: "FeatureCollection",
      features: [{ type: "Feature", id: "fixture-item", collection: stacCollectionId, geometry: null, properties: {} }],
    }));
  } else if (request.url.includes("REQUEST=GetCapabilities")) {
    response.setHeader("content-type", "application/xml");
    response.end('<?xml version="1.0"?><WMS_Capabilities><Capability><Layer><Name>maui-flood-hazard</Name></Layer></Capability></WMS_Capabilities>');
  } else if (request.url.includes("REQUEST=GetMap")) {
    response.setHeader("content-type", "image/png");
    response.end(png);
  } else {
    response.statusCode = 404;
    response.end("missing");
  }
}

function createPng(width, height, pixelAt) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    const row = y * (width * 4 + 1);
    raw[row] = 0;
    for (let x = 0; x < width; x += 1) {
      const rgba = pixelAt(x, y);
      for (let channel = 0; channel < 4; channel += 1) raw[row + 1 + x * 4 + channel] = rgba[channel];
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0)),
  ]);
}

function chunk(type, data) {
  const value = Buffer.alloc(data.length + 12);
  value.writeUInt32BE(data.length, 0);
  value.write(type, 4, 4, "ascii");
  data.copy(value, 8);
  return value;
}

function runCanary(extraEnv) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], { env: { ...process.env, ...extraEnv }, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (value) => { stdout += value; });
    child.stderr.on("data", (value) => { stderr += value; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`canary exited ${code}\n${stdout}\n${stderr}`));
    });
  });
}
