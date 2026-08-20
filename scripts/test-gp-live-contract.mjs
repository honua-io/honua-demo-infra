import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { selectGovernedGpProcess } from "./live-demo-canary.mjs";

const repoRoot = fileURLToPath(new URL("../", import.meta.url));
const script = fileURLToPath(new URL("./live-demo-canary.mjs", import.meta.url));
const deploymentRevision = "6ad71ac701ca709ec671afd09257217e8d17a149";
const stacServerCommit = "1fc339a3692289e9bc4ec90ed1533c5eb22a995e";
const stacCollectionId = "90810";
const apiKey = "fixture-process-key";
const bufferOutput = Buffer.from(
  '{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[1,0],[0,1],[-1,0],[0,-1],[1,0]]]},"properties":{"processId":"geometry.buffer","inputSrid":4326,"bufferDistance":1}}',
);
const bufferSha256 = createHash("sha256").update(bufferOutput).digest("hex");

test("generated manifest governs only the bounded geometry.buffer process", async () => {
  const manifest = JSON.parse(await readFile(path.join(repoRoot, "manifest/demo-services.v1.json"), "utf8"));
  const process = selectGovernedGpProcess(manifest);
  assert.equal(process.id, "geometry.buffer");
  assert.deepEqual(process.execution.modes, ["sync", "async"]);
  assert.equal(process.execution.backend, "local");
  assert.equal(process.inputSchema.properties.srid.const, 4326);
  assert.equal(process.inputSchema.properties.distance.maximum, 1);
  assert.equal(process.auth.credentialProfile, "demo-process-execute");
  assert.equal(process.lifecycle.dismissConformanceClaimed, false);
  assert.deepEqual(process.requestBudget, {
    scope: "demo-api-key/global",
    requestsPerWindow: 60,
    windowSeconds: 60,
    resetPolicy: "fixed-window",
    responseHeaders: ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
  });
  assert.equal(
    process.canary.expectedSha256,
    "a5797d4b43e2d8af4ac8b3be5dbc31edf96a206ab960497b991e191e5a7b3997",
  );
});

test("live canary proves sync and async geometry identity through one scoped key", async () => {
  const harness = await createHarness();
  try {
    const result = await runCanary(harness.baseUrl, harness.evidencePath, apiKey);
    assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
    const receipt = JSON.parse(await readFile(harness.evidencePath, "utf8"));
    assert.equal(receipt.summary.failed, 0);
    assert.equal(receipt.gp.processId, "geometry.buffer");
    assert.equal(receipt.gp.jobId, "gp-job-1");
    assert.equal(receipt.gp.syncSha256, bufferSha256);
    assert.equal(receipt.gp.asyncSha256, bufferSha256);
    assert.deepEqual(receipt.gp.observedStatuses, ["running", "successful"]);
    assert.ok(harness.requests.every((request) =>
      !request.path.startsWith("/ogc/processes/") || request.apiKey === apiKey));
  } finally {
    await harness.close();
  }
});

test("live canary fails closed when the scoped process key is absent", async () => {
  const harness = await createHarness();
  try {
    const result = await runCanary(harness.baseUrl, harness.evidencePath, "");
    assert.notEqual(result.code, 0);
    const receipt = JSON.parse(await readFile(harness.evidencePath, "utf8"));
    assert.match(receipt.fatal, /HONUA_DEMO_GP_API_KEY/u);
    assert.equal(harness.requests.some((request) => request.path.includes("geometry.buffer")), false);
  } finally {
    await harness.close();
  }
});

test("live canary rejects geometry bytes that drift from the manifest digest", async () => {
  const harness = await createHarness({ corruptSync: true });
  try {
    const result = await runCanary(harness.baseUrl, harness.evidencePath, apiKey);
    assert.notEqual(result.code, 0);
    const receipt = JSON.parse(await readFile(harness.evidencePath, "utf8"));
    const sync = receipt.results.find((entry) => entry.name === "geometry.buffer:sync");
    assert.equal(sync.passed, false);
    assert.match(sync.error, /does not match pinned/u);
  } finally {
    await harness.close();
  }
});

async function createHarness({ corruptSync = false } = {}) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "honua-gp-canary-"));
  const evidencePath = path.join(temp, "receipt.json");
  const requests = [];
  let statusReads = 0;
  const manifest = fixtureManifest();
  const server = http.createServer(async (request, response) => {
    const body = await readRequestBody(request);
    requests.push({
      method: request.method,
      path: request.url,
      apiKey: request.headers["x-api-key"] ?? null,
      prefer: request.headers.prefer ?? null,
      body,
    });

    const rateHeaders = {
      "x-ratelimit-limit": "60",
      "x-ratelimit-remaining": "59",
      "x-ratelimit-reset": "1893456000",
    };
    const send = (status, payload, contentType = "application/json", headers = {}) => {
      response.statusCode = status;
      response.setHeader("content-type", contentType);
      for (const [name, value] of Object.entries(headers)) response.setHeader(name, value);
      response.end(Buffer.isBuffer(payload) || typeof payload === "string" ? payload : JSON.stringify(payload));
    };

    if (request.url === "/") {
      send(200, '<!doctype html><main><h1>Honua demo environment</h1><a href="/healthz/live"></a><a href="/healthz/ready"></a><a href="/api/v1/capabilities/manifest"></a><a href="/demo-services.v1.json"></a><a href="/stac"></a><a href="https://samples.honua.io/"></a></main>', "text/html");
    } else if (request.url === "/api/v1/capabilities/manifest") {
      send(200, { server: { deploymentRevision } });
    } else if (request.url === "/demo-services.v1.json") {
      send(200, manifest);
    } else if (request.url === "/healthz/ready") {
      send(200, "ready", "text/plain");
    } else if (request.url === "/stac") {
      send(200, { type: "Catalog" });
    } else if (request.url === "/stac/collections") {
      send(200, { collections: [{ id: stacCollectionId }] });
    } else if (request.url === `/stac/collections/${stacCollectionId}`) {
      send(200, { type: "Collection", id: stacCollectionId });
    } else if (request.url === `/stac/collections/${stacCollectionId}/items?limit=2` ||
        (request.method === "POST" && request.url === "/stac/search")) {
      send(200, featureCollection());
    } else if (request.method === "POST" &&
        request.url === "/ogc/processes/processes/geometry.buffer/execution") {
      if (request.headers["x-api-key"] !== apiKey) {
        send(401, { title: "Unauthorized" });
      } else if (request.headers.prefer === "respond-sync") {
        send(
          200,
          corruptSync ? Buffer.from("drift") : bufferOutput,
          "application/geo+json",
          { ...rateHeaders, "preference-applied": "respond-sync" },
        );
      } else {
        send(
          201,
          { jobID: "gp-job-1", status: "accepted" },
          "application/json",
          {
            ...rateHeaders,
            "preference-applied": "respond-async",
            location: "/ogc/processes/jobs/gp-job-1",
          },
        );
      }
    } else if (request.method === "GET" && request.url === "/ogc/processes/jobs/gp-job-1") {
      statusReads += 1;
      send(200, { jobID: "gp-job-1", status: statusReads === 1 ? "running" : "successful" }, "application/json", rateHeaders);
    } else if (request.method === "GET" && request.url === "/ogc/processes/jobs/gp-job-1/results") {
      send(200, {
        outputFeatureLayer: {
          id: "gp-job-1:artifact:1",
          kind: "FeatureLayer",
          href: `data:application/geo+json;base64,${bufferOutput.toString("base64")}`,
          type: "application/geo+json",
        },
      }, "application/json", rateHeaders);
    } else {
      send(404, { error: "missing" });
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    evidencePath,
    requests,
    close: async () => {
      await new Promise((resolve) => server.close(resolve));
      await rm(temp, { recursive: true, force: true });
    },
  };
}

function fixtureManifest() {
  const process = {
    id: "geometry.buffer",
    execution: {
      path: "/ogc/processes/processes/geometry.buffer/execution",
      modes: ["sync", "async"],
      syncPreference: "respond-sync",
      asyncPreference: "respond-async",
    },
    auth: { mode: "demo-key", header: "X-API-Key" },
    lifecycle: {
      statusPath: "/ogc/processes/jobs/{jobId}",
      resultsPath: "/ogc/processes/jobs/{jobId}/results",
    },
    output: { name: "outputFeatureLayer", kind: "FeatureLayer", mediaType: "application/geo+json" },
    requestBudget: {
      scope: "demo-api-key/global",
      requestsPerWindow: 60,
      windowSeconds: 60,
      resetPolicy: "fixed-window",
    },
    canary: {
      inputs: { wkb: { type: "Point", coordinates: [0, 0] }, srid: 4326, distance: 1, geodesic: false },
      expectedMediaType: "application/geo+json",
      expectedSha256: bufferSha256,
    },
  };
  return {
    format: "honua.demo-services.v1",
    schemaVersion: "1.3.0",
    sources: {
      stacSeed: `https://raw.githubusercontent.com/honua-io/honua-server/${stacServerCommit}/tests/seed/demo-stac-imagery-v1.sql`,
      stacSeedSha256: "d".repeat(64),
    },
    services: [{
      id: "demo-stac",
      protocols: {
        stac: {
          path: "/stac",
          collectionsPath: "/stac/collections",
          searchPath: "/stac/search",
          collections: [{ id: stacCollectionId, path: `/stac/collections/${stacCollectionId}` }],
        },
      },
    }],
    processes: [process],
  };
}

function featureCollection() {
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", id: "reef-scene-1", collection: stacCollectionId, geometry: null, properties: {} }],
  };
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

function runCanary(baseUrl, evidencePath, key) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      env: {
        ...process.env,
        HONUA_DEMO_BASE_URL: baseUrl,
        HONUA_DEMO_TIMEOUT_MS: "2000",
        HONUA_DEMO_GP_POLL_INTERVAL_MS: "10",
        HONUA_DEMO_GP_API_KEY: key,
        HONUA_DEMO_CANARY_EVIDENCE_PATH: evidencePath,
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
