#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";

const scriptPath = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(scriptPath), "..");
const evidenceDir = path.join(repoRoot, ".artifacts", "live-demo-canary");
const evidencePath = process.env.HONUA_DEMO_CANARY_EVIDENCE_PATH
  ?? path.join(evidenceDir, "live-demo-canary.v1.json");
const baseUrl = (process.env.HONUA_DEMO_BASE_URL ?? "https://demo.honua.io").replace(/\/$/u, "");
const timeoutMs = Number(process.env.HONUA_DEMO_TIMEOUT_MS ?? 20_000);
const wmsAdmission = process.env.HONUA_DEMO_WMS_ADMISSION ?? "optional-live";
const expectedDeploymentRevision = (process.env.HONUA_DEMO_EXPECTED_DEPLOYMENT_REVISION ?? "").trim();
const requireDeploymentBinding = process.env.HONUA_DEMO_REQUIRE_DEPLOYMENT_BINDING === "true";
const expectedStacSeedUrl = (process.env.HONUA_DEMO_EXPECTED_STAC_SEED_URL ?? "").trim();
const expectedStacServerCommit = (process.env.HONUA_DEMO_EXPECTED_STAC_SERVER_COMMIT ?? "").trim();
const expectedStacSeedSha256 = (process.env.HONUA_DEMO_EXPECTED_STAC_SEED_SHA256 ?? "").trim();
const expectedManifestSha256 = (process.env.HONUA_DEMO_EXPECTED_MANIFEST_SHA256 ?? "").trim();
const requireStacSeedBinding = process.env.HONUA_DEMO_REQUIRE_STAC_SEED_BINDING === "true";
const expectedStacMetadataEnvironment = (process.env.HONUA_DEMO_EXPECTED_STAC_METADATA_ENVIRONMENT ?? "").trim();
const expectedStacMetadataRevisionText = (process.env.HONUA_DEMO_EXPECTED_STAC_METADATA_REVISION ?? "").trim();
const expectedStacExecutionSha256 = (process.env.HONUA_DEMO_EXPECTED_STAC_EXECUTION_SHA256 ?? "").trim();
const stacCanaryCollectionId = process.env.HONUA_DEMO_STAC_CANARY_COLLECTION_ID ?? "90810";
const results = [];

async function main() {
  if (!["live", "planned", "optional-live"].includes(wmsAdmission)) {
    throw new Error("HONUA_DEMO_WMS_ADMISSION must be live, planned, or optional-live");
  }
  if (requireDeploymentBinding) {
    if (!/^[0-9a-f]{40}$/u.test(expectedDeploymentRevision)) {
      throw new Error("dispatch requires an exact 40-character HONUA_DEMO_EXPECTED_DEPLOYMENT_REVISION");
    }
    if (!expectedStacSeedUrl || !/^[0-9a-f]{40}$/u.test(expectedStacServerCommit) || !/^[0-9a-f]{64}$/u.test(expectedStacSeedSha256) || !/^[0-9a-f]{64}$/u.test(expectedManifestSha256)) {
      throw new Error("dispatch requires exact checked-out STAC seed URL, server commit, source digest, and manifest SHA-256 bindings");
    }
  }
  const expectedStacMetadataRevision = Number(expectedStacMetadataRevisionText);
  if (requireStacSeedBinding && (
    expectedStacMetadataEnvironment !== "Production"
    || !Number.isSafeInteger(expectedStacMetadataRevision)
    || expectedStacMetadataRevision < 1
    || !/^[0-9a-f]{64}$/u.test(expectedStacExecutionSha256)
  )) {
    throw new Error("canary requires an active Production STAC seed revision and exact execution digest");
  }

  await probeLanding(results);
  const capabilityManifest = await probeJson(results, "capability-manifest", "/api/v1/capabilities/manifest");
  const deploymentRevision = capabilityManifest.server?.deploymentRevision;
  const capabilityResult = results.find((result) => result.name === "capability-manifest");
  if (!/^[0-9a-f]{40}$/u.test(deploymentRevision ?? "")) {
    failResult(capabilityResult, "capability manifest has no exact 40-character deployment revision");
  } else if (expectedDeploymentRevision && deploymentRevision !== expectedDeploymentRevision) {
    failResult(capabilityResult, `deployment revision ${deploymentRevision} does not match expected revision ${expectedDeploymentRevision}`);
  } else if (capabilityResult) {
    capabilityResult.semantic = { deploymentRevision };
  }
  const manifest = await probeJson(results, "manifest", "/demo-services.v1.json");
  if (manifest.format !== "honua.demo-services.v1" || !/^1\.\d+\.\d+$/u.test(manifest.schemaVersion ?? "")) {
    throw new Error(`unexpected manifest contract ${manifest.format}@${manifest.schemaVersion}`);
  }
  if (!Array.isArray(manifest.services) || manifest.services.length === 0) {
    throw new Error("published demo manifest contains no services");
  }
  const manifestResult = results.find((result) => result.name === "manifest");
  const manifestSha256 = manifestResult?.sha256;
  const stacSeedUrl = manifest.sources?.stacSeed;
  const stacSeedMatch = /^https:\/\/raw\.githubusercontent\.com\/honua-io\/honua-server\/([0-9a-f]{40})\/tests\/seed\/demo-stac-imagery-v1\.sql$/u.exec(stacSeedUrl ?? "");
  const stacServerCommit = stacSeedMatch?.[1] ?? null;
  const stacSeedSha256 = manifest.sources?.stacSeedSha256;
  if (!stacSeedMatch) {
    failResult(manifestResult, "manifest sources.stacSeed is not an immutable honua-server commit URL");
  } else if (expectedStacSeedUrl && stacSeedUrl !== expectedStacSeedUrl) {
    failResult(manifestResult, "published manifest STAC seed URL does not match the checked-out contract");
  } else if (expectedStacServerCommit && stacServerCommit !== expectedStacServerCommit) {
    failResult(manifestResult, "published manifest STAC seed commit does not match the checked-out contract");
  } else if (!/^[0-9a-f]{64}$/u.test(stacSeedSha256 ?? "")) {
    failResult(manifestResult, "published manifest STAC seed source digest is invalid");
  } else if (expectedStacSeedSha256 && stacSeedSha256 !== expectedStacSeedSha256) {
    failResult(manifestResult, "published manifest STAC seed source digest does not match the checked-out contract");
  } else if (expectedManifestSha256 && manifestSha256 !== expectedManifestSha256) {
    failResult(manifestResult, "published manifest digest does not match the checked-out contract");
  }
  if (manifestResult) {
    manifestResult.semantic = { ...(manifestResult.semantic ?? {}), stacSeedUrl, stacServerCommit, stacSeedSha256 };
  }

  await probeText(results, "readiness", "/healthz/ready");
  const stacServices = manifest.services.filter((service) => service.protocols?.stac);
  if (stacServices.length === 0) {
    throw new Error("published demo manifest contains no advertised STAC service");
  }
  const canaryBindings = [];
  for (const service of manifest.services) {
    const protocols = service.protocols ?? {};
    if (protocols.featureServer) {
      await probeJson(results, `${service.id}:feature-server`, `${protocols.featureServer.path}?f=json`);
    }
    if (protocols.ogcFeatures) {
      await probeJson(results, `${service.id}:ogc-features`, protocols.ogcFeatures.path);
    }
    if (protocols.imageServerTiles) {
      const tilePath = protocols.imageServerTiles.tileTemplate
        .replace("{z}", "10")
        .replace("{y}", "451")
        .replace("{x}", "67");
      await probeBinary(results, `${service.id}:image-tile`, tilePath);
    }
    if (protocols.terrainRgb) {
      await probeJson(results, `${service.id}:terrain-tilejson`, protocols.terrainRgb.tileJson);
    }
    if (protocols.pmtiles) {
      await probeRange(results, `${service.id}:pmtiles-range`, protocols.pmtiles.path);
    }
    if (protocols.stac) {
      await probeJson(results, `${service.id}:stac-root`, protocols.stac.path);
      await probeJson(results, `${service.id}:stac-collections`, protocols.stac.collectionsPath);
      for (const collection of protocols.stac.collections ?? []) {
        await probeJson(results, `${service.id}:stac:${collection.id}`, collection.path);
      }
      const canaryCollection = (protocols.stac.collections ?? [])
        .find((collection) => collection.id === stacCanaryCollectionId);
      if (canaryCollection) {
        canaryBindings.push({ service, collection: canaryCollection });
      }
    }
  }
  if (canaryBindings.length !== 1) {
    throw new Error(`published STAC contract must declare exact canary collection ${stacCanaryCollectionId} once; found ${canaryBindings.length}`);
  }
  const stacCanary = canaryBindings[0];
  await probeStacFeatureCollection(
    results,
    `${stacCanary.service.id}:stac:${stacCanaryCollectionId}:items`,
    `${stacCanary.collection.path}/items?limit=2`,
    stacCanaryCollectionId,
    2,
  );
  await probeStacFeatureCollection(
    results,
    `${stacCanary.service.id}:stac:${stacCanaryCollectionId}:search`,
    stacCanary.service.protocols.stac.searchPath,
    stacCanaryCollectionId,
    2,
    {
      method: "POST",
      body: JSON.stringify({ collections: [stacCanaryCollectionId], limit: 2 }),
    },
  );

  const wmsBindings = selectWmsBindings(manifest, wmsAdmission);
  const wmsRelease = manifest.releaseContracts?.wms;
  const wmsContractAdmission = wmsAdmission === "optional-live" ? "live" : wmsAdmission;
  let deployment = null;
  if (wmsBindings.length > 0) {
    deployment = validateWmsDeployment(wmsRelease, wmsContractAdmission);
    for (const binding of wmsBindings) {
      await probeWmsCapabilities(results, binding);
      await probeWmsMap(results, binding);
    }
  }

  const glyphs = manifest.assets?.glyphs;
  if (glyphs?.path && glyphs.fontstacks?.length) {
    const glyphPath = glyphs.path.replace("{fontstack}", glyphs.fontstacks[0]).replace("{range}", "0-255");
    await probeBinary(results, "glyphs:first-range", glyphPath);
  }

  const receipt = {
    format: "honua.demo.live-canary.v1",
    generatedAt: new Date().toISOString(),
    baseUrl,
    deployment: {
      revision: deploymentRevision ?? null,
      expectedRevision: expectedDeploymentRevision || null,
    },
    manifest: {
      format: manifest.format,
      schemaVersion: manifest.schemaVersion,
      serviceCount: manifest.services.length,
      sha256: manifestSha256,
      expectedSha256: expectedManifestSha256 || null,
      stacSeedUrl,
      expectedStacSeedUrl: expectedStacSeedUrl || null,
      stacServerCommit,
      expectedStacServerCommit: expectedStacServerCommit || null,
      stacSeedSha256,
      expectedStacSeedSha256: expectedStacSeedSha256 || null,
    },
    stac: {
      canaryCollectionId: stacCanaryCollectionId,
      serviceId: stacCanary.service.id,
      metadataEnvironment: expectedStacMetadataEnvironment || null,
      metadataRevision: requireStacSeedBinding ? expectedStacMetadataRevision : null,
      executionSha256: expectedStacExecutionSha256 || null,
    },
    wms: {
      admission: wmsAdmission,
      contractAdmission: wmsContractAdmission,
      bindingCount: wmsBindings.length,
      deployment,
      releaseDefinitionSha256: wmsRelease?.definitionSha256 ?? null,
    },
    summary: {
      total: results.length,
      passed: results.filter((result) => result.passed).length,
      failed: results.filter((result) => !result.passed).length,
    },
    results,
  };
  await mkdir(path.dirname(evidencePath), { recursive: true });
  await writeFile(evidencePath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(receipt.summary)}\n`);
  for (const result of results) {
    process.stdout.write(`${result.passed ? "PASS" : "FAIL"} ${result.name} ${result.status} ${result.latencyMs}ms\n`);
    if (result.error) process.stdout.write(`  ${result.error}\n`);
  }
  if (receipt.summary.failed > 0) process.exitCode = 1;
}

export function selectWmsBindings(manifest, admission) {
  if (admission === "planned") {
    const candidate = manifest.releaseCandidates?.wms;
    if (!candidate || candidate.status !== "planned" || !Array.isArray(candidate.bindings)) {
      throw new Error("planned WMS admission requested but the manifest has no planned WMS bindings");
    }
    return candidate.bindings;
  }
  const liveBindings = manifest.services
    .filter((service) => service.protocols?.wms)
    .map((service) => ({ serviceId: service.id, wms: service.protocols.wms }));
  if (admission === "live" && liveBindings.length === 0) {
    throw new Error("live WMS admission requires at least one advertised live WMS binding");
  }
  return liveBindings;
}

function validateWmsDeployment(release, admission) {
  if (!release || release.status !== admission) {
    throw new Error(`WMS release status ${release?.status ?? "missing"} does not match ${admission} admission`);
  }
  const observed = {
    imageDigest: process.env.HONUA_DEMO_WMS_SERVER_IMAGE_DIGEST ?? "",
    sourceCommit: process.env.HONUA_DEMO_WMS_SERVER_COMMIT ?? "",
    architecture: process.env.HONUA_DEMO_WMS_SERVER_ARCHITECTURE ?? "",
  };
  const required = release.requiredServer ?? {};
  const expected = {
    imageDigest: required.imageDigest,
    sourceCommit: required.sourceCommit,
    architecture: required.platform?.architecture,
  };
  for (const key of Object.keys(expected)) {
    if (!observed[key]) throw new Error(`missing deployed WMS identity ${key}`);
    if (observed[key] !== expected[key]) {
      throw new Error(`deployed WMS ${key} does not match the release contract`);
    }
  }
  return observed;
}

async function probeWmsCapabilities(results, binding) {
  const query = new URLSearchParams({
    SERVICE: "WMS",
    REQUEST: "GetCapabilities",
    VERSION: binding.wms.capabilitiesVersion,
  });
  const response = await probe(
    results,
    `${binding.serviceId}:wms-capabilities`,
    `${binding.wms.path}?${query}`,
    { accept: "application/xml,text/xml" },
    [200],
  );
  if (!response.ok) return;
  try {
    validateWmsCapabilities(response.body.toString("utf8"), binding.wms.layerName);
    response.result.semantic = { layerName: binding.wms.layerName, exceptionFree: true };
  } catch (error) {
    failResult(response.result, error instanceof Error ? error.message : String(error));
  }
}

async function probeWmsMap(results, binding) {
  const response = await probe(
    results,
    `${binding.serviceId}:wms-map`,
    buildWmsMapPath(binding.wms),
    { accept: "image/png" },
    [200],
  );
  if (!response.ok) return;
  const contentType = response.result.contentType?.toLowerCase() ?? "";
  if (!contentType.startsWith("image/png")) {
    failResult(response.result, `expected content-type image/png (received ${contentType || "none"})`);
    return;
  }
  try {
    const expected = binding.wms.expectedMap;
    response.result.semantic = inspectPng(response.body, {
      width: expected.width,
      height: expected.height,
      minDistinctColors: expected.minDistinctColors,
      minNonTransparentPixels: expected.minNonTransparentPixels,
    });
  } catch (error) {
    failResult(response.result, error instanceof Error ? error.message : String(error));
  }
}

export function validateWmsCapabilities(xml, layerName) {
  if (!/<(?:\w+:)?(?:WMS_Capabilities|WMT_MS_Capabilities)\b/u.test(xml)) {
    throw new Error("WMS capabilities root is missing");
  }
  if (/<(?:\w+:)?(?:ServiceException|ExceptionReport)\b/iu.test(xml)) {
    throw new Error("WMS capabilities contains an exception");
  }
  const escaped = layerName.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  if (!new RegExp(`<Name>\\s*${escaped}\\s*</Name>`, "u").test(xml)) {
    throw new Error(`WMS capabilities does not name layer ${layerName}`);
  }
}

export function buildWmsMapPath(wms) {
  const expected = wms.expectedMap;
  const query = new URLSearchParams({
    SERVICE: "WMS",
    REQUEST: "GetMap",
    VERSION: expected.version,
    LAYERS: wms.layerName,
    STYLES: "",
    SRS: expected.srs,
    BBOX: expected.bbox.join(","),
    WIDTH: String(expected.width),
    HEIGHT: String(expected.height),
    FORMAT: expected.format,
    TRANSPARENT: expected.transparent ? "TRUE" : "FALSE",
  });
  return `${wms.path}?${query}`;
}

export function inspectPng(buffer, expected) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (buffer.length < 33 || !buffer.subarray(0, 8).equals(signature)) {
    throw new Error("WMS map is not a PNG");
  }
  let offset = 8;
  let header = null;
  let palette = null;
  let transparency = null;
  const imageData = [];
  while (offset + 12 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    const dataStart = offset + 8;
    const dataEnd = dataStart + length;
    if (dataEnd + 4 > buffer.length) throw new Error("WMS PNG has a truncated chunk");
    const data = buffer.subarray(dataStart, dataEnd);
    if (type === "IHDR") {
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === "PLTE") palette = data;
    else if (type === "tRNS") transparency = data;
    else if (type === "IDAT") imageData.push(data);
    offset = dataEnd + 4;
    if (type === "IEND") break;
  }
  if (!header || imageData.length === 0) throw new Error("WMS PNG is missing IHDR or IDAT");
  if (header.width !== expected.width || header.height !== expected.height) {
    throw new Error(`WMS PNG dimensions ${header.width}x${header.height} do not match ${expected.width}x${expected.height}`);
  }
  if (header.bitDepth !== 8 || header.interlace !== 0) {
    throw new Error("WMS PNG must be non-interlaced 8-bit pixels");
  }
  const channels = new Map([[0, 1], [2, 3], [3, 1], [4, 2], [6, 4]]).get(header.colorType);
  if (!channels) throw new Error(`unsupported WMS PNG color type ${header.colorType}`);
  if (header.colorType === 3 && !palette) throw new Error("indexed WMS PNG is missing a palette");

  const stride = header.width * channels;
  const inflated = inflateSync(Buffer.concat(imageData));
  if (inflated.length !== (stride + 1) * header.height) {
    throw new Error("WMS PNG scanline size is invalid");
  }
  const pixels = Buffer.alloc(stride * header.height);
  for (let y = 0; y < header.height; y += 1) {
    const sourceOffset = y * (stride + 1);
    const filter = inflated[sourceOffset];
    for (let x = 0; x < stride; x += 1) {
      const raw = inflated[sourceOffset + 1 + x];
      const left = x >= channels ? pixels[y * stride + x - channels] : 0;
      const up = y > 0 ? pixels[(y - 1) * stride + x] : 0;
      const upLeft = y > 0 && x >= channels ? pixels[(y - 1) * stride + x - channels] : 0;
      let value;
      if (filter === 0) value = raw;
      else if (filter === 1) value = raw + left;
      else if (filter === 2) value = raw + up;
      else if (filter === 3) value = raw + Math.floor((left + up) / 2);
      else if (filter === 4) value = raw + paeth(left, up, upLeft);
      else throw new Error(`unsupported WMS PNG filter ${filter}`);
      pixels[y * stride + x] = value & 0xff;
    }
  }

  const colors = new Set();
  let nonTransparentPixels = 0;
  const totalPixels = header.width * header.height;
  for (let index = 0; index < totalPixels; index += 1) {
    const base = index * channels;
    let rgba;
    if (header.colorType === 0) rgba = [pixels[base], pixels[base], pixels[base], 255];
    else if (header.colorType === 2) rgba = [pixels[base], pixels[base + 1], pixels[base + 2], 255];
    else if (header.colorType === 3) {
      const paletteIndex = pixels[base];
      const paletteOffset = paletteIndex * 3;
      if (paletteOffset + 2 >= palette.length) throw new Error("WMS PNG palette index is invalid");
      rgba = [palette[paletteOffset], palette[paletteOffset + 1], palette[paletteOffset + 2], transparency?.[paletteIndex] ?? 255];
    } else if (header.colorType === 4) rgba = [pixels[base], pixels[base], pixels[base], pixels[base + 1]];
    else rgba = [pixels[base], pixels[base + 1], pixels[base + 2], pixels[base + 3]];
    if (rgba[3] > 0) nonTransparentPixels += 1;
    colors.add(rgba.join(","));
  }
  if (colors.size < expected.minDistinctColors) {
    throw new Error(`WMS PNG has ${colors.size} distinct color(s), expected at least ${expected.minDistinctColors}`);
  }
  if (nonTransparentPixels < expected.minNonTransparentPixels) {
    throw new Error(`WMS PNG has ${nonTransparentPixels} non-transparent pixels, expected at least ${expected.minNonTransparentPixels}`);
  }
  return {
    width: header.width,
    height: header.height,
    colorType: header.colorType,
    distinctColors: colors.size,
    nonTransparentPixels,
    totalPixels,
  };
}

function paeth(left, up, upLeft) {
  const estimate = left + up - upLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const diagonalDistance = Math.abs(estimate - upLeft);
  if (leftDistance <= upDistance && leftDistance <= diagonalDistance) return left;
  if (upDistance <= diagonalDistance) return up;
  return upLeft;
}

async function probeLanding(results) {
  const response = await probe(results, "environment-landing", "/", { accept: "text/html" }, [200]);
  if (!response.ok) return;
  const contentType = response.result.contentType ?? "";
  const html = response.body.toString("utf8");
  const requiredFragments = [
    "<!doctype html>", "<main>", "<h1>Honua demo environment</h1>",
    'href="/healthz/live"', 'href="/healthz/ready"',
    'href="/api/v1/capabilities/manifest"', 'href="/demo-services.v1.json"',
    'href="/stac"', 'href="https://samples.honua.io/"',
  ];
  const missing = requiredFragments.filter((fragment) => !html.includes(fragment));
  if (!contentType.toLowerCase().startsWith("text/html")) {
    missing.unshift(`content-type text/html (received ${contentType || "none"})`);
  }
  if (missing.length > 0) failResult(response.result, `landing contract missing: ${missing.join(", ")}`);
}

async function probeJson(results, name, urlPath) {
  const response = await probe(results, name, urlPath, { accept: "application/json" }, [200]);
  if (!response.ok) return {};
  try {
    return JSON.parse(response.body.toString("utf8"));
  } catch (error) {
    failResult(response.result, `invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
    return {};
  }
}

async function probeStacFeatureCollection(
  results,
  name,
  urlPath,
  collectionId,
  limit,
  requestOptions = {},
) {
  const response = await probe(
    results,
    name,
    urlPath,
    {
      accept: "application/geo+json,application/json",
      ...(requestOptions.body ? { "content-type": "application/json" } : {}),
    },
    [200],
    requestOptions,
  );
  if (!response.ok) return {};

  let payload;
  try {
    payload = JSON.parse(response.body.toString("utf8"));
  } catch (error) {
    failResult(response.result, `invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
    return {};
  }

  const features = payload.type === "FeatureCollection" && Array.isArray(payload.features)
    ? payload.features
    : null;
  if (!features) {
    failResult(response.result, "expected a STAC FeatureCollection");
    return payload;
  }
  if (features.length === 0) {
    failResult(response.result, `expected a non-empty result for STAC collection ${collectionId}`);
    return payload;
  }
  if (features.length > limit) {
    failResult(response.result, `expected at most ${limit} STAC features (received ${features.length})`);
    return payload;
  }

  const invalidFeature = features.find((feature) =>
    typeof feature?.id !== "string" || feature.id.length === 0 || feature.collection !== collectionId);
  if (invalidFeature) {
    failResult(response.result, `STAC result is not identity-bound to collection ${collectionId}`);
    return payload;
  }

  response.result.semantic = {
    collectionId,
    featureCount: features.length,
    itemIds: features.map((feature) => feature.id),
  };
  return payload;
}

async function probeText(results, name, urlPath) { return probe(results, name, urlPath, {}, [200]); }
async function probeBinary(results, name, urlPath) { return probe(results, name, urlPath, {}, [200]); }
async function probeRange(results, name, urlPath) { return probe(results, name, urlPath, { range: "bytes=0-126" }, [206]); }

async function probe(results, name, urlPath, headers, expectedStatuses, requestOptions = {}) {
  const started = performance.now();
  const correlationId = `live-canary-${randomUUID()}`;
  const result = {
    name,
    method: requestOptions.method ?? "GET",
    url: `${baseUrl}${urlPath}`,
    correlationId,
    passed: false,
    status: 0,
    latencyMs: 0,
    bytes: 0,
  };
  results.push(result);
  try {
    const response = await fetch(result.url, {
      ...requestOptions,
      headers: { ...(requestOptions.headers ?? {}), ...headers, "x-correlation-id": correlationId },
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = Buffer.from(await response.arrayBuffer());
    result.status = response.status;
    result.latencyMs = Math.round(performance.now() - started);
    result.bytes = body.byteLength;
    result.contentType = response.headers.get("content-type");
    result.sha256 = createHash("sha256").update(body).digest("hex");
    result.passed = expectedStatuses.includes(response.status) && body.byteLength > 0;
    if (!result.passed) result.error = `expected HTTP ${expectedStatuses.join(" or ")} with a non-empty body`;
    return { ok: result.passed, body, result };
  } catch (error) {
    result.latencyMs = Math.round(performance.now() - started);
    result.error = error instanceof Error ? error.message : String(error);
    return { ok: false, body: Buffer.alloc(0), result };
  }
}

function failResult(result, error) { result.passed = false; result.error = error; }

async function writeFatal(error) {
  await mkdir(path.dirname(evidencePath), { recursive: true });
  const summary = {
    total: results.length,
    passed: results.filter((result) => result.passed).length,
    failed: results.filter((result) => !result.passed).length,
  };
  await writeFile(
    evidencePath,
    `${JSON.stringify({ format: "honua.demo.live-canary.v1", generatedAt: new Date().toISOString(), baseUrl, fatal: error instanceof Error ? error.message : String(error), summary, results }, null, 2)}\n`,
    "utf8",
  );
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) main().catch(writeFatal);
