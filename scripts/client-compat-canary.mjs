#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const descriptorPath = path.join(repoRoot, "manifest", "client-compat.v1.json");
const evidencePath = process.env.HONUA_CLIENT_COMPAT_EVIDENCE_PATH
  ?? path.join(repoRoot, ".artifacts", "client-compat-canary", "client-compat-canary.v1.json");
const timeoutMs = Number(process.env.HONUA_DEMO_TIMEOUT_MS ?? 20_000);

async function main() {
  const descriptorBytes = await readFile(descriptorPath);
  const descriptor = JSON.parse(descriptorBytes.toString("utf8"));
  if (descriptor.format !== "honua.demo.client-compat.v1" || descriptor.access?.allowAnonymous !== false) {
    throw new Error("client-compat descriptor is not the protected v1 contract");
  }

  const baseUrl = (process.env.HONUA_DEMO_BASE_URL ?? descriptor.baseUrl).replace(/\/$/u, "");
  const apiKey = process.env.HONUA_CLIENT_COMPAT_API_KEY ?? "";
  const results = [];
  const skipped = [];
  const metadataUrl = `${baseUrl}${descriptor.service.featureServerMetadataPath}?f=json`;
  const queryUrl = `${baseUrl}${descriptor.service.featureServerQueryPath}`;

  await probeAnonymousDenial(results, metadataUrl);
  let server = null;
  if (apiKey) {
    const lineage = serverLineage();
    server = lineage.server;
    if (lineage.error) {
      results.push({ name: "authenticated-lineage", status: 0, latencyMs: 0, bytes: 0, passed: false, error: lineage.error });
    } else {
      const metadata = await probeJson(results, "authenticated-metadata", metadataUrl, apiKey);
      if (metadata) validateMetadata(results.at(-1), metadata, descriptor.fixture.fields);

      const fieldNames = descriptor.fixture.fields.map((field) => field.name).join(",");
      const params = new URLSearchParams({
        where: "1=1",
        outFields: fieldNames,
        returnGeometry: "false",
        orderByFields: descriptor.fixture.primaryKey,
        f: "json",
      });
      const query = await probeJson(results, "authenticated-query", `${queryUrl}?${params}`, apiKey);
      if (query) validateQuery(results.at(-1), query, descriptor);
    }
  } else {
    skipped.push({
      name: "authenticated-schema-query",
      reason: "HONUA_CLIENT_COMPAT_API_KEY is not configured",
    });
  }

  const receipt = {
    format: "honua.demo.client-compat-canary.v1",
    schemaVersion: "1.0.0",
    generatedAt: new Date().toISOString(),
    target: {
      baseUrl,
      serviceName: descriptor.service.name,
      layerId: descriptor.service.layerId,
      descriptorSha256: createHash("sha256").update(descriptorBytes).digest("hex"),
      server,
    },
    authentication: {
      configured: Boolean(apiKey),
      credentialRecorded: false,
    },
    summary: {
      total: results.length,
      passed: results.filter((result) => result.passed).length,
      failed: results.filter((result) => !result.passed).length,
      skipped: skipped.length,
    },
    results,
    skipped,
  };
  await mkdir(path.dirname(evidencePath), { recursive: true });
  await writeFile(evidencePath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(receipt.summary)}\n`);
  for (const result of results) {
    process.stdout.write(`${result.passed ? "PASS" : "FAIL"} ${result.name} HTTP ${result.status}\n`);
    if (result.error) process.stdout.write(`  ${result.error}\n`);
  }
  for (const skip of skipped) process.stdout.write(`SKIP ${skip.name}: ${skip.reason}\n`);
  if (receipt.summary.failed > 0) process.exitCode = 1;
}

function serverLineage() {
  const commit = process.env.HONUA_CLIENT_COMPAT_SERVER_COMMIT ?? "";
  const image = process.env.HONUA_CLIENT_COMPAT_SERVER_IMAGE ?? "";
  if (!/^[0-9a-f]{40}$/u.test(commit)) {
    return { server: null, error: "authenticated proof requires a full 40-character server commit" };
  }
  if (!/@sha256:[0-9a-f]{64}$/u.test(image)) {
    return { server: null, error: "authenticated proof requires an image pinned by @sha256 digest" };
  }
  return { server: { commit, image }, error: null };
}

async function probeAnonymousDenial(results, url) {
  const result = await request("anonymous-denial", url, {});
  let payload = null;
  if (result.body) {
    try {
      payload = JSON.parse(result.body);
    } catch {
      // A transport-level 401/403 does not require a GeoServices JSON body.
    }
  }

  const exposesProtectedData = hasProtectedData(payload);
  const transportDenied = [401, 403].includes(result.status);
  const geoServicesErrorCode = Number(payload?.error?.code);
  const geoServicesDenied = result.status === 200 && geoServicesErrorCode === 499;

  result.passed = (transportDenied || geoServicesDenied) && !exposesProtectedData;
  if (geoServicesDenied) {
    result.denialMode = "geoservices-error";
    result.geoServicesErrorCode = geoServicesErrorCode;
  } else if (transportDenied) {
    result.denialMode = "http-status";
  }
  if (exposesProtectedData) {
    result.error = "anonymous response exposed protected metadata or features";
  } else if (!result.passed) {
    result.error = "expected HTTP 401/403 or HTTP 200 with GeoServices error code 499";
  }
  delete result.body;
  results.push(result);
}

function hasProtectedData(payload) {
  if (!payload || typeof payload !== "object") return false;
  return [
    payload.layers,
    payload.fields,
    payload.features,
    payload.data?.layers,
    payload.data?.fields,
    payload.data?.features,
  ].some(Array.isArray);
}

async function probeJson(results, name, url, apiKey) {
  const result = await request(name, url, { "X-API-Key": apiKey, accept: "application/json" });
  result.passed = result.status === 200 && result.bytes > 0;
  if (!result.passed) result.error = "expected HTTP 200 with a non-empty JSON body";
  results.push(result);
  if (!result.passed) {
    delete result.body;
    return null;
  }
  try {
    return JSON.parse(result.body);
  } catch (error) {
    fail(result, `invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  } finally {
    delete result.body;
  }
}

async function request(name, url, headers) {
  const started = performance.now();
  const result = { name, url, status: 0, latencyMs: 0, bytes: 0, passed: false };
  try {
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.text();
    result.status = response.status;
    result.latencyMs = Math.round(performance.now() - started);
    result.bytes = Buffer.byteLength(body);
    result.body = body;
  } catch (error) {
    result.latencyMs = Math.round(performance.now() - started);
    result.error = error instanceof Error ? error.message : String(error);
  }
  return result;
}

function validateMetadata(result, payload, expectedFields) {
  const fields = payload.fields ?? payload.data?.fields;
  if (!Array.isArray(fields)) {
    fail(result, "metadata response has no fields array");
    return;
  }
  const actual = new Set(fields.map((field) => String(typeof field === "string" ? field : field.name).toLowerCase()));
  const missing = expectedFields.map((field) => field.name).filter((name) => !actual.has(name));
  if (missing.length) fail(result, `metadata is missing fields: ${missing.join(", ")}`);
}

function validateQuery(result, payload, descriptor) {
  const features = payload.features ?? payload.data?.features;
  if (!Array.isArray(features) || features.length !== descriptor.fixture.expectedFeatureCount) {
    fail(result, `expected ${descriptor.fixture.expectedFeatureCount} query features`);
    return;
  }
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
  for (const [index, feature] of features.entries()) {
    const attributes = feature.attributes ?? feature.properties;
    const valid = attributes
      && Number.isInteger(attributes.objectid)
      && typeof attributes.name === "string"
      && (attributes.description === null || typeof attributes.description === "string")
      && typeof attributes.status === "string"
      && Number.isInteger(attributes.count)
      && typeof attributes.ratio === "number"
      && uuidPattern.test(attributes.uid)
      && (typeof attributes.active === "boolean" || attributes.active === 0 || attributes.active === 1);
    if (!valid) {
      fail(result, `feature ${index} violates the typed client-compat contract`);
      return;
    }
  }
}

function fail(result, error) {
  result.passed = false;
  result.error = error;
}

main().catch(async (error) => {
  await mkdir(path.dirname(evidencePath), { recursive: true });
  await writeFile(
    evidencePath,
    `${JSON.stringify({ format: "honua.demo.client-compat-canary.v1", generatedAt: new Date().toISOString(), fatal: error instanceof Error ? error.message : String(error) }, null, 2)}\n`,
    "utf8",
  );
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
