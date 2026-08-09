#!/usr/bin/env python3
"""Generate and validate the protected client-compat descriptor."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = REPO_ROOT / "stacks" / "aws" / "client-compat-seed.v1.json"
SEED_MANIFEST = REPO_ROOT / "stacks" / "aws" / "SEED_MANIFEST.md"
SEED_SCRIPT = REPO_ROOT / "stacks" / "aws" / "scripts" / "seed-test-service.sh"
PUBLIC_MANIFEST = REPO_ROOT / "manifest" / "demo-services.v1.json"
OUTPUT = REPO_ROOT / "manifest" / "client-compat.v1.json"

EXPECTED_FIELDS = [
    ("objectid", "bigint", "integer"),
    ("name", "text", "string"),
    ("description", "text", "string"),
    ("status", "text", "string"),
    ("count", "integer", "integer"),
    ("ratio", "double precision", "number"),
    ("uid", "text", "uuid"),
    ("active", "boolean", "boolean"),
]


def fail(message: str) -> "SystemExit":
    return SystemExit(f"generate-client-compat: error: {message}")


def validate(definition: dict) -> None:
    if definition.get("format") != "honua.demo.client-compat-seed.v1":
        raise fail("unexpected seed format")
    if definition.get("schemaVersion") != "1.0.0":
        raise fail("unexpected seed schemaVersion")

    deployment = definition.get("deployment", {})
    if deployment.get("serviceName") != "test_service":
        raise fail("serviceName must remain test_service")
    if deployment.get("accessPolicy") != {"allowAnonymous": False}:
        raise fail("client-compat fixture must remain allowAnonymous=false")
    if not isinstance(deployment.get("layerId"), int):
        raise fail("deployment.layerId must be the governed integer binding")

    fixture = definition.get("fixture", {})
    if fixture.get("expectedFeatureCount") != 10:
        raise fail("client-compat fixture must retain exactly 10 features")
    actual_fields = [
        (field.get("name"), field.get("sourceType"), field.get("semanticType"))
        for field in fixture.get("fields", [])
    ]
    if actual_fields != EXPECTED_FIELDS:
        raise fail(f"typed fixture contract changed: {actual_fields!r}")

    section_match = re.search(
        r"## SDK client-compat target.*?(?=\n## |\Z)",
        SEED_MANIFEST.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    if not section_match:
        raise fail("SDK client-compat section missing from SEED_MANIFEST.md")
    section = section_match.group(0)
    if "allowAnonymous: false" not in section or "allowAnonymous: true" in section:
        raise fail("SEED_MANIFEST client-compat policy must say allowAnonymous: false only")

    script = SEED_SCRIPT.read_text(encoding="utf-8")
    if "client-compat-seed.v1.json" not in script or '"allowAnonymous":true' in script:
        raise fail("seed script must consume the definition and never enable anonymous access")
    for field, _, _ in EXPECTED_FIELDS:
        if field not in script:
            raise fail(f"seed script no longer contains required field {field}")

    public_services = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8")).get("services", [])
    if any(service.get("id") == deployment["serviceName"] for service in public_services):
        raise fail("protected test_service must not appear in demo-services.v1.json")


def build(definition: dict) -> dict:
    deployment = definition["deployment"]
    storage = definition["storage"]
    fixture = definition["fixture"]
    layer_id = deployment["layerId"]
    service = deployment["serviceName"]
    seed_bytes = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    return {
        "format": "honua.demo.client-compat.v1",
        "schemaVersion": "1.0.0",
        "supportClaim": (
            "Protected SDK certification fixture. This descriptor is not a public-demo "
            "inventory entry and is not GA evidence without a fresh canary receipt."
        ),
        "baseUrl": deployment["baseUrl"],
        "service": {
            "name": service,
            "layerId": layer_id,
            "layerName": deployment["layerName"],
            "featureServerMetadataPath": f"/rest/services/{service}/FeatureServer/{layer_id}",
            "featureServerQueryPath": f"/rest/services/{service}/FeatureServer/{layer_id}/query",
        },
        "access": {
            "allowAnonymous": False,
            "apiKeyHeader": "X-API-Key",
            "canarySecret": "HONUA_CLIENT_COMPAT_API_KEY",
        },
        "fixture": {
            "profile": definition["fixtureProfile"],
            "expectedFeatureCount": fixture["expectedFeatureCount"],
            "primaryKey": storage["primaryKey"],
            "geometry": {
                "column": storage["geometryColumn"],
                "type": storage["geometryType"],
                "srid": storage["srid"],
            },
            "fields": fixture["fields"],
        },
        "consumerBinding": {
            "repository": "honua-io/honua-sdk-python",
            "environment": "staging",
            "variables": {
                "HONUA_BASE_URL": deployment["baseUrl"],
                "HONUA_SERVICE_ID": service,
                "HONUA_LAYER_ID": str(layer_id),
                "HONUA_SEED_PROFILE": definition["fixtureProfile"],
                "HONUA_SERVER_COMMIT": "required external rotation value",
                "HONUA_SERVER_IMAGE": "required immutable @sha256 external rotation value",
                "HONUA_API_KEY": "secret; never stored in this descriptor",
            },
        },
        "canaryBinding": {
            "serverCommitVariable": "HONUA_CLIENT_COMPAT_SERVER_COMMIT",
            "serverImageVariable": "HONUA_CLIENT_COMPAT_SERVER_IMAGE",
            "requiredAnonymousOutcome": "HTTP 401, 403, or 499",
            "authenticatedChecks": ["metadata field contract", "10-row typed query contract"],
        },
        "provenance": {
            "seedDefinition": "stacks/aws/client-compat-seed.v1.json",
            "seedDefinitionSha256": hashlib.sha256(seed_bytes).hexdigest(),
            "generator": "manifest/generate-client-compat.py",
            "issue": "https://github.com/honua-io/honua-demo-infra/issues/28",
        },
        "containsSecrets": False,
    }


def render(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    definition = json.loads(SEED.read_text(encoding="utf-8"))
    validate(definition)
    generated = render(build(definition))

    if args.check:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if committed == generated:
            print(f"OK: {OUTPUT.relative_to(REPO_ROOT)} matches the protected seed definition")
            return 0
        sys.stderr.write(f"DRIFT: {OUTPUT.relative_to(REPO_ROOT)} is stale\n")
        sys.stderr.writelines(
            difflib.unified_diff(
                committed.splitlines(keepends=True), generated.splitlines(keepends=True),
                fromfile="committed", tofile="generated",
            )
        )
        return 1

    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
