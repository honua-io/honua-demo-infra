#!/usr/bin/env python3
"""Generate manifest/demo-services.v1.json from the demo seed definitions.

The manifest is NEVER hand-maintained (honua-demo-infra#19). It is derived from the
two seed sources of truth this repo already has:

  1. stacks/aws/SEED_MANIFEST.md — the Maui Nui seed-data manifest. Its three
     machine-parsed tables (vector layers, raster layers, basemap + glyphs)
     define every publicly discoverable demo service: service ids,
     FeatureServer layer ids, tile routes, PMTiles archive, glyph route,
     source attribution, and licenses.
  2. honua-server tests/seed/demo-stac-imagery-v1.sql at the pinned ref the
     repo README documents (the "Seed data" section's raw URL — schema-coupled
     seed SQL stays in honua-server and is referenced by pinned ref, never
     vendored here). It defines the STAC service and its two collections.

Only publicly discoverable surface goes in: everything emitted is reachable
anonymously through demo.honua.io capability endpoints. No credentials, no
AWS resource ids, no infra internals. Deliberate exclusions (see
manifest/README.md): `test_service` (allowAnonymous=false since 2026-07-23),
the honua-site demo-page static PMTiles archives (`maui-*-static`, defined in
honua-site, not by this repo's seeds), and the internal raster availability
stubs (`maui_*_meta`).

Determinism: no timestamps, stable seed-table ordering, fixed JSON formatting
— so the committed file is byte-for-byte reproducible and the sdk-js consumer
(honua-sdk-js#825) can vendor and drift-check it.

Usage:
  python3 manifest/generate-demo-services.py            # (re)write the manifest
  python3 manifest/generate-demo-services.py --check    # fail if committed file is stale
  python3 manifest/generate-demo-services.py --stac-sql /path/to/demo-stac-imagery-v1.sql
                                                        # offline override for the pinned fetch
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_MANIFEST = REPO_ROOT / "stacks" / "aws" / "SEED_MANIFEST.md"
REPO_README = REPO_ROOT / "README.md"
OUTPUT = Path(__file__).resolve().parent / "demo-services.v1.json"
WMS_RELEASE = Path(__file__).resolve().parent / "wms-release.v1.json"

BASE_URL = "https://demo.honua.io"
PUBLISH_URL = f"{BASE_URL}/demo-services.v1.json"

# Endpoint shapes for the seeded services. These mirror the honua-server
# endpoint registry and the as-built demo page contract (honua-site
# assets/demo/layers.json): every vector seed is published with
# service name == FeatureServer layer name == OGC collection id, MVT via OGC
# API Tiles on WebMercatorQuad with the constant PostGIS ST_AsMVT source-layer
# "layer" (SEED_MANIFEST.md, "Vector layers" section).
OGC_FEATURES_COLLECTION = "/ogc/features/collections/{id}"
OGC_TILES_TEMPLATE = (
    "/ogc/tiles/collections/{id}/tiles/WebMercatorQuad/{{z}}/{{y}}/{{x}}"
)
MVT_SOURCE_LAYER = "layer"

STAC_SEED_FILE = "tests/seed/demo-stac-imagery-v1.sql"

WMS_SERVICE_IDS = {"maui-flood-hazard", "maui-sea-level-rise"}


def fail(msg: str) -> "SystemExit":
    return SystemExit(f"generate-demo-services: error: {msg}")


# ---------------------------------------------------------------------------
# SEED_MANIFEST.md table parsing
# ---------------------------------------------------------------------------

def parse_tables(markdown: str) -> dict[str, list[list[str]]]:
    """Return {section heading: table rows} for every '## ' section with a table."""
    tables: dict[str, list[list[str]]] = {}
    heading = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue  # separator row
        tables.setdefault(heading, []).append(cells)
    return tables


def clean(cell: str) -> str:
    """Strip markdown emphasis/backticks from a table cell."""
    return cell.replace("**", "").replace("`", "").strip()


def first_int(cell: str) -> int:
    m = re.search(r"[\d,]+", cell)
    if not m:
        raise fail(f"no feature count found in cell: {cell!r}")
    return int(m.group(0).replace(",", ""))


def vector_services(rows: list[list[str]]) -> list[dict]:
    services = []
    for row in rows:
        if len(row) != 6 or "/FeatureServer/" not in row[1]:
            continue  # header row
        service_id = clean(row[0])
        fs_path = clean(row[1])
        m = re.fullmatch(r"/rest/services/([^/]+)/FeatureServer/(\d+)", fs_path)
        if not m or m.group(1) != service_id:
            raise fail(f"unexpected FeatureServer path for {service_id!r}: {fs_path!r}")
        services.append(
            {
                "id": service_id,
                "type": "feature",
                "layerName": service_id,
                "featureCount": first_int(row[2]),
                "protocols": {
                    "featureServer": {
                        "layerId": int(m.group(2)),
                        "path": fs_path,
                    },
                    "ogcFeatures": {
                        "collectionId": service_id,
                        "path": OGC_FEATURES_COLLECTION.format(id=service_id),
                    },
                    "ogcTiles": {
                        "collectionId": service_id,
                        "tileMatrixSetId": "WebMercatorQuad",
                        "tileTemplate": OGC_TILES_TEMPLATE.format(id=service_id),
                        "mvtSourceLayer": MVT_SOURCE_LAYER,
                    },
                },
                "source": clean(row[3]),
                "vintage": clean(row[4]),
                "license": clean(row[5]),
            }
        )
    if not services:
        raise fail("no vector services parsed from SEED_MANIFEST.md")
    return services


def raster_services(rows: list[list[str]]) -> list[dict]:
    services = []
    for row in rows:
        if len(row) != 5:
            continue
        route = clean(row[1]).split()[0] if clean(row[1]) else ""
        service_id = clean(row[0])
        if route.startswith("/terrain/"):
            base = route.rsplit("/", 3)[0]  # /terrain/{id}
            protocols = {
                "terrainRgb": {
                    "tileTemplate": route,
                    "tileJson": f"{base}/tile.json",
                    "encoding": "mapbox-terrain-rgb",
                }
            }
            svc_type = "terrain"
        elif "/ImageServer/tile/" in route:
            protocols = {"imageServerTiles": {"tileTemplate": route}}
            svc_type = "raster"
        else:
            continue  # header row
        services.append(
            {
                "id": service_id,
                "type": svc_type,
                "protocols": protocols,
                "source": clean(row[2]),
                "license": clean(row[4]),
            }
        )
    if not services:
        raise fail("no raster services parsed from SEED_MANIFEST.md")
    return services


def basemap_and_glyphs(rows: list[list[str]]) -> tuple[list[dict], dict]:
    services: list[dict] = []
    assets: dict = {}
    for row in rows:
        if len(row) != 4:
            continue
        key = clean(row[1]).split()[0]
        serving = row[3]
        pmtiles = re.search(r"/api/v1/tiles/pmtiles/(\S+)", serving)
        if pmtiles:
            license_text = clean(serving.split("—")[-1]) if "—" in serving else ""
            services.append(
                {
                    "id": key,
                    "type": "basemap",
                    "protocols": {
                        "pmtiles": {
                            "archiveId": pmtiles.group(1),
                            "path": f"/api/v1/tiles/pmtiles/{pmtiles.group(1)}",
                        }
                    },
                    "source": clean(row[2]),
                    "license": license_text,
                }
            )
        elif key.startswith("fonts/"):
            fontstack = key.split("/")[1]
            assets["glyphs"] = {
                "path": "/fonts/{fontstack}/{range}.pbf",
                "fontstacks": [fontstack],
                "source": clean(row[2]),
            }
    if not services or "glyphs" not in assets:
        raise fail("basemap/glyph rows not parsed from SEED_MANIFEST.md")
    return services, assets


# ---------------------------------------------------------------------------
# Pinned honua-server STAC seed (tests/seed/demo-stac-imagery-v1.sql)
# ---------------------------------------------------------------------------

def pinned_stac_seed_url() -> str:
    """The pinned ref lives in one place: the repo README's 'Seed data' section."""
    readme = REPO_README.read_text(encoding="utf-8")
    m = re.search(
        r"https://raw\.githubusercontent\.com/honua-io/honua-server/"
        r"([0-9a-f]{40})/" + re.escape(STAC_SEED_FILE),
        readme,
    )
    if not m:
        raise fail(
            f"pinned raw URL for {STAC_SEED_FILE} not found in README.md "
            "(Seed data section) — the generator reads the pinned ref from there"
        )
    return m.group(0)


def fetch_stac_seed(sql_path: str | None) -> tuple[str, str]:
    if sql_path:
        return Path(sql_path).read_text(encoding="utf-8"), pinned_stac_seed_url()
    url = pinned_stac_seed_url()
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - pinned public raw URL
            return resp.read().decode("utf-8"), url
    except OSError as exc:
        raise fail(
            f"could not fetch pinned STAC seed SQL from {url}: {exc}. "
            "Offline? Pass --stac-sql <local copy of the pinned file>."
        )


def stac_service(sql: str) -> dict:
    svc = re.search(
        r"'id','svc-demo-stac','name','([^']+)',\s*'title','([^']+)'", sql
    )
    route = re.search(r"'route','(/[^']*)'", sql)
    if not svc or not route:
        raise fail("STAC service metadata not found in the pinned seed SQL")

    collections = []
    matches = list(
        re.finditer(r"'id','res-demo-stac-(\d+)','name','([^']+)',", sql)
    )
    if not matches:
        raise fail("no STAC collection resources found in the pinned seed SQL")
    for i, m in enumerate(matches):
        block = sql[m.start(): matches[i + 1].start() if i + 1 < len(matches) else len(sql)]
        title = re.search(r"'title','([^']+)'", block)
        desc = re.search(r"'description','([^']+)'", block)
        lic = re.search(r"'license','([^']+)'", block)
        bbox = re.search(
            r"'bbox', jsonb_build_object\('west',(-?[\d.]+),'south',(-?[\d.]+),"
            r"'east',(-?[\d.]+),'north',(-?[\d.]+)\)",
            block,
        )
        if not (title and desc and lic and bbox):
            raise fail(f"incomplete metadata for STAC collection {m.group(1)}")
        collections.append(
            {
                "id": m.group(1),
                "name": m.group(2),
                "title": title.group(1),
                "description": desc.group(1),
                "license": lic.group(1),
                "bbox": [float(v) for v in bbox.groups()],
                "path": f"/stac/collections/{m.group(1)}",
            }
        )

    return {
        "id": svc.group(1),
        "type": "stac",
        "title": svc.group(2),
        "protocols": {
            "stac": {
                "path": route.group(1),
                "collectionsPath": "/stac/collections",
                "searchPath": "/stac/search",
                "collections": collections,
            }
        },
    }


# ---------------------------------------------------------------------------
# Planned WMS release contract
# ---------------------------------------------------------------------------

def load_wms_release() -> tuple[dict, str]:
    raw = WMS_RELEASE.read_bytes()
    definition = json.loads(raw)
    if definition.get("format") != "honua.demo.wms-release.v1":
        raise fail("unexpected WMS release format")
    if definition.get("schemaVersion") != "1.0.0":
        raise fail("unexpected WMS release schemaVersion")

    admission = definition.get("admission", {})
    status = admission.get("status")
    if status not in {"planned", "live"}:
        raise fail("WMS admission.status must be planned or live")

    server = definition.get("serverImage", {})
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", server.get("digest", "")):
        raise fail("WMS server image must use an immutable sha256 digest")
    if not re.fullmatch(r"[0-9a-f]{40}", server.get("sourceCommit", "")):
        raise fail("WMS server sourceCommit must be a full commit")
    if server.get("platform") != {"os": "linux", "architecture": "arm64"}:
        raise fail("WMS release is admitted only for linux/arm64")

    fix = definition.get("requiredFix", {})
    if not re.fullmatch(r"[0-9a-f]{40}", fix.get("mergeCommit", "")):
        raise fail("WMS requiredFix.mergeCommit must be a full commit")
    if fix.get("isAncestorOfServerCommit") is not True:
        raise fail("WMS release must carry a positive fix ancestry proof")

    bindings = definition.get("bindings", [])
    if {binding.get("serviceId") for binding in bindings} != WMS_SERVICE_IDS:
        raise fail("WMS release must bind exactly the two governed Maui hazard services")
    for binding in bindings:
        service_id = binding["serviceId"]
        wms = binding.get("wms", {})
        expected_path = f"/rest/services/{service_id}/MapServer/WMS"
        if wms.get("path") != expected_path or wms.get("layerName") != service_id:
            raise fail(f"unexpected WMS path/layer binding for {service_id}")
        expected_map = wms.get("expectedMap", {})
        if expected_map.get("width") != 512 or expected_map.get("height") != 512:
            raise fail(f"WMS canary map must remain bounded at 512x512 for {service_id}")
        governance = binding.get("governance", {})
        if governance.get("status") not in {"blocked", "approved"}:
            raise fail(f"WMS governance status is invalid for {service_id}")
        for evidence in governance.get("evidence", []):
            if not evidence.get("url", "").startswith("https://"):
                raise fail(f"WMS governance evidence must use HTTPS for {service_id}")
            if not re.fullmatch(r"[0-9a-f]{64}", evidence.get("sha256", "")):
                raise fail(f"WMS governance evidence needs a sha256 digest for {service_id}")
        if status == "live" and governance.get("status") != "approved":
            raise fail(f"cannot admit live WMS with blocked governance for {service_id}")

    return definition, hashlib.sha256(raw).hexdigest()


def public_wms_release(definition: dict, definition_sha256: str) -> tuple[dict, list[dict]]:
    server = definition["serverImage"]
    fix = definition["requiredFix"]
    release = {
        "status": definition["admission"]["status"],
        "definition": "manifest/wms-release.v1.json",
        "definitionSha256": definition_sha256,
        "requiredServer": {
            "imageDigest": server["digest"],
            "sourceCommit": server["sourceCommit"],
            "platform": server["platform"],
            "requiredFix": {
                "pullRequest": fix["pullRequest"],
                "mergeCommit": fix["mergeCommit"],
                "isAncestorOfServerCommit": fix["isAncestorOfServerCommit"],
            },
        },
        "requiredChecks": definition["admission"]["requiredChecks"],
    }
    bindings = json.loads(json.dumps(definition["bindings"]))
    return release, bindings


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_manifest(stac_sql_path: str | None) -> dict:
    tables = parse_tables(SEED_MANIFEST.read_text(encoding="utf-8"))

    def section(prefix: str) -> list[list[str]]:
        for heading, rows in tables.items():
            if heading.startswith(prefix):
                return rows
        raise fail(f"section {prefix!r} not found in SEED_MANIFEST.md")

    vectors = vector_services(section("Vector layers"))
    rasters = raster_services(section("Raster layers"))
    basemaps, assets = basemap_and_glyphs(section("Basemap + glyphs"))
    stac_sql, stac_url = fetch_stac_seed(stac_sql_path)
    stac = stac_service(stac_sql)

    services = vectors + rasters + basemaps + [stac]
    wms_definition, wms_definition_sha256 = load_wms_release()
    wms_release, wms_bindings = public_wms_release(
        wms_definition, wms_definition_sha256
    )
    if wms_release["status"] == "live":
        services_by_id = {service["id"]: service for service in services}
        for binding in wms_bindings:
            service = services_by_id.get(binding["serviceId"])
            if service is None:
                raise fail(f"WMS service {binding['serviceId']!r} is not in the seed manifest")
            service["protocols"]["wms"] = {
                **binding["wms"],
                "governance": binding["governance"],
            }

    manifest = {
        "format": "honua.demo-services.v1",
        "schemaVersion": "1.1.0",
        "description": (
            "Publicly discoverable seeded services of the demo.honua.io demo "
            "environment. Generated from seed definitions by "
            "manifest/generate-demo-services.py (honua-demo-infra#19) — never edit "
            "by hand."
        ),
        "baseUrl": BASE_URL,
        "publishUrl": PUBLISH_URL,
        "sources": {
            "seedManifest": "stacks/aws/SEED_MANIFEST.md",
            "stacSeed": stac_url,
            "wmsRelease": "manifest/wms-release.v1.json",
        },
        "releaseContracts": {"wms": wms_release},
        "services": services,
        "assets": assets,
    }
    if wms_release["status"] == "planned":
        manifest["releaseCandidates"] = {
            "wms": {
                "status": "planned",
                "bindings": wms_bindings,
            }
        }
    return manifest


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail (exit 1) if the committed manifest is stale")
    parser.add_argument("--stac-sql", metavar="PATH", default=None,
                        help="local copy of the pinned demo-stac-imagery-v1.sql "
                             "(offline override for the raw.githubusercontent fetch)")
    args = parser.parse_args()

    generated = render(build_manifest(args.stac_sql))

    if args.check:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if committed == generated:
            print(f"OK: {OUTPUT.relative_to(REPO_ROOT)} matches the seed definitions")
            return 0
        sys.stderr.write(
            f"DRIFT: {OUTPUT.relative_to(REPO_ROOT)} is stale relative to the seed "
            "definitions. Regenerate with:\n  python3 manifest/generate-demo-services.py\n\n"
        )
        sys.stderr.writelines(
            difflib.unified_diff(
                committed.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile="committed", tofile="generated",
            )
        )
        return 1

    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
