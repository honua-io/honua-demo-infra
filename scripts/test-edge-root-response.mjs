// Contract test for the CloudFront viewer-request function defined in
// stacks/aws/cloudfront.tf (aws_cloudfront_function.forwarded_host).
//
// `GET /` on demo.honua.io is generated at the edge by that function — it
// never reaches the origin, so it does not inherit honua-server's security
// headers, and the default cache behavior attaches no response-headers
// policy. That is exactly how the root shipped without HSTS and COOP
// (honua-release#87). This test extracts the function body straight out of
// the Terraform heredoc, runs it, and pins the generated response so the
// header set cannot silently regress again.
//
// No AWS credentials, no terraform, no network: `node --test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const cloudfrontTf = fileURLToPath(new URL("../stacks/aws/cloudfront.tf", import.meta.url));

// The origin's own baseline, verified against
// `curl -sI https://demo.honua.io/rest/services`. The edge-generated root
// must not drift below it.
const ORIGIN_BASELINE = {
  "strict-transport-security": "max-age=63072000; includeSubDomains; preload",
  "cross-origin-opener-policy": "same-origin",
  "x-frame-options": "DENY",
  "x-content-type-options": "nosniff",
};

function loadHandler() {
  const source = readFileSync(cloudfrontTf, "utf8");
  const resourceAt = source.indexOf('resource "aws_cloudfront_function" "forwarded_host" {');
  assert.notEqual(resourceAt, -1, "aws_cloudfront_function.forwarded_host not found in cloudfront.tf");

  const heredoc = /\n\s*code\s*=\s*<<-EOT\n([\s\S]*?)\n\s*EOT\n/u.exec(source.slice(resourceAt));
  assert.ok(heredoc, "forwarded_host function has no `code = <<-EOT` heredoc");

  const context = vm.createContext({});
  vm.runInContext(`${heredoc[1]}\n;handler`, context, { filename: "forwarded-host.js" });
  const handler = vm.runInContext("handler", context);
  assert.equal(typeof handler, "function", "heredoc did not define a `handler` function");
  return handler;
}

function edgeRequest(method, uri) {
  return { request: { method, uri, headers: {} } };
}

function headerValues(response) {
  return Object.fromEntries(Object.entries(response.headers).map(([name, header]) => [name, header.value]));
}

test("edge-generated root carries the origin's security baseline", () => {
  const handler = loadHandler();
  const response = handler(edgeRequest("GET", "/"));

  assert.equal(response.statusCode, 200);
  const headers = headerValues(response);

  for (const [name, value] of Object.entries(ORIGIN_BASELINE)) {
    assert.equal(headers[name], value, `root response header \`${name}\` must be \`${value}\``);
  }

  // Header names must stay lowercase — CloudFront Functions reject a
  // response whose header keys are not lowercase.
  for (const name of Object.keys(headers)) {
    assert.equal(name, name.toLowerCase(), `header \`${name}\` must be lowercase`);
  }
});

test("root keeps its existing content, caching, and framing controls", () => {
  const handler = loadHandler();
  const headers = headerValues(handler(edgeRequest("GET", "/")));

  assert.equal(headers["content-type"], "text/html; charset=utf-8");
  assert.equal(headers["cache-control"], "no-store");
  assert.equal(headers["referrer-policy"], "no-referrer");
  assert.match(headers["content-security-policy"], /(^|;\s*)default-src 'none'/u);
  // frame-ancestors, not x-frame-options, is the control that actually stops
  // framing in modern agents — keep both.
  assert.match(headers["content-security-policy"], /frame-ancestors 'none'/u);
});

test("HEAD / answers with the same headers and no body", () => {
  const handler = loadHandler();
  const response = handler(edgeRequest("HEAD", "/"));

  assert.equal(response.statusCode, 200);
  assert.equal(response.body, undefined, "HEAD must not carry a body");
  assert.deepEqual(headerValues(response), headerValues(handler(edgeRequest("GET", "/"))));
});

test("GET / still serves the environment landing document", () => {
  const handler = loadHandler();
  const body = handler(edgeRequest("GET", "/")).body;

  assert.equal(body.encoding, "text");
  for (const fragment of [
    "<!doctype html>",
    "<h1>Honua demo environment</h1>",
    'href="/healthz/live"',
    'href="/demo-services.v1.json"',
    'href="https://samples.honua.io/"',
  ]) {
    assert.ok(body.data.includes(fragment), `landing document lost \`${fragment}\``);
  }
});

test("GET /mcp is still short-circuited to 405 at the edge", () => {
  const handler = loadHandler();
  const response = handler(edgeRequest("GET", "/mcp"));

  assert.equal(response.statusCode, 405);
  assert.equal(response.headers.allow.value, "POST, DELETE");
});

test("every other request passes through with the public forwarded host", () => {
  const handler = loadHandler();

  for (const [method, uri] of [
    ["GET", "/rest/services"],
    ["POST", "/mcp"],
    ["HEAD", "/mcp"],
    ["GET", "/healthz/live"],
  ]) {
    const request = handler(edgeRequest(method, uri));
    assert.equal(request.statusCode, undefined, `${method} ${uri} must reach the origin, not be generated at the edge`);
    assert.equal(request.headers["x-forwarded-host"].value, "demo.honua.io");
  }
});
