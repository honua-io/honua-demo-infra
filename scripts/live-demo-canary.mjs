#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const evidenceDir = path.join(repoRoot, ".artifacts", "live-demo-canary");
const evidencePath = path.join(evidenceDir, "live-demo-canary.v1.json");
const baseUrl = (process.env.HONUA_DEMO_BASE_URL ?? "https://demo.honua.io").replace(/\/$/u, "");
const timeoutMs = Number(process.env.HONUA_DEMO_TIMEOUT_MS ?? 20_000);

async function main() {
  const results = [];
  const manifest = await probeJson(results, "manifest", "/demo-services.v1.json");
  if (manifest.format !== "honua.demo-services.v1" || manifest.schemaVersion !== "1.0.0") {
    throw new Error(`unexpected manifest contract ${manifest.format}@${manifest.schemaVersion}`);
  }
  if (!Array.isArray(manifest.services) || manifest.services.length === 0) {
    throw new Error("published demo manifest contains no services");
  }

  await probeText(results, "readiness", "/healthz/ready");
  for (const service of manifest.services) {
    const protocols = service.protocols ?? {};
    if (protocols.featureServer) {
      await probeJson(results, `${service.id}:feature-server`, `${protocols.featureServer.path}?f=json`);
    }
    if (protocols.ogcFeatures) {
      await probeJson(results, `${service.id}:ogc-features`, protocols.ogcFeatures.path);
    }
    if (protocols.imageServerTiles) {
      // z/y/x 10/451/67 intersects central Maui. Probe the exact published
      // tile contract rather than deriving an undeclared metadata route.
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
    manifest: {
      format: manifest.format,
      schemaVersion: manifest.schemaVersion,
      serviceCount: manifest.services.length,
    },
    summary: {
      total: results.length,
      passed: results.filter((result) => result.passed).length,
      failed: results.filter((result) => !result.passed).length,
    },
    results,
  };
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(evidencePath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(receipt.summary)}\n`);
  for (const result of results) {
    process.stdout.write(`${result.passed ? "PASS" : "FAIL"} ${result.name} ${result.status} ${result.latencyMs}ms\n`);
    if (result.error) process.stdout.write(`  ${result.error}\n`);
  }
  if (receipt.summary.failed > 0) process.exitCode = 1;
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

async function probeText(results, name, urlPath) {
  return probe(results, name, urlPath, {}, [200]);
}

async function probeBinary(results, name, urlPath) {
  return probe(results, name, urlPath, {}, [200]);
}

async function probeRange(results, name, urlPath) {
  return probe(results, name, urlPath, { range: "bytes=0-126" }, [206]);
}

async function probe(results, name, urlPath, headers, expectedStatuses) {
  const started = performance.now();
  const result = { name, url: `${baseUrl}${urlPath}`, passed: false, status: 0, latencyMs: 0, bytes: 0 };
  results.push(result);
  try {
    const response = await fetch(result.url, { headers, signal: AbortSignal.timeout(timeoutMs) });
    const body = Buffer.from(await response.arrayBuffer());
    result.status = response.status;
    result.latencyMs = Math.round(performance.now() - started);
    result.bytes = body.byteLength;
    result.contentType = response.headers.get("content-type");
    result.passed = expectedStatuses.includes(response.status) && body.byteLength > 0;
    if (!result.passed) result.error = `expected HTTP ${expectedStatuses.join(" or ")} with a non-empty body`;
    return { ok: result.passed, body, result };
  } catch (error) {
    result.latencyMs = Math.round(performance.now() - started);
    result.error = error instanceof Error ? error.message : String(error);
    return { ok: false, body: Buffer.alloc(0), result };
  }
}

function failResult(result, error) {
  result.passed = false;
  result.error = error;
}

main().catch(async (error) => {
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(
    evidencePath,
    `${JSON.stringify({ format: "honua.demo.live-canary.v1", generatedAt: new Date().toISOString(), baseUrl, fatal: error instanceof Error ? error.message : String(error) }, null, 2)}\n`,
    "utf8",
  );
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
