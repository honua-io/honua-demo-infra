// Contract test for the /demo-data/* viewer-request function in
// stacks/aws/static-data.tf (aws_cloudfront_function.static_data_preflight):
// CORS preflights are answered at the edge, every other request goes to S3
// untouched. Runs the heredoc straight out of the Terraform source.
//
// No AWS credentials, no terraform, no network: `node --test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const staticDataTf = readFileSync(new URL("../stacks/aws/static-data.tf", import.meta.url), "utf8");
const cloudfrontTf = readFileSync(new URL("../stacks/aws/cloudfront.tf", import.meta.url), "utf8");

function corsOrigins() {
  const list = /\n\s*tile_cors_origins\s*=\s*\[([^\]]*)\]/u.exec(cloudfrontTf);
  assert.ok(list, "local.tile_cors_origins not found in cloudfront.tf");
  return [...list[1].matchAll(/"([^"]*)"/gu)].map((match) => match[1]);
}

function loadHandler() {
  const resourceAt = staticDataTf.indexOf('resource "aws_cloudfront_function" "static_data_preflight" {');
  assert.notEqual(resourceAt, -1, "aws_cloudfront_function.static_data_preflight not found");
  const heredoc = /\n\s*code\s*=\s*<<-EOT\n([\s\S]*?)\n\s*EOT\n/u.exec(staticDataTf.slice(resourceAt));
  assert.ok(heredoc, "static_data_preflight has no `code = <<-EOT` heredoc");
  const code = heredoc[1].replace("${jsonencode(local.tile_cors_origins)}", JSON.stringify(corsOrigins()));
  assert.doesNotMatch(code, /\$\{/u, "unresolved Terraform interpolation left in the function body");
  const context = vm.createContext({});
  vm.runInContext(`${code}\n;handler`, context, { filename: "static-data-preflight.js" });
  return vm.runInContext("handler", context);
}

const handler = loadHandler();

test("a Range preflight from the samples site is answered at the edge", () => {
  const response = handler({
    request: {
      method: "OPTIONS",
      uri: "/demo-data/maui-island-display/2026-11-11/parcels.pmtiles",
      headers: {
        origin: { value: "https://samples.honua.io" },
        "access-control-request-method": { value: "GET" },
        "access-control-request-headers": { value: "range" },
      },
    },
  });
  assert.equal(response.statusCode, 204);
  assert.equal(response.headers["access-control-allow-origin"].value, "*");
  assert.match(response.headers["access-control-allow-headers"].value, /\bRange\b/u);
  assert.match(response.headers["access-control-allow-methods"].value, /\bGET\b/u);
});

test("reads pass through to S3 unchanged", () => {
  const request = { method: "GET", uri: "/demo-data/publications/x/parcels.parquet", headers: {} };
  assert.equal(handler({ request }), request);
});
