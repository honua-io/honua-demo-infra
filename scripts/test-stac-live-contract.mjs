import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./live-demo-canary.mjs", import.meta.url));
const deploymentRevision = "6ad71ac701ca709ec671afd09257217e8d17a149";
const collectionId = "90810";

test("STAC canary binds deployment revision and non-empty collection results", async () => {
  const harness = await createHarness(false);
  try {
    const result = await runCanary(harness.baseUrl, harness.evidencePath);
    assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);

    const receipt = JSON.parse(await readFile(harness.evidencePath, "utf8"));
    assert.equal(receipt.summary.failed, 0);
    assert.equal(receipt.deployment.revision, deploymentRevision);
    assert.equal(receipt.stac.canaryCollectionId, collectionId);
    for (const suffix of ["items", "search"]) {
      const proof = receipt.results.find((entry) => entry.name === `demo-stac:stac:${collectionId}:${suffix}`);
      assert.equal(proof.semantic.collectionId, collectionId);
      assert.deepEqual(proof.semantic.itemIds, ["reef-scene-1"]);
    }
    assert.deepEqual(harness.searchBodies, [{ collections: [collectionId], limit: 2 }]);
  } finally {
    await harness.close();
  }
});

test("STAC canary rejects an empty collection-bound search", async () => {
  const harness = await createHarness(true);
  try {
    const result = await runCanary(harness.baseUrl, harness.evidencePath);
    assert.notEqual(result.code, 0);

    const receipt = JSON.parse(await readFile(harness.evidencePath, "utf8"));
    const proof = receipt.results.find((entry) => entry.name === `demo-stac:stac:${collectionId}:search`);
    assert.equal(proof.passed, false);
    assert.match(proof.error, /non-empty result/u);
  } finally {
    await harness.close();
  }
});

async function createHarness(emptySearch) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "honua-stac-canary-"));
  const evidencePath = path.join(temp, "receipt.json");
  const searchBodies = [];
  const server = http.createServer(async (request, response) => {
    const body = await readRequestBody(request);
    if (request.method === "POST" && request.url === "/stac/search") {
      searchBodies.push(JSON.parse(body));
    }
    const payload = route(request, emptySearch);
    response.statusCode = payload.status ?? 200;
    response.setHeader("content-type", payload.contentType ?? "application/json");
    response.end(typeof payload.body === "string" ? payload.body : JSON.stringify(payload.body));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    evidencePath,
    searchBodies,
    close: async () => {
      await new Promise((resolve) => server.close(resolve));
      await rm(temp, { recursive: true, force: true });
    },
  };
}

function route(request, emptySearch) {
  if (request.url === "/") {
    return {
      contentType: "text/html",
      body: '<!doctype html><main><h1>Honua demo environment</h1><a href="/healthz/live"></a><a href="/healthz/ready"></a><a href="/api/v1/capabilities/manifest"></a><a href="/demo-services.v1.json"></a><a href="/stac"></a><a href="https://samples.honua.io/"></a></main>',
    };
  }
  if (request.url === "/healthz/ready") return { contentType: "text/plain", body: "Ready" };
  if (request.url === "/api/v1/capabilities/manifest") return { body: { server: { deploymentRevision } } };
  if (request.url === "/demo-services.v1.json") return { body: fixtureManifest() };
  if (request.url === "/stac") return { body: { type: "Catalog" } };
  if (request.url === "/stac/collections") return { body: { collections: [{ id: collectionId }] } };
  if (request.url === `/stac/collections/${collectionId}`) return { body: { type: "Collection", id: collectionId } };
  if (request.url === `/stac/collections/${collectionId}/items?limit=2`) return { body: featureCollection() };
  if (request.method === "POST" && request.url === "/stac/search") {
    return { body: emptySearch ? featureCollection([]) : featureCollection() };
  }
  return { status: 404, body: { error: "missing" } };
}

function fixtureManifest() {
  return {
    format: "honua.demo-services.v1",
    schemaVersion: "1.1.0",
    services: [{
      id: "demo-stac",
      protocols: {
        stac: {
          path: "/stac",
          collectionsPath: "/stac/collections",
          searchPath: "/stac/search",
          collections: [{ id: collectionId, path: `/stac/collections/${collectionId}` }],
        },
      },
    }],
  };
}

function featureCollection(features = [{
  type: "Feature",
  id: "reef-scene-1",
  collection: collectionId,
  geometry: null,
  properties: {},
}]) {
  return { type: "FeatureCollection", features };
}

function readRequestBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function runCanary(baseUrl, evidencePath) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      env: {
        ...process.env,
        HONUA_DEMO_BASE_URL: baseUrl,
        HONUA_DEMO_TIMEOUT_MS: "2000",
        HONUA_DEMO_CANARY_EVIDENCE_PATH: evidencePath,
        HONUA_DEMO_EXPECTED_DEPLOYMENT_REVISION: deploymentRevision,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (value) => { stdout += value; });
    child.stderr.on("data", (value) => { stderr += value; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}
