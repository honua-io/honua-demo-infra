import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./client-compat-canary.mjs", import.meta.url));
const apiKey = "test-only-key";

test("canary always proves denial and conditionally proves typed authentication", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "honua-client-compat-"));
  const server = http.createServer(handleRequest);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const common = {
    HONUA_DEMO_BASE_URL: `http://127.0.0.1:${port}`,
    HONUA_DEMO_TIMEOUT_MS: "2000",
  };

  try {
    const anonymousPath = path.join(temp, "anonymous.json");
    await runCanary({ ...common, HONUA_CLIENT_COMPAT_EVIDENCE_PATH: anonymousPath });
    const anonymous = JSON.parse(await readFile(anonymousPath, "utf8"));
    assert.deepEqual(anonymous.summary, { total: 1, passed: 1, failed: 0, skipped: 1 });
    assert.equal(anonymous.authentication.configured, false);

    const authenticatedPath = path.join(temp, "authenticated.json");
    await runCanary({
      ...common,
      HONUA_CLIENT_COMPAT_EVIDENCE_PATH: authenticatedPath,
      HONUA_CLIENT_COMPAT_API_KEY: apiKey,
      HONUA_CLIENT_COMPAT_SERVER_COMMIT: "0123456789abcdef0123456789abcdef01234567",
      HONUA_CLIENT_COMPAT_SERVER_IMAGE: `ghcr.io/honua-io/honua-server@sha256:${"a".repeat(64)}`,
    });
    const text = await readFile(authenticatedPath, "utf8");
    const authenticated = JSON.parse(text);
    assert.deepEqual(authenticated.summary, { total: 3, passed: 3, failed: 0, skipped: 0 });
    assert.equal(authenticated.authentication.credentialRecorded, false);
    assert.equal(text.includes(apiKey), false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(temp, { recursive: true, force: true });
  }
});

function handleRequest(request, response) {
  response.setHeader("content-type", "application/json");
  if (request.headers["x-api-key"] !== apiKey) {
    response.statusCode = 499;
    response.end(JSON.stringify({ error: { code: 499, message: "Unauthorized" } }));
    return;
  }
  if (request.url.includes("/query?")) {
    response.statusCode = 200;
    response.end(JSON.stringify({ features: fixtureFeatures() }));
    return;
  }
  response.statusCode = 200;
  response.end(JSON.stringify({
    fields: ["objectid", "name", "description", "status", "count", "ratio", "uid", "active"]
      .map((name) => ({ name })),
  }));
}

function fixtureFeatures() {
  return Array.from({ length: 10 }, (_, index) => ({
    attributes: {
      objectid: index + 1,
      name: `feature-${index + 1}`,
      description: index % 3 === 0 ? null : `description-${index + 1}`,
      status: index % 2 === 0 ? "active" : "inactive",
      count: index + 1,
      ratio: (index + 1) * 1.25,
      uid: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
      active: index % 2 === 0,
    },
  }));
}

function runCanary(extraEnv) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      env: { ...process.env, ...extraEnv },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`canary exited ${code}\n${stdout}\n${stderr}`));
    });
  });
}
