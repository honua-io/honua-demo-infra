"""Write runner/migrations/*.json and runner/migration-manifest.v2.json from RunnerArtifactHarness output.

Usage: python3 -I emit_runner_artifacts.py <harness-out.json> <source-commit> <candidate-image-digest>

The harness runs inside a honua-server checkout at <source-commit>; its JSON holds, per pending script,
the commands the server's own DbUp engine executes and the reader-compatibility declaration. The
executed baseline is the current manifest's executed set plus its applied scripts (the live journal
after the previous governed invocation).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

RUNNER = Path(os.environ.get("RUNNER_DIR") or Path(__file__).resolve().parent.parent / "runner")
PHASES = {"Expand": "Expand", "ContractAnnotated": "Contract"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value: object) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def main(harness_path: str, source_commit: str, image_digest: str) -> None:
    harness = json.loads(Path(harness_path).read_text(encoding="utf-8"))
    if harness["missingFromDiscovery"]:
        raise SystemExit(f"baseline scripts missing from discovery: {harness['missingFromDiscovery']}")
    manifest_path = RUNNER / "migration-manifest.v2.json"
    previous = json.loads(manifest_path.read_bytes())
    executed = sorted(previous["executedScripts"] + [s["name"] for s in previous["scripts"]])
    migrations = RUNNER / "migrations"
    for old in migrations.glob("*.json"):
        old.unlink()
    by_name = {script["name"]: script for script in harness["scripts"]}
    entries = []
    for name in harness["order"]:
        script = by_name[name]
        phase = PHASES.get(script["classification"])
        if phase is None:
            raise SystemExit(f"{name}: classification {script['classification']} cannot be applied")
        file_name = name.removesuffix(".sql") + ".json"
        # The harness serializes the server's declaration record with its C# property names; the
        # runner (and the server's journal columns) use the camelCase keys.
        declaration = {key[:1].lower() + key[1:]: value for key, value in script["declaration"].items()}
        body = _dump({"name": name, "phase": phase, "commands": script["commands"], "declaration": declaration})
        (migrations / file_name).write_bytes(body)
        entries.append({"name": name, "file": file_name, "phase": phase, "sha256": _sha(body)})
    manifest = {
        "schemaVersion": previous["schemaVersion"],
        "operation": f"apply-{source_commit[:7]}",
        "sourceCommit": source_commit,
        "candidateImageDigest": image_digest,
        "executedScripts": executed,
        "executedScriptsSha256": _sha("\n".join(executed).encode()),
        "scripts": entries,
        "pendingScriptsSha256": _sha("\n".join(harness["order"]).encode()),
        "contractApprovalNonce": harness["nonce"],
    }
    manifest_path.write_bytes(_dump(manifest))
    print(f"{len(entries)} scripts, {sum(e['phase'] == 'Contract' for e in entries)} contract, nonce={harness['nonce']}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
