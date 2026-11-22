#!/usr/bin/env bash
# One-job equivalence proof: governed runner handler vs honua-server's own runner, from the same baseline.
set -uo pipefail
root=$PWD; out=$root/artifacts/harness; mkdir -p "$out"; here=$root/tests/dotnet/Honua.Server.Tests/ZzHarness
export PGPORT=$((40000 + RANDOM % 2000)) PGPASSWORD=p PGHOST=127.0.0.1 PGUSER=postgres
name=s-demos-equiv-$PGPORT
docker run -d --rm --name $name -e POSTGRES_PASSWORD=p -p 127.0.0.1:$PGPORT:5432 postgis/postgis:16-3.4 >/dev/null || exit 1
trap 'docker rm -f $name >/dev/null 2>&1' EXIT
for i in $(seq 60); do docker exec $name pg_isready -U postgres >/dev/null 2>&1 && sleep 3 && break; sleep 2; done
psqlx() { docker exec -i $name psql -U postgres -v ON_ERROR_STOP=1 -qAt "$@"; }
for db in a b; do psqlx -c "CREATE DATABASE $db" && psqlx -d $db -c "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;"; done
dt() { HARNESS_MODE=$1 HARNESS_DB="Host=127.0.0.1;Port=$PGPORT;Database=$2;Username=postgres;Password=p" HARNESS_OUT=$out/$1-$2.txt \
  HARNESS_BASELINE=$here/baseline.txt HARNESS_APPROVAL=${3:-} \
  dotnet test tests/dotnet/Honua.Server.Tests/Honua.Server.Tests.csproj -c Release --no-build --filter "FullyQualifiedName~RunnerEquivalenceHarness" > $out/$1-$2.log 2>&1; echo "$1 $2 exit=$?"; cat $out/$1-$2.txt 2>/dev/null; }
dotnet build tests/dotnet/Honua.Server.Tests/Honua.Server.Tests.csproj -c Release > $out/build.log 2>&1 || { tail -30 $out/build.log; exit 1; }
HARNESS_OUT=$out/out.json HARNESS_BASELINE=$here/baseline.txt dotnet test tests/dotnet/Honua.Server.Tests/Honua.Server.Tests.csproj -c Release --no-build --filter "FullyQualifiedName~RunnerArtifactHarness" > $out/artifact.log 2>&1 || { tail -30 $out/artifact.log; exit 1; }
rm -rf $here/runner $out/runner; cp -r $here/runner-src $here/runner
RUNNER_DIR=$here/runner python3 -I $here/emit_runner_artifacts.py $out/out.json 99c422646c4cd246a73e9b65efd00e9e36724587 sha256:2ef00dc750c8222ed01694f52371bfd3e7b17cd12378ad6f2281efdde2bd2e76 || exit 1
cp -r $here/runner $out/runner
dt baseline a; dt baseline b
python3 -m venv $out/venv >/dev/null && $out/venv/bin/pip -q install pg8000 boto3 >/dev/null 2>&1
echo "== handler on a"; $out/venv/bin/python -I $here/drive_handler.py $here/runner a | tee $out/handler-a.json | head -c 1500; echo
NONCE=$(python3 -c "import json;print(json.load(open('$here/runner/migration-manifest.v2.json'))['contractApprovalNonce'] or '')")
dt reference b "$NONCE"
dt verify a; dt verify b
echo "== replay"; $out/venv/bin/python -I $here/drive_handler.py $here/runner a | head -c 400; echo
for db in a b; do docker exec $name pg_dump -U postgres --schema-only $db > $out/schema-$db.sql; \
  psqlx -d $db -c "SELECT scriptname FROM public.schema_versions ORDER BY 1" > $out/journal-$db.txt; \
  psqlx -d $db -c "SELECT script_name, classification, script_sha256, min_reader_schema FROM public.schema_version_reader_compatibility ORDER BY 1" > $out/reader-$db.txt; done
echo "== diffs"; for f in schema journal reader; do if cmp -s $out/$f-a.* $out/$f-b.* ; then echo "$f IDENTICAL"; else echo "$f DIFFER"; diff $out/$f-a.* $out/$f-b.* | head -40; fi; done
wc -l $out/journal-a.txt $out/journal-b.txt
