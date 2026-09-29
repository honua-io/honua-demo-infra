#!/usr/bin/env python3
"""Governed restore of the live demo `honua.features` relation (honua-server#3384).

Operator ruling A (2026-09-16) selected RESTORE. The last good copy of the
feature relation is the retained `public.features` table: migration 001 ran with
`search_path=public`, while the serving image qualifies the relation with its
default metadata schema `honua`. The restore is a zero-copy, single-transaction
`ALTER TABLE ... SET SCHEMA honua` that proves the reviewed row count, content
digest and change-journal key digest before and after the move.

Subcommands:
  render PROOF-RELATION|cutover     print the Lambda event (never invokes)
  verify PHASE RESPONSE             check a helper response against the pins
  prove PHASE EVIDENCE_DIR          read-only proof invocation + verification
  cutover EVIDENCE_DIR REVIEWED_PROOF_SHA256
                                    the single authorized data-changing step
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

BOOTSTRAP_FUNCTION = "honua-demo-demo-postgis-bootstrap"
DB_INSTANCE = "honua-demo-demo-postgres"
SOURCE_RELATION = "public.features"
TARGET_RELATION = "honua.features"
TARGET_SEQUENCE = "honua.features_objectid_seq"
# Change writers and the pinned seed serialize on this generation lock.
GENERATION_LOCK = (144047712, 0)
SEED_LAYERS = (90810, 90820)

# Retained automated snapshot that contains the last good relation, and the
# manual copy the coordinator makes so it survives the 3-day retention window.
SOURCE_SNAPSHOT = "rds:honua-demo-demo-postgres-2026-09-16-07-25"
SOURCE_SNAPSHOT_CREATED = "2026-09-16T07:25:10.936Z"
RESTORE_SNAPSHOT = "honua-demo-stac-3384-features-12cf791316b8"

# Evidence captured read-only on 2026-09-16 from the live database.
EXPECTED_ROWS = 110229
EXPECTED_CONTENT_SHA256 = "12cf791316b8739de47844f2dc1724879f4514570879cf4508ec32902297521e"
EXPECTED_KEY_SHA256 = "573a62ff64d5c8784566f95800cfcaba771fa247699bbfcf5ffd93ff6ec7a24c"
EXPECTED_NON_SEED_ROWS = 110222
EXPECTED_NON_SEED_CONTENT_SHA256 = "fce37c30965c5aa96cd46188e5b993755dd72c29a695a2b6e12db207acd3bf91"
EXPECTED_JOURNAL_ROWS = 222124
EXPECTED_JOURNAL_MAX_GENERATION = 222124
EXPECTED_JOURNAL_LAST_CHANGE = "2026-07-20T21:28:14.467807Z"
EXPECTED_LAYER_ROWS = {
    1: 51245, 2: 3274, 3: 7071, 4: 2014, 5: 1537, 6: 2328, 7: 1, 8: 1, 9: 1,
    12: 28, 13: 42674, 68810: 4, 68820: 3, 68821: 2, 68822: 29, 68823: 10,
    90810: 4, 90820: 3,
}

PHASES = ("pre-cutover", "post-cutover", "post-migrations", "post-seed")
PROOF_RELATIONS = (SOURCE_RELATION, TARGET_RELATION)


CONTENT_SHA256_EXPR = (
    "encode(sha256(convert_to(string_agg(concat_ws('|', layer_id, objectid, "
    "coalesce(md5(ST_AsEWKB(geometry)), '-'), coalesce(md5(attributes::text), '-'), "
    "coalesce(to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'), '-'), "
    "coalesce(to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'), '-')), "
    "E'\\n' ORDER BY layer_id, objectid), 'UTF8')), 'hex')"
)
KEY_SHA256_EXPR = (
    "encode(sha256(convert_to(string_agg(layer_id || '|' || objectid, "
    "E'\\n' ORDER BY layer_id, objectid), 'UTF8')), 'hex')"
)


def _row_digest(relation: str, where: str = "") -> str:
    return f"SELECT count(*) AS n, {CONTENT_SHA256_EXPR} AS sha FROM {relation}{where}"


def _key_digest(source: str) -> str:
    return f"SELECT count(*) AS n, {KEY_SHA256_EXPR} AS sha FROM {source}"


JOURNAL_NET_LIVE = (
    "(SELECT layer_id, objectid FROM (SELECT DISTINCT ON (layer_id, objectid) layer_id, objectid, operation "
    "FROM honua.feature_changes ORDER BY layer_id, objectid, generation DESC) last WHERE operation <> 3) live"
)


def render_proof(relation: str) -> dict:
    if relation not in PROOF_RELATIONS:
        raise ValueError(f"proof relation must be one of {PROOF_RELATIONS}")
    seed_layers = ", ".join(str(layer) for layer in SEED_LAYERS)
    query = (
        "WITH content AS (" + _row_digest(relation) + "), "
        "non_seed AS (" + _row_digest(relation, f" WHERE layer_id NOT IN ({seed_layers})") + "), "
        "keys AS (" + _key_digest(relation) + "), "
        "journal_keys AS (" + _key_digest(JOURNAL_NET_LIVE) + "), "
        "journal AS (SELECT count(*) AS n, max(generation) AS g, "
        "to_char(max(changed_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS last FROM honua.feature_changes), "
        f"layers AS (SELECT json_object_agg(layer_id, n ORDER BY layer_id)::text AS j FROM (SELECT layer_id, count(*) AS n FROM {relation} GROUP BY layer_id) l) "
        "SELECT to_regclass('public.features') IS NOT NULL, to_regclass('honua.features') IS NOT NULL, "
        "content.n, content.sha, non_seed.n, non_seed.sha, keys.sha, journal_keys.n, journal_keys.sha, "
        "journal.n, journal.g, journal.last, (SELECT count(*) FROM honua.replicas), "
        f"EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = '{relation}'::regclass AND tgname = 'trigger_track_feature_changes' AND NOT tgisinternal), "
        f"pg_get_serial_sequence('{relation}', 'objectid'), layers.j "
        "FROM content, non_seed, keys, journal_keys, journal, layers"
    )
    return {"operation": "break-glass-sql", "query": query}


CUTOVER_SQL = f"""DO $stac_3384_features_cutover$
DECLARE
    observed_rows bigint;
    observed_content text;
    observed_keys text;
    journal_rows bigint;
    journal_generation bigint;
    journal_keys text;
    replica_rows bigint;
BEGIN
    PERFORM set_config('lock_timeout', '5s', true);
    PERFORM set_config('statement_timeout', '90s', true);
    PERFORM pg_advisory_xact_lock({GENERATION_LOCK[0]}, {GENERATION_LOCK[1]});

    IF to_regclass('honua.features') IS NOT NULL
       OR to_regclass('{TARGET_SEQUENCE}') IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'stac-3384 cutover refused: honua.features already exists';
    END IF;
    IF to_regclass('public.features') IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'stac-3384 cutover refused: restore source public.features is missing';
    END IF;

    LOCK TABLE public.features IN ACCESS EXCLUSIVE MODE;
    LOCK TABLE honua.feature_changes, honua.replicas IN SHARE MODE;

    SELECT count(*), {CONTENT_SHA256_EXPR} INTO observed_rows, observed_content FROM public.features;
    SELECT {KEY_SHA256_EXPR} INTO observed_keys FROM public.features;
    SELECT count(*), max(generation) INTO journal_rows, journal_generation FROM honua.feature_changes;
    SELECT {KEY_SHA256_EXPR} INTO journal_keys FROM {JOURNAL_NET_LIVE};
    SELECT count(*) INTO replica_rows FROM honua.replicas;

    IF observed_rows IS DISTINCT FROM {EXPECTED_ROWS}
       OR observed_content IS DISTINCT FROM '{EXPECTED_CONTENT_SHA256}'
       OR observed_keys IS DISTINCT FROM '{EXPECTED_KEY_SHA256}'
       OR journal_keys IS DISTINCT FROM '{EXPECTED_KEY_SHA256}'
       OR journal_rows IS DISTINCT FROM {EXPECTED_JOURNAL_ROWS}
       OR journal_generation IS DISTINCT FROM {EXPECTED_JOURNAL_MAX_GENERATION}
       OR replica_rows IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'stac-3384 cutover refused: restore source drifted from the reviewed proof';
    END IF;

    ALTER TABLE public.features SET SCHEMA honua;

    SELECT count(*), {CONTENT_SHA256_EXPR}
      INTO observed_rows, observed_content FROM honua.features;
    IF observed_rows IS DISTINCT FROM {EXPECTED_ROWS}
       OR observed_content IS DISTINCT FROM '{EXPECTED_CONTENT_SHA256}'
       OR to_regclass('public.features') IS NOT NULL
       OR pg_get_serial_sequence('honua.features', 'objectid') IS DISTINCT FROM '{TARGET_SEQUENCE}'
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'honua.features'::regclass
                      AND tgname = 'trigger_track_feature_changes' AND NOT tgisinternal) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'stac-3384 cutover rolled back: honua.features did not prove the reviewed content';
    END IF;
END
$stac_3384_features_cutover$"""


def render_cutover() -> dict:
    return {
        "operation": "break-glass-sql",
        "statements": [CUTOVER_SQL],
        "query": render_proof(TARGET_RELATION)["query"],
    }


FIELDS = (
    "sourcePresent", "targetPresent", "rows", "contentSha256", "nonSeedRows", "nonSeedContentSha256",
    "keySha256", "journalNetLiveRows", "journalNetLiveKeySha256", "journalRows", "journalMaxGeneration",
    "journalLastChange", "replicas", "trackingTrigger", "objectidSequence", "layerRows",
)


def parse_response(response: dict) -> dict:
    if "errorMessage" in response or "FunctionError" in response:
        raise RuntimeError("helper invocation failed; no proof row")
    rows = response.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != len(FIELDS):
        raise RuntimeError("helper response is not exactly one proof row")
    proof = dict(zip(FIELDS, rows[0]))
    for key in ("sourcePresent", "targetPresent", "trackingTrigger"):
        # pg8000 renders Python booleans; psql renders t/f.
        if proof[key] not in ("True", "False", "t", "f"):
            raise RuntimeError(f"{key} is not a boolean")
        proof[key] = proof[key] in ("True", "t")
    for key in ("rows", "nonSeedRows", "journalNetLiveRows", "journalRows", "journalMaxGeneration", "replicas"):
        proof[key] = int(proof[key])
    proof["layerRows"] = {int(k): int(v) for k, v in json.loads(proof["layerRows"]).items()}
    return proof


def verify(phase: str, proof: dict) -> list[str]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    failures: list[str] = []

    def expect(key: str, value: object) -> None:
        if proof.get(key) != value:
            failures.append(f"{key}: expected {value!r}, observed {proof.get(key)!r}")

    expect("sourcePresent", phase == "pre-cutover")
    expect("targetPresent", phase != "pre-cutover")
    expect("rows", EXPECTED_ROWS)
    expect("keySha256", EXPECTED_KEY_SHA256)
    expect("journalNetLiveRows", EXPECTED_ROWS)
    expect("journalNetLiveKeySha256", EXPECTED_KEY_SHA256)
    expect("nonSeedRows", EXPECTED_NON_SEED_ROWS)
    expect("nonSeedContentSha256", EXPECTED_NON_SEED_CONTENT_SHA256)
    expect("layerRows", EXPECTED_LAYER_ROWS)
    expect("replicas", 0)
    expect("trackingTrigger", True)
    expect(
        "objectidSequence",
        "public.features_objectid_seq" if phase == "pre-cutover" else TARGET_SEQUENCE,
    )
    if phase == "post-seed":
        # The seed deletes and re-inserts only its own fixture layers, so their
        # timestamps change; every restored non-seed row must stay identical.
        if proof.get("journalRows", 0) <= EXPECTED_JOURNAL_ROWS:
            failures.append("journalRows: the seed must append change history, never remove it")
    else:
        expect("contentSha256", EXPECTED_CONTENT_SHA256)
        expect("journalRows", EXPECTED_JOURNAL_ROWS)
        expect("journalMaxGeneration", EXPECTED_JOURNAL_MAX_GENERATION)
        expect("journalLastChange", EXPECTED_JOURNAL_LAST_CHANGE)
    return failures


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)


def _invoke(event: dict, evidence_dir: Path, name: str, run: Runner) -> dict:
    event_path = evidence_dir / f"{name}-event.json"
    response_path = evidence_dir / f"{name}-response.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    # fileb:// sends the raw JSON bytes under both AWS CLI v1 and v2.
    result = run([
        "aws", "lambda", "invoke", "--function-name", BOOTSTRAP_FUNCTION,
        "--invocation-type", "RequestResponse", "--payload", f"fileb://{event_path}",
        "--query", "FunctionError", "--output", "text", str(response_path),
    ])
    if result.returncode != 0 or result.stdout.strip() not in ("", "None"):
        raise RuntimeError(f"{name} invocation failed")
    return json.loads(response_path.read_text(encoding="utf-8"))


def _write_verdict(evidence_dir: Path, phase: str, proof: dict, failures: list[str]) -> Path:
    verdict = {
        "format": "honua.demo.stac-3384-features-proof.v1",
        "phase": phase,
        "proof": {**proof, "layerRows": {str(k): v for k, v in proof["layerRows"].items()}},
        "failures": failures,
        "pass": not failures,
    }
    path = evidence_dir / f"{phase}-proof.json"
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(verdict, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def prove(phase: str, evidence_dir: Path, run: Runner = _run) -> int:
    event = render_proof(SOURCE_RELATION if phase == "pre-cutover" else TARGET_RELATION)
    if set(event) != {"operation", "query"} or not event["query"].startswith("WITH "):
        raise RuntimeError("proof events must be query-only")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    proof = parse_response(_invoke(event, evidence_dir, phase, run))
    failures = verify(phase, proof)
    path = _write_verdict(evidence_dir, phase, proof, failures)
    print(f"{phase} proof: {'PASS' if not failures else 'FAIL'} sha256={_sha256(path)}")
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)
    return 0 if not failures else 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cutover(evidence_dir: Path, reviewed_proof_sha256: str, run: Runner = _run) -> int:
    if not re.fullmatch(r"[0-9a-f]{64}", reviewed_proof_sha256):
        raise RuntimeError("reviewed pre-cutover proof digest must be 64 lowercase hex")
    proof_path = evidence_dir / "pre-cutover-proof.json"
    if _sha256(proof_path) != reviewed_proof_sha256:
        raise RuntimeError("pre-cutover proof does not match the reviewed digest")
    if not json.loads(proof_path.read_text(encoding="utf-8")).get("pass"):
        raise RuntimeError("pre-cutover proof did not pass")

    snapshot = run([
        "aws", "rds", "describe-db-snapshots", "--db-snapshot-identifier", RESTORE_SNAPSHOT,
        "--query", "DBSnapshots[0].{id:DBSnapshotIdentifier,db:DBInstanceIdentifier,status:Status,type:SnapshotType}",
        "--output", "json",
    ])
    if snapshot.returncode != 0:
        raise RuntimeError("restore snapshot is not readable")
    if json.loads(snapshot.stdout) != {"id": RESTORE_SNAPSHOT, "db": DB_INSTANCE, "status": "available", "type": "manual"}:
        raise RuntimeError("restore snapshot is not the available manual copy of the source database")

    # One attempt only: the marker is a replay guard, never an idempotent reuse.
    with open(evidence_dir / "cutover-attempt.json", "x", encoding="utf-8") as handle:
        json.dump({"attempt": 1, "reviewedProofSha256": reviewed_proof_sha256, "snapshot": RESTORE_SNAPSHOT}, handle)
        handle.write("\n")
    proof = parse_response(_invoke(render_cutover(), evidence_dir, "cutover", run))
    failures = verify("post-cutover", proof)
    path = _write_verdict(evidence_dir, "post-cutover", proof, failures)
    print(f"post-cutover proof: {'PASS' if not failures else 'FAIL'} sha256={_sha256(path)}")
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)
    return 0 if not failures else 1


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2 and argv[0] == "render":
        event = render_cutover() if argv[1] == "cutover" else render_proof(argv[1])
        print(json.dumps(event, indent=2))
        return 0
    if len(argv) == 3 and argv[0] == "verify":
        proof = parse_response(json.loads(Path(argv[2]).read_text(encoding="utf-8")))
        failures = verify(argv[1], proof)
        for failure in failures:
            print(failure, file=sys.stderr)
        print(f"{argv[1]} proof: {'PASS' if not failures else 'FAIL'}")
        return 0 if not failures else 1
    if len(argv) == 3 and argv[0] == "prove" and argv[1] in PHASES:
        return prove(argv[1], Path(argv[2]))
    if len(argv) == 3 and argv[0] == "cutover":
        return cutover(Path(argv[1]), argv[2])
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    os.environ.setdefault("AWS_REGION", "us-west-2")
    os.environ.setdefault("AWS_PAGER", "")
    sys.exit(main(sys.argv[1:]))
