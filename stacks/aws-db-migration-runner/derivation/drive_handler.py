"""Apply the governed runner handler to a local database (connection swapped, pins from the manifest)."""
import hashlib, json, os, sys
from pathlib import Path
import pg8000.native
runner = Path(sys.argv[1]); sys.path.insert(0, str(runner))
import handler  # noqa: E402
m = json.loads((runner / "migration-manifest.v2.json").read_bytes())
os.environ.update({
    "EXPECTED_SOURCE_COMMIT": m["sourceCommit"], "EXPECTED_CANDIDATE_IMAGE_DIGEST": m["candidateImageDigest"],
    "EXPECTED_EXECUTED_SET_SHA256": m["executedScriptsSha256"], "EXPECTED_PENDING_SET_SHA256": m["pendingScriptsSha256"],
    "MIGRATION_OPERATION": handler.OPERATION,
    "SOURCE_HANDLER_SHA256": hashlib.sha256((runner / "handler.py").read_bytes()).hexdigest(),
    "SOURCE_MANIFEST_SHA256": hashlib.sha256((runner / "migration-manifest.v2.json").read_bytes()).hexdigest(),
})
handler._connect = lambda: pg8000.native.Connection(user="postgres", password="p", host="127.0.0.1", port=int(os.environ["PGPORT"]), database=sys.argv[2])
event = {"operation": handler.OPERATION}
if m["contractApprovalNonce"]:
    event["approveContractMigrations"] = m["contractApprovalNonce"]
print(json.dumps(handler.handler(event, None))[:4000])
