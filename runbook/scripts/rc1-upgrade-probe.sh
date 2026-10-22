#!/usr/bin/env bash
# Read-only before/after probe for the rc1 upgrade of demo.honua.io.
# Usage: rc1-upgrade-probe.sh <label> [out-dir]     e.g. before | after-migrate | after-deploy
# Writes <out-dir>/<label>.json and prints a one-screen summary. Never prints the admin key.
# Exit 0 = all checks pass; 2 = a check failed; 3 = pending Contract migration seen (ABORT the upgrade).
set -uo pipefail
LABEL="${1:?label}"; OUT="${2:-$HOME/.honua-runtime-proof/rc1-upgrade-20261022}"; mkdir -p "$OUT"
B="${BASE:-https://demo.honua.io}"; R=us-west-2; F=honua-demo-demo-honua
K="${HONUA_DEMO_API_KEY:-$(aws secretsmanager get-secret-value --region $R --secret-id honua-demo-demo/admin-password --query SecretString --output text)}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
code() { curl -s -m 60 -o "$T/body" -w '%{http_code}' "$@"; }
fail=0; abort=0
{
echo "{"
echo "\"label\":\"$LABEL\",\"at\":\"$(date -u +%FT%TZ)\","
echo "\"aliases\":$(aws lambda list-aliases --function-name $F --region $R --query 'Aliases[].{n:Name,v:FunctionVersion,rev:RevisionId}' --output json),"
echo "\"healthz\":{\"live\":\"$(code $B/healthz/live)\",\"ready\":\"$(code $B/healthz/ready)\"},"
code -H "X-API-Key: $K" "$B/api/v1/admin/deploy/preflight?includeDiagnostics=true" >/dev/null; cp "$T/body" "$T/pre.json"
echo "\"preflight\":$(python3 -c "import json;d=json.load(open('$T/pre.json'));print(json.dumps({'status':d.get('status'),'migration':d.get('migration'),'db':d.get('databaseCompatibility',{}).get('engineVersion'),'serving':d.get('platformRelease',{}).get('serving')}))" 2>/dev/null || echo null),"
code -H "X-API-Key: $K" $B/api/v1/metrics/health >/dev/null
echo "\"metricsHealth\":$(python3 -c "import json;print(json.dumps(json.load(open('$T/body'))))" 2>/dev/null || echo null),"
code -H "X-API-Key: $K" $B/api/v1/capabilities/manifest >/dev/null
echo "\"deploymentRevision\":$(python3 -c "import json;print(json.dumps(json.load(open('$T/body'))['server'].get('deploymentRevision')))" 2>/dev/null || echo null),"
echo "\"counts\":{"
sep=""
for id in 1:maui-parcels 2:maui-zoning 3:maui-roads 4:maui-flood-hazard 5:maui-sea-level-rise 6:maui-place-names 13:maui-buildings; do
  n=${id%%:*}; s=${id##*:}
  code "$B/rest/services/$s/FeatureServer/$n/query?where=1%3D1&returnCountOnly=true&f=json" >/dev/null
  c=$(python3 -c "import json;print(json.load(open('$T/body')).get('count'))" 2>/dev/null || echo null)
  printf '%s"%s":%s' "$sep" "$s" "$c"; sep=","
done
echo "},"
echo "\"families\":{"
sep=""
for p in "ogc-features:ogc/features/collections/maui-parcels/items?limit=1" "stac-items:stac/collections/90810/items?limit=2" "stac-root:stac" \
         "rest-services:rest/services?f=json" "quickstart-layer:rest/services/maui-buildings/FeatureServer/13/query?where=1%3D1&outFields=*&resultRecordCount=1&f=json" \
         "geocode:rest/services/Geocode/GeocodeServer?f=json" "hillshade:rest/services/maui-hillshade/ImageServer?f=json" \
         "processes:ogc/processes/processes" "manifest:demo-services.v1.json"; do
  printf '%s"%s":"%s"' "$sep" "${p%%:*}" "$(code "$B/${p#*:}")"; sep=","
done
code -X POST -H 'content-type: application/json' -d '{"collections":["90810"],"limit":2}' $B/stac/search >/dev/null
printf ',"stac-search":"%s"' "$(code -X POST -H 'content-type: application/json' -d '{"collections":["90810"],"limit":2}' $B/stac/search)"
echo "},"
code "$B/demo-services.v1.json" >/dev/null
echo "\"manifest\":$(python3 -c "import json;d=json.load(open('$T/body'));print(json.dumps({'schemaVersion':d['schemaVersion'],'services':[s['id'] for s in d['services']]}))" 2>/dev/null || echo null)"
echo "}"
} > "$OUT/$LABEL.json"
python3 - "$OUT/$LABEL.json" <<'PY' || exit $?
import json,sys
d=json.load(open(sys.argv[1])); bad=[]; abort=False
if d["healthz"]!={"live":"200","ready":"200"}: bad.append("healthz")
m=(d.get("preflight") or {}).get("migration") or {}
pend=m.get("pendingScripts") or []
if m.get("hasPendingContractScripts") or m.get("pendingContractScripts"): abort=True
bad+= [k for k,v in d["families"].items() if v!="200"]
bad+= [k for k,v in d["counts"].items() if not isinstance(v,int) or v<=0]
if len(d["manifest"]["services"])!=12 if d.get("manifest") else True: bad.append("manifest-12-services")
print(f'{d["label"]} {d["at"]} rev={str(d["deploymentRevision"])[:7]} live={ [a["v"] for a in d["aliases"] if a["n"]=="live"] } staging={ [a["v"] for a in d["aliases"] if a["n"]=="staging"] }')
print("migration:",m.get("lifecycleStatus"),"| pending:",len(pend),"| first:",pend[:1],"| last:",pend[-1:])
print("counts:",d["counts"]); print("families:",d["families"])
print("FAILED:",bad or "none"); 
if abort: print("ABORT: pending Contract migration reported"); sys.exit(3)
sys.exit(2 if bad else 0)
PY
