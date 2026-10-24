#!/usr/bin/env bash
# Reset the Inspection & Editing scratch layer on demo.honua.io to its seed.
#
# The honua.io /demo-editing.html page lets anonymous visitors PATCH and POST
# synthetic inspection points over OData. This script restores the layer to
# the 27 seed points in maui-inspections.seed.geojson, which is a byte copy of
# honua-site assets/demos/editing/inspections.geojson (the same fixture the
# 2026-06-12 server seed was generated from; see
# runbook/editing-scratch-layer-anonymous-writes.md).
#
# It reconciles over the server's own OData API with the admin key:
#   - a row identical to an unclaimed seed point is kept (ObjectId stays stable);
#   - a row whose name matches an unclaimed seed point is PATCHed back to it;
#   - any other row (visitor additions) is DELETEd;
#   - a seed point with no row left is POSTed.
# A second run plans nothing (idempotent). DRY RUN BY DEFAULT: without --apply
# it only prints the plan. With --apply it executes the plan, re-reads the
# layer, and exits non-zero unless the layer equals the seed.
#
# It only ever touches the layer named $LAYER_NAME (default
# maui-inspections-scratch, the managed-store copy). It refuses a layer whose
# fields are not the seed's fields. The original maui-inspections layer is
# published storageMode=source, so the server answers OData writes on it with
# 501 and it never needs resetting.
#
# The admin X-API-Key is never hard-coded. It is read from HONUA_DEMO_API_KEY or,
# if unset, from AWS Secrets Manager (secret honua-demo-demo/admin-password),
# the same way demo-b-probes.sh does.
#
# Usage:
#   ./reset-inspections-scratch.sh                      # dry run against demo.honua.io
#   ./reset-inspections-scratch.sh --apply              # reset
#   BASE=http://localhost:8080 HONUA_DEMO_API_KEY=... ./reset-inspections-scratch.sh --apply
#
# Exit codes: 0 = in seed state (dry run: plan printed), 1 = verification or
# request failure, 2 = usage / guard refusal.
set -euo pipefail

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE="${BASE:-https://demo.honua.io}"
LAYER_NAME="${LAYER_NAME:-maui-inspections-scratch}"
SEED_FILE="${SEED_FILE:-${SCRIPT_DIR}/maui-inspections.seed.geojson}"
SECRET_ID="${HONUA_DEMO_SECRET_ID:-honua-demo-demo/admin-password}"

API_KEY="${HONUA_DEMO_API_KEY:-}"
if [ -z "$API_KEY" ]; then
  API_KEY="$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_ID" --query SecretString --output text 2>/dev/null || true)"
fi
if [ -z "$API_KEY" ] && [ "$APPLY" = "1" ]; then
  echo "no admin API key (set HONUA_DEMO_API_KEY or grant access to $SECRET_ID)" >&2
  exit 2
fi

export BASE LAYER_NAME SEED_FILE APPLY API_KEY
exec python3 -I - <<'PY'
import json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE = os.environ["BASE"].rstrip("/")
LAYER_NAME = os.environ["LAYER_NAME"]
APPLY = os.environ["APPLY"] == "1"
API_KEY = os.environ.get("API_KEY", "")
FIELDS = ["name", "category", "status", "note", "reported_at"]


def request(method, path_or_url, body=None):
    url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
    headers = {"Accept": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw) if raw else None
        except ValueError:
            return err.code, raw.decode("utf-8", "replace")[:300]


def reroot(link):
    # The server emits nextLink with its internal host behind the Lambda.
    parts = urllib.parse.urlsplit(link)
    return BASE + parts.path + ("?" + parts.query if parts.query else "")


def norm_time(value):
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return text


def norm_coords(coords):
    return [round(float(c), 7) for c in (coords or [])]


def key_of(attrs, coords):
    return (
        attrs.get("name"), attrs.get("category"), attrs.get("status"), attrs.get("note"),
        norm_time(attrs.get("reported_at")), tuple(norm_coords(coords)),
    )


# ── seed ──────────────────────────────────────────────────────────────
seed_doc = json.load(open(os.environ["SEED_FILE"], encoding="utf-8"))
seed = []
for feature in seed_doc["features"]:
    attrs = {k: feature["properties"].get(k) for k in FIELDS}
    seed.append({"attrs": attrs, "coords": feature["geometry"]["coordinates"]})
if len(seed) != 27 or len({s["attrs"]["name"] for s in seed}) != 27:
    sys.exit("seed file must hold 27 uniquely named inspection points")

# ── layer ─────────────────────────────────────────────────────────────
status, layers = request("GET", "/odata/Layers")
if status != 200:
    sys.exit(f"GET /odata/Layers -> {status}: {layers}")
match = [row for row in layers.get("value", []) if row.get("Name") == LAYER_NAME]
if not match:
    print(f"layer {LAYER_NAME!r} is not published on {BASE}; nothing to reset", file=sys.stderr)
    sys.exit(2)
layer_id = match[0]["Id"]
features_path = f"/odata/Layers({layer_id})/Features"

rows, url = [], features_path + "?$top=1000"
while url:
    status, page = request("GET", url)
    if status != 200:
        sys.exit(f"GET {url} -> {status}: {page}")
    rows.extend(page.get("value", []))
    nxt = page.get("@odata.nextLink")
    url = reroot(nxt) if nxt and len(rows) < 20000 else None

# Guard: refuse anything that is not shaped like the inspection layer.
reserved = {"ObjectId", "LayerId", "Geometry", "id", "objectid", "OBJECTID"}
for row in rows:
    extra = {k for k in row if not k.startswith("@") and k not in reserved and k not in FIELDS}
    if extra:
        print(f"refusing: layer {LAYER_NAME} row {row.get('ObjectId')} carries fields {sorted(extra)} "
              f"that the inspection seed does not have", file=sys.stderr)
        sys.exit(2)


def plan_for(rows):
    unclaimed = list(range(len(seed)))
    keep, patch, delete = [], [], []
    remaining = []
    seed_keys = {i: key_of(seed[i]["attrs"], seed[i]["coords"]) for i in unclaimed}
    for row in rows:
        k = key_of(row, (row.get("Geometry") or {}).get("coordinates"))
        hit = next((i for i in unclaimed if seed_keys[i] == k), None)
        if hit is None:
            remaining.append(row)
        else:
            unclaimed.remove(hit)
            keep.append(row["ObjectId"])
    for row in remaining:
        hit = next((i for i in unclaimed if seed[i]["attrs"]["name"] == row.get("name")), None)
        if hit is None:
            delete.append(row["ObjectId"])
        else:
            unclaimed.remove(hit)
            patch.append((row["ObjectId"], hit))
    return keep, patch, delete, unclaimed


keep, patch, delete, post = plan_for(rows)
print(f"{BASE} layer {LAYER_NAME} (OData id {layer_id}): {len(rows)} rows")
print(f"  keep   {len(keep)}")
print(f"  patch  {len(patch)}  " + ", ".join(f"{oid}<-{seed[i]['attrs']['name']}" for oid, i in patch))
print(f"  delete {len(delete)}  " + ", ".join(str(oid) for oid in delete))
print(f"  post   {len(post)}  " + ", ".join(seed[i]["attrs"]["name"] for i in post))
if not (patch or delete or post):
    print("already in seed state")
    sys.exit(0)
if not APPLY:
    print("dry run: re-run with --apply to execute this plan")
    sys.exit(0)


def body_for(i):
    return {"Geometry": {"type": "Point", "coordinates": seed[i]["coords"]}, "Attributes": dict(seed[i]["attrs"])}


failed = 0
for oid, i in patch:
    status, resp = request("PATCH", f"{features_path}({oid})", body_for(i))
    ok = status in (200, 204)
    failed += not ok
    print(f"  PATCH {oid} -> {status}" + ("" if ok else f" {resp}"))
for oid in delete:
    status, resp = request("DELETE", f"{features_path}({oid})")
    ok = status in (200, 204, 404)
    failed += not ok
    print(f"  DELETE {oid} -> {status}" + ("" if ok else f" {resp}"))
for i in post:
    status, resp = request("POST", features_path, body_for(i))
    ok = status in (200, 201)
    failed += not ok
    print(f"  POST {seed[i]['attrs']['name']} -> {status}" + ("" if ok else f" {resp}"))

rows, url = [], features_path + "?$top=1000"
while url:
    status, page = request("GET", url)
    if status != 200:
        sys.exit(f"verify GET {url} -> {status}")
    rows.extend(page.get("value", []))
    nxt = page.get("@odata.nextLink")
    url = reroot(nxt) if nxt and len(rows) < 20000 else None
keep, patch, delete, post = plan_for(rows)
clean = not (patch or delete or post) and len(rows) == 27
print(f"verify: {len(rows)} rows, {'seed state' if clean else 'NOT in seed state'}"
      + (f", {failed} request(s) failed" if failed else ""))
sys.exit(0 if clean and not failed else 1)
PY
