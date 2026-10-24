#!/usr/bin/env bash
# Read-only before/after probe for the rc1 upgrade of demo.honua.io.
# Usage: rc1-upgrade-probe.sh <label> [out-dir]     e.g. before | after-migrate | after-deploy
# Writes <out-dir>/<label>.json and prints a one-screen summary. Never prints the admin key.
# Exit 0 = all checks pass; 2 = a check failed; 3 = pending Contract migration seen (ABORT the upgrade).
#
# Demo-surface checks (2026-10-24, FOSS4G readiness) so a re-pin cannot silently break the
# browser demos: PMTiles ranged GET (206, >0 bytes) for every seeded archive; MapServer
# export and ImageServer exportImage as f=image (PNG, >0 bytes) and as f=json (an href to
# a stored image, which needs Redis-coordinated temp storage); an OGC API Tiles MVT tile
# (200/204); an OGC API Maps PNG (>0 bytes); and the capability manifest's licensing state
# (licenseValidationState=Disabled, every policies.entitlements[] active).
# Tile CORS (2026-10-24): a GET with `Origin: https://example.com` on an OGC Tiles MVT tile,
# an ImageServer tile and the PMTiles basemap must carry `access-control-allow-origin: *`
# (tiles are anonymous public reads; samples.honua.io and copied samples need it).
set -uo pipefail
LABEL="${1:?label}"; OUT="${2:-$HOME/.honua-runtime-proof/rc1-upgrade-20261022}"; mkdir -p "$OUT"
B="${BASE:-https://demo.honua.io}"; R=us-west-2; F=honua-demo-demo-honua
K="${HONUA_DEMO_API_KEY:-$(aws secretsmanager get-secret-value --region $R --secret-id honua-demo-demo/admin-password --query SecretString --output text)}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
code() { curl -s -m 60 -o "$T/body" -w '%{http_code}' "$@"; }
# demo_get <name> <path> [curl args...]: JSON object {status,bytes,type,png,cache}
demo_get() {
  local name="$1" path="$2"; shift 2
  local w; w=$(curl -s -m 60 -o "$T/d.$name" -D "$T/h.$name" -w '%{http_code} %{size_download} %{content_type}' "$@" "$B/$path")
  python3 - "$name" "$T/d.$name" "$T/h.$name" $w <<'PY'
import json,sys
name,body,hdr,status,size=sys.argv[1:6]; ctype=sys.argv[6] if len(sys.argv)>6 else ""
data=open(body,"rb").read()
def header(name):
    return next((l.split(":",1)[1].strip() for l in open(hdr,errors="replace") if l.lower().startswith(name+":")),None)
cache=header("x-cache")
out={"status":int(status),"bytes":int(size),"type":ctype,"png":data[:8]==b"\x89PNG\r\n\x1a\n","cache":cache}
if header("access-control-allow-origin") is not None: out["acao"]=header("access-control-allow-origin")
if ctype.startswith("application/json"):
    try:
        j=json.loads(data); out["href"]=bool(j.get("href")); out["error"]=(j.get("error") or {}).get("code")
    except Exception: pass
print(json.dumps(out))
PY
}
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
echo "\"demo\":{"
sep=""
for a in maui-basemap maui-buildings-static maui-hillshade-static maui-imagery-static maui-parcels-static maui-roads-static maui-terrain-static maui-zoning-static; do
  printf '%s"pmtiles-%s":%s' "$sep" "$a" "$(demo_get "pm-$a" "api/v1/tiles/pmtiles/$a" -H 'Range: bytes=0-16383' -H 'Origin: https://honua.io')"; sep=","
done
BB="bbox=-156.7,20.7,-156.2,21.05&bboxSR=4326&imageSR=3857&size=256,256"
printf ',"mapserver-export":%s' "$(demo_get ms-img "rest/services/maui-zoning/MapServer/export?$BB&format=png&transparent=true&f=image")"
printf ',"mapserver-export-json":%s' "$(demo_get ms-json "rest/services/maui-zoning/MapServer/export?$BB&format=png&transparent=true&f=json")"
printf ',"imageserver-export":%s' "$(demo_get is-img "rest/services/maui-hillshade/ImageServer/exportImage?$BB&format=png&f=image")"
printf ',"imageserver-export-json":%s' "$(demo_get is-json "rest/services/maui-hillshade/ImageServer/exportImage?$BB&format=png&f=json")"
printf ',"ogc-tiles-mvt":%s' "$(demo_get mvt "ogc/tiles/collections/maui-zoning/tiles/WebMercatorQuad/10/451/66")"
printf ',"ogc-maps-png":%s' "$(demo_get maps "ogc/maps/collections/maui-zoning/map?bbox=-156.7,20.7,-156.2,21.05&width=256&height=256&f=png")"
XO=(-H 'Origin: https://example.com')
printf ',"cors-ogc-tiles":%s' "$(demo_get cors-mvt "ogc/tiles/collections/maui-zoning/tiles/WebMercatorQuad/10/451/66" "${XO[@]}")"
printf ',"cors-imageserver-tile":%s' "$(demo_get cors-ist "rest/services/maui-hillshade/ImageServer/tile/10/451/66" "${XO[@]}")"
printf ',"cors-pmtiles":%s' "$(demo_get cors-pm "api/v1/tiles/pmtiles/maui-basemap" -H 'Range: bytes=0-16383' "${XO[@]}")"
code "$B/api/v1/capabilities/manifest" >/dev/null
printf ',"licensing":%s' "$(python3 -c "
import json;p=json.load(open('$T/body')).get('policies',{});e=p.get('entitlements',[])
print(json.dumps({'currentEdition':p.get('currentEdition'),'licenseValidationState':p.get('licenseValidationState'),'entitlements':len(e),'inactive':sorted(x['key'] for x in e if not x.get('active'))}))" 2>/dev/null || echo null)"
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
demo=d.get("demo") or {}
for k,v in demo.items():
    if k.startswith("pmtiles-") and not (v.get("status")==206 and v.get("bytes",0)>0): bad.append(k)
for k in ("mapserver-export","imageserver-export","ogc-maps-png"):
    v=demo.get(k) or {}
    if not (v.get("status")==200 and v.get("png") and v.get("bytes",0)>0): bad.append(k)
for k in ("mapserver-export-json","imageserver-export-json"):
    v=demo.get(k) or {}
    if not (v.get("status")==200 and v.get("href")): bad.append(k)
if (demo.get("ogc-tiles-mvt") or {}).get("status") not in (200,204): bad.append("ogc-tiles-mvt")
for k in ("cors-ogc-tiles","cors-imageserver-tile","cors-pmtiles"):
    v=demo.get(k) or {}
    if not (v.get("status") in (200,204,206) and v.get("acao")=="*"): bad.append(k)
lic=demo.get("licensing") or {}
if lic.get("licenseValidationState")!="Disabled": bad.append("licensing-mode-disabled")
if lic.get("inactive") or not lic.get("entitlements"): bad.append("entitlements-all-active")
print(f'{d["label"]} {d["at"]} rev={str(d["deploymentRevision"])[:7]} live={ [a["v"] for a in d["aliases"] if a["n"]=="live"] } staging={ [a["v"] for a in d["aliases"] if a["n"]=="staging"] }')
print("migration:",m.get("lifecycleStatus"),"| pending:",len(pend),"| first:",pend[:1],"| last:",pend[-1:])
print("counts:",d["counts"]); print("families:",d["families"])
for k,v in demo.items(): print(f"demo {k}:",json.dumps(v))
print("FAILED:",bad or "none"); 
if abort: print("ABORT: pending Contract migration reported"); sys.exit(3)
sys.exit(2 if bad else 0)
PY
