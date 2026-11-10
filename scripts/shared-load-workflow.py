"""Protected workflow orchestration; AWS output and credentials never reach artifacts."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = Path("artifacts/shared-load")
SAMPLES = Path("samples-source")
FUNCTION = "honua-demo-demo-honua"
SECRET = "honua-demo-demo/admin-password"
WORKFLOW = ".github/workflows/load-demo-dataset.yml"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def capture(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    require(result.returncode == 0, f"{args[0]} operation failed; inspect protected workflow configuration")
    return result.stdout.strip()


def aws(*args):
    return json.loads(capture("aws", *args, "--output", "json"))


def mask(value):
    require(isinstance(value, str) and value and "\n" not in value and "\r" not in value,
            "Invalid credential/configuration value")
    print(f"::add-mask::{value}", flush=True)
    return value


def validate(env):
    require(env["GITHUB_REF"] == "refs/heads/trunk", "Dispatch only on trunk")
    require(env["LOAD_ACTION"] in {"prepare", "stage", "activate", "rollback"}, "Invalid action")
    require(re.fullmatch(r"[a-f0-9]{40}", env["SAMPLES_SHA"]), "Full samples commit required")
    if env["LOAD_ACTION"] == "prepare":
        require(not env.get("PREVIOUS_RUN_ID") and not env.get("PLAN_SHA256"), "Prepare takes no prior run")
        return
    prior = env.get("PREVIOUS_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]*", prior) and prior != env["GITHUB_RUN_ID"], "Previous run required")
    require(re.fullmatch(r"[a-f0-9]{64}", env.get("PLAN_SHA256", "")), "Reviewed plan digest required")
    repo = env["GITHUB_REPOSITORY"]
    current = json.loads(capture("gh", "api", f"repos/{repo}/actions/runs/{env['GITHUB_RUN_ID']}"))
    previous = json.loads(capture("gh", "api", f"repos/{repo}/actions/runs/{prior}"))
    require(previous["workflow_id"] == current["workflow_id"] and previous["path"] == WORKFLOW
            and previous["head_branch"] == "trunk" and previous["event"] == "workflow_dispatch"
            and previous["status"] == "completed" and previous["repository"]["full_name"] == repo,
            "Previous artifact must be from this trunk workflow, including failed completed runs")


def verify(env):
    require(capture("git", "-C", str(SAMPLES), "rev-parse", "HEAD") == env["SAMPLES_SHA"], "Checkout mismatch")
    capture("git", "-C", str(SAMPLES), "merge-base", "--is-ancestor", env["SAMPLES_SHA"], "origin/trunk")
    if env["LOAD_ACTION"] == "prepare":
        return
    metadata = json.loads((ROOT / "workflow.json").read_text())
    require(metadata["samplesSha"] == env["SAMPLES_SHA"] and metadata["planSha256"] == env["PLAN_SHA256"],
            "Artifact revision or approved digest differs")
    capture("node", "--input-type=module", "-e",
            "import {verifyPlan} from './samples-source/scripts/state-local/shared-load.mjs';"
            "await verifyPlan(process.argv[1],process.argv[2]);", str(ROOT), env["PLAN_SHA256"])


def metric(database, name, statistic):
    now = datetime.now(timezone.utc)
    result = aws("cloudwatch", "get-metric-statistics", "--namespace", "AWS/RDS", "--metric-name", name,
                 "--dimensions", f"Name=DBInstanceIdentifier,Value={database}", "--period", "60",
                 "--statistics", statistic, "--start-time", (now - timedelta(minutes=4)).isoformat(),
                 "--end-time", now.isoformat())
    points = result.get("Datapoints", [])
    require(bool(points), "Missing database monitoring data; fail closed")
    point = max(points, key=lambda value: value["Timestamp"])
    age = (now - datetime.fromisoformat(point["Timestamp"].replace("Z", "+00:00"))).total_seconds()
    require(0 <= age <= 180, "Stale database monitoring data; fail closed")
    return float(point[statistic])


def monitor(database, baseline, enforce=True):
    connections = metric(database, "DatabaseConnections", "Maximum")
    free = metric(database, "FreeStorageSpace", "Minimum")
    used = max(0, baseline - free)
    print(json.dumps({"connections": connections, "storageGrowthBytes": used}), flush=True)
    if enforce:
        require(connections < 130, "Connection ceiling reached; load stopped")
        require(used < 900_000_000 and free > 2_000_000_000, "Storage guard reached; load stopped")
    return {"connections": connections, "storageGrowthBytes": used}


def configure(env):
    key = mask(aws("secretsmanager", "get-secret-value", "--secret-id", SECRET)["SecretString"])
    bucket = mask(aws("lambda", "get-function-configuration", "--function-name", FUNCTION,
                      "--qualifier", "live", "--query", "Environment.Variables.FileStorage__AwsS3__BucketName"))
    databases = aws("rds", "describe-db-instances", "--query", "DBInstances[].DBInstanceIdentifier")
    matches = [name for name in databases if name.startswith("honua-demo-demo-")]
    require(len(matches) == 1, "Exactly one existing demo database required")
    database = mask(matches[0])
    env["HONUA_ADMIN_API_KEY"] = key
    return bucket, database


def stage_objects(bucket, plan, digest):
    urls = {}
    for layer in plan["layers"]:
        name, checksum = layer["name"], layer["cleanSha256"]
        key = f"state-local/{digest}/{checksum}/{name}.geojson"
        filename = ROOT / f"{name}.geojson"
        result = subprocess.run(["aws", "s3api", "head-object", "--bucket", bucket, "--key", key],
                                capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            existing = json.loads(result.stdout)
            require(existing.get("Metadata", {}).get("sha256") == checksum
                    and existing["ContentLength"] == layer["bytes"], "Existing staged object differs")
        else:
            require("(404)" in result.stderr, "Cannot verify staged object; fail closed")
            aws("s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(filename),
                "--if-none-match", "*", "--metadata", f"sha256={checksum}", "--content-type", "application/geo+json")
        urls[name] = mask(capture("aws", "s3", "presign", f"s3://{bucket}/{key}", "--expires-in", "900"))
    return urls


def claim_generation(bucket, env):
    key = f"state-local/{env['PLAN_SHA256']}/last-attempt.json"
    with tempfile.TemporaryDirectory() as private:
        filename = Path(private) / "generation.json"
        result = subprocess.run(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key, str(filename)],
                                capture_output=True, text=True, timeout=120)
        condition = ["--if-none-match", "*"]
        if result.returncode == 0:
            previous = json.loads(filename.read_text())
            require(previous["runId"] == env["PREVIOUS_RUN_ID"] and previous["samplesSha"] == env["SAMPLES_SHA"],
                    "Stale artifact refused; use the most recent attempted run's receipts")
            condition = ["--if-match", json.loads(result.stdout)["ETag"]]
        else:
            require("(NoSuchKey)" in result.stderr and env["LOAD_ACTION"] == "stage", "Load generation unavailable")
        filename.write_text(json.dumps({"runId": env["GITHUB_RUN_ID"], "samplesSha": env["SAMPLES_SHA"]}))
        aws("s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(filename), *condition)
        (ROOT / "generation.json").write_text(filename.read_text())


def run_guarded(command, env, database, baseline, enforce):
    monitor(database, baseline, enforce)
    child = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while child.poll() is None:
            monitor(database, baseline, enforce)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        require(child.returncode == 0, "Loader stopped; retain receipts and reconcile intents before retry")
        return monitor(database, baseline, enforce)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def run(env):
    action = env["LOAD_ACTION"]
    ROOT.mkdir(parents=True, exist_ok=True)
    command = ["node", str(SAMPLES / "scripts/state-local/shared-load.mjs"), action, str(ROOT)]
    if action == "prepare":
        result = json.loads(capture(*command))
        (ROOT / "workflow.json").write_text(json.dumps({"samplesSha": env["SAMPLES_SHA"],
                                                       "planSha256": result["planSha256"]}))
        print(json.dumps(result, indent=2))
        return
    digest = env["PLAN_SHA256"]
    plan = json.loads((ROOT / "shared-load-plan.json").read_text())
    bucket, database = configure(env)
    baseline_file = ROOT / "monitor-baseline.json"
    if baseline_file.exists():
        baseline = json.loads(baseline_file.read_text())["freeBytes"]
    else:
        require(action == "stage", "Initial monitoring baseline missing")
        baseline = metric(database, "FreeStorageSpace", "Minimum")
        baseline_file.write_text(json.dumps({"freeBytes": baseline}))
    monitor(database, baseline, action != "rollback")
    claim_generation(bucket, env)
    with tempfile.TemporaryDirectory() as private:
        if action == "stage":
            urls = stage_objects(bucket, plan, digest)
            url_file = Path(private) / "import-urls.json"
            url_file.write_text(json.dumps(urls))
            url_file.chmod(0o600)
            env["HONUA_SAMPLE_IMPORT_URLS_FILE"] = str(url_file)
        result = run_guarded(command + [digest], env, database, baseline, action != "rollback")
        (ROOT / f"{action}-monitor.json").write_text(json.dumps(result))
    print(f"{action} completed; inspect retained deployment receipts")


if __name__ == "__main__":
    try:
        {"validate": validate, "verify": verify, "run": run}[sys.argv[1]](dict(os.environ))
    except Exception:
        print("::error::Shared load stopped. Inspect retained receipts and protected workflow configuration; do not retry with a fresh artifact.",
              file=sys.stderr)
        sys.exit(1)
