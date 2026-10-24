// Contract test for the PMTiles viewer-request function in
// stacks/aws/cloudfront.tf (aws_cloudfront_function.pmtiles_rewrite).
//
// /api/v1/tiles/pmtiles/<id> is served from the demo data bucket through an
// S3 origin, not from honua-server. The function maps the public URL to the
// object key at the bucket root and refuses ids that are not in
// local.pmtiles_edge_archives. This test extracts the heredoc and the locals
// it interpolates straight out of the Terraform source and runs the handler.
//
// No AWS credentials, no terraform, no network: `node --test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const cloudfrontTf = fileURLToPath(new URL("../stacks/aws/cloudfront.tf", import.meta.url));
const source = readFileSync(cloudfrontTf, "utf8");

function hclLocal(name) {
  const scalar = new RegExp(`\\n\\s*${name}\\s*=\\s*"([^"]*)"`, "u").exec(source);
  if (scalar) return scalar[1];
  const list = new RegExp(`\\n\\s*${name}\\s*=\\s*\\[([\\s\\S]*?)\\]`, "u").exec(source);
  assert.ok(list, `local.${name} not found in cloudfront.tf`);
  return [...list[1].matchAll(/"([^"]*)"/gu)].map((m) => m[1]);
}

function loadHandler() {
  const resourceAt = source.indexOf('resource "aws_cloudfront_function" "pmtiles_rewrite" {');
  assert.notEqual(resourceAt, -1, "aws_cloudfront_function.pmtiles_rewrite not found in cloudfront.tf");
  const heredoc = /\n\s*code\s*=\s*<<-EOT\n([\s\S]*?)\n\s*EOT\n/u.exec(source.slice(resourceAt));
  assert.ok(heredoc, "pmtiles_rewrite function has no `code = <<-EOT` heredoc");
  const code = heredoc[1].replace(/\$\{jsonencode\(local\.([a-z_]+)\)\}/gu, (_, name) => JSON.stringify(hclLocal(name)));
  assert.doesNotMatch(code, /\$\{/u, "unresolved Terraform interpolation left in the function body");
  const context = vm.createContext({});
  vm.runInContext(`${code}\n;handler`, context, { filename: "pmtiles-rewrite.js" });
  return vm.runInContext("handler", context);
}

const handler = loadHandler();
const archives = hclLocal("pmtiles_edge_archives");

function run(method, uri, headers = {}) {
  return handler({ request: { method, uri, headers, querystring: { x: { value: "1" } } } });
}

test("every listed archive rewrites to its bucket-root key", () => {
  assert.ok(archives.includes("maui-basemap"));
  assert.equal(archives.length, 8);
  for (const id of archives) {
    const out = run("GET", `/api/v1/tiles/pmtiles/${id}`);
    assert.equal(out.uri, `/${id}`);
    assert.equal(Object.keys(out.querystring).length, 0);
    assert.equal(out.statusCode, undefined);
  }
});

test("HEAD is rewritten the same way", () => {
  assert.equal(run("HEAD", "/api/v1/tiles/pmtiles/maui-basemap").uri, "/maui-basemap");
});

test("unknown ids, traversal, and nested paths are 404 at the edge", () => {
  for (const uri of [
    "/api/v1/tiles/pmtiles/",
    "/api/v1/tiles/pmtiles/manifest/demo-services.v1.json",
    "/api/v1/tiles/pmtiles/../fonts/x",
    "/api/v1/tiles/pmtiles/maui-basemap/0/0/0",
    "/api/v1/tiles/pmtiles/imports/secret",
    "/api/v1/tiles/pmtiles/maui-basemap.pmtiles",
  ]) {
    const out = run("GET", uri);
    assert.equal(out.statusCode, 404, uri);
  }
});

test("preflight echoes only the tile CORS origins", () => {
  const ok = run("OPTIONS", "/api/v1/tiles/pmtiles/maui-basemap", { origin: { value: "https://honua.io" } });
  assert.equal(ok.statusCode, 204);
  assert.equal(ok.headers["access-control-allow-origin"].value, "https://honua.io");
  assert.match(ok.headers["access-control-allow-headers"].value, /Range/u);
  const no = run("OPTIONS", "/api/v1/tiles/pmtiles/maui-basemap", { origin: { value: "https://evil.example" } });
  assert.equal(no.statusCode, 204);
  assert.equal(no.headers["access-control-allow-origin"], undefined);
  for (const name of Object.keys(ok.headers)) assert.equal(name, name.toLowerCase());
});

test("edge CORS origins match the tile response-headers policy", () => {
  assert.deepEqual([...hclLocal("tile_cors_origins")], ["https://honua.io", "https://www.honua.io", "http://localhost:8123"]);
  assert.match(source, /access_control_allow_origins \{\n\s*items = local\.tile_cors_origins/u);
});

test("the S3 behavior precedes the generic /api/v1/tiles/* behavior", () => {
  const dist = source.slice(source.indexOf('resource "aws_cloudfront_distribution" "demo" {'));
  const pm = dist.indexOf("path_pattern     = local.pmtiles_path_pattern");
  const generic = dist.indexOf('dynamic "ordered_cache_behavior"');
  assert.ok(pm > 0 && generic > 0 && pm < generic, "PMTiles behavior must come first");
  assert.equal(hclLocal("pmtiles_path_pattern"), "/api/v1/tiles/pmtiles/*");
});
