#!/usr/bin/env python3
"""Sanitize exact AWS identities and bind pre/post runtime evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "stacks" / "aws-db-migration-runner" / "runner"
ARCHIVE_BASE64_SHA256 = "7HtndesCzzenO9cFPm2ipSS+NDt3heY4S0Ozpwiyx5o="
ACCOUNT = "585192672263"
REGION = "us-west-2"
RUNNER_NAME = "honua-demo-demo-db-migration-092-105"
RUNNER_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{RUNNER_NAME}"
RUNNER_ROLE = f"arn:aws:iam::{ACCOUNT}:role/{RUNNER_NAME}-role"
VPC_ID = "vpc-0ac1893d15caf97b8"
SUBNETS = ["subnet-042ddf313d8ae1b17", "subnet-095cc7be2b14464f5", "subnet-0fd43d4ab39f4ff00"]
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
SHA64 = re.compile(r"^[A-Za-z0-9+/]{43}=$")
SG = re.compile(r"^sg-[0-9a-f]{17}$")
SECRET_ARN = re.compile(r"^arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-[A-Za-z0-9]{6}$")
STATE_ADDRESSES = {
    "data.aws_iam_policy_document.assume", "data.aws_secretsmanager_secret.db_connection",
    "aws_cloudwatch_log_group.runner", "aws_iam_role.runner", "aws_iam_role_policy.runner",
    "aws_security_group.runner", "aws_lambda_function.runner",
}


def req(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    req(type(value) is dict, "runtime artifact must be an object")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_sha(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def exact_keys(value: dict, keys: set[str], label: str) -> None:
    req(set(value) == keys, f"{label} allowlist drifted")


def state_pins(state: dict) -> dict:
    resources = state.get("resources")
    req(type(resources) is list, "isolated state resources missing")
    indexed: dict[str, dict] = {}
    for resource in resources:
        req(type(resource) is dict and resource.get("mode") in {"data", "managed"}, "isolated state resource invalid")
        prefix = "data." if resource["mode"] == "data" else ""
        address = f"{prefix}{resource.get('type')}.{resource.get('name')}"
        instances = resource.get("instances")
        req(address not in indexed and type(instances) is list and len(instances) == 1, "isolated state instance graph drifted")
        attributes = instances[0].get("attributes") if type(instances[0]) is dict else None
        req(type(attributes) is dict, "isolated state attributes missing")
        indexed[address] = attributes
    req(set(indexed) == STATE_ADDRESSES, "isolated state resource graph drifted")

    secret = indexed["data.aws_secretsmanager_secret.db_connection"]
    secret_arn = secret.get("arn")
    req(secret.get("name") == "honua-demo-demo/connection-string" and type(secret_arn) is str and SECRET_ARN.fullmatch(secret_arn) is not None, "state DB secret identity drifted")
    security_group = indexed["aws_security_group.runner"]
    security_group_id = security_group.get("id")
    req(type(security_group_id) is str and SG.fullmatch(security_group_id) is not None and security_group.get("vpc_id") == VPC_ID, "state runner security group drifted")
    role = indexed["aws_iam_role.runner"]
    req(role.get("arn") == RUNNER_ROLE and role.get("name") == f"{RUNNER_NAME}-role", "state runner role drifted")
    runner = indexed["aws_lambda_function.runner"]
    vpc = runner.get("vpc_config")
    environment = runner.get("environment")
    req(type(vpc) is list and len(vpc) == 1 and vpc[0].get("vpc_id") == VPC_ID, "state runner VPC missing")
    req(sorted(vpc[0].get("subnet_ids", [])) == SUBNETS and vpc[0].get("security_group_ids") == [security_group_id], "state runner network references drifted")
    req(type(environment) is list and len(environment) == 1 and environment[0].get("variables", {}).get("DB_SECRET_ARN") == secret_arn, "state runner secret reference drifted")
    req(runner.get("role") == RUNNER_ROLE and runner.get("runtime") == "python3.13" and runner.get("handler") == "handler.handler" and runner.get("memory_size") == 512, "state runner configuration drifted")
    req(runner.get("source_code_hash") == ARCHIVE_BASE64_SHA256, "state runner source digest drifted")
    qualified_arn, version = runner.get("qualified_arn"), runner.get("version")
    req(type(version) is str and re.fullmatch(r"[1-9][0-9]*", version) is not None and qualified_arn == f"{RUNNER_ARN}:{version}", "state runner qualified identity drifted")
    outputs = state.get("outputs")
    req(type(outputs) is dict and set(outputs) == {"db_migration_runner_qualified_arn", "db_migration_runner_version"}, "isolated state outputs drifted")
    req(outputs["db_migration_runner_qualified_arn"].get("value") == qualified_arn and outputs["db_migration_runner_version"].get("value") == version, "isolated state output identity drifted")
    identity = {"dbSecretArn":secret_arn, "securityGroupId":security_group_id, "runnerQualifiedArn":qualified_arn, "runnerVersion":version, "sourceCodeSha256":ARCHIVE_BASE64_SHA256}
    return {**identity, "stateIdentitySha256":value_sha(identity)}


def sanitize_runner(raw: dict, pins: dict) -> dict:
    config = raw.get("Configuration")
    req(type(config) is dict, "runner configuration missing")
    version = config.get("Version")
    req(type(version) is str and re.fullmatch(r"[1-9][0-9]*", version) is not None, "runner is not qualified")
    req(config.get("FunctionName") == RUNNER_NAME and config.get("FunctionArn") == pins["runnerQualifiedArn"] and version == pins["runnerVersion"], "runner identity drifted")
    req(config.get("CodeSha256") == ARCHIVE_BASE64_SHA256, "runner deployed code digest drifted")
    req(config.get("Runtime") == "python3.13" and config.get("Handler") == "handler.handler", "runner handler/runtime drifted")
    req(config.get("Role") == RUNNER_ROLE and config.get("MemorySize") == 512, "runner role/memory drifted")
    req(config.get("Timeout") == 900, "runner timeout drifted")
    req(raw.get("Concurrency") == {"ReservedConcurrentExecutions": 1}, "runner reserved concurrency drifted")
    req(config.get("Architectures") == ["arm64"], "runner architecture drifted")
    vpc = config.get("VpcConfig")
    req(type(vpc) is dict and vpc.get("VpcId") == VPC_ID, "runner VPC drifted")
    subnets, security_groups = vpc.get("SubnetIds"), vpc.get("SecurityGroupIds")
    req(type(subnets) is list and sorted(subnets) == SUBNETS, "runner subnet set drifted")
    req(security_groups == [pins["securityGroupId"]], "runner security group drifted")
    environment = config.get("Environment", {}).get("Variables")
    req(type(environment) is dict and environment.get("DB_SECRET_ARN") == pins["dbSecretArn"], "runner secret reference drifted")
    expected_environment = {
        "DB_SECRET_ARN": environment["DB_SECRET_ARN"],
        "EXPECTED_CANDIDATE_IMAGE_DIGEST": IMAGE_DIGEST,
        "EXPECTED_PENDING_SET_SHA256": "e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7",
        "EXPECTED_PREFLIGHT_SHA256": "357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee",
        "EXPECTED_SOURCE_COMMIT": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
        "MIGRATION_OPERATION": "apply-092-105",
        "SOURCE_HANDLER_SHA256": hashlib.sha256((RUNNER / "handler.py").read_bytes()).hexdigest(),
        "SOURCE_MANIFEST_SHA256": hashlib.sha256((RUNNER / "migration-manifest.v1.json").read_bytes()).hexdigest(),
    }
    req(environment == expected_environment, "runner environment drifted")
    revision = config.get("RevisionId")
    req(type(revision) is str and re.fullmatch(r"[0-9a-f-]{36}", revision) is not None, "runner revision missing")
    return {
        "schema": "honua-db-migration-function-audit-v1", "kind": "runner",
        "functionName": RUNNER_NAME, "functionArn": config["FunctionArn"], "version": version,
        "revisionId": revision, "codeSha256": config["CodeSha256"], "runtime": "python3.13",
        "handler": "handler.handler", "role": RUNNER_ROLE, "memorySize": 512, "timeout": 900,
        "reservedConcurrentExecutions": 1, "architectures": ["arm64"], "vpcId": VPC_ID,
        "subnetIds": SUBNETS, "securityGroupIds": security_groups,
        "environmentKeys": sorted(environment), "environmentSha256": value_sha(environment),
    }


def sanitize_candidate(raw: dict) -> dict:
    config, code = raw.get("Configuration"), raw.get("Code")
    req(type(config) is dict and type(code) is dict, "candidate response missing")
    arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-honua:40"
    req(config.get("FunctionName") == "honua-demo-demo-honua" and config.get("FunctionArn") == arn, "candidate identity drifted")
    req(config.get("Version") == "40" and config.get("RevisionId") == "0326e209-4231-4acd-9bb4-d3cb89402db0", "candidate :40 drifted")
    resolved = code.get("ResolvedImageUri", "")
    req(type(resolved) is str and resolved.endswith(f"@{IMAGE_DIGEST}"), "candidate digest drifted")
    environment = config.get("Environment", {}).get("Variables")
    req(type(environment) is dict and environment.get("HONUA_SKIP_MIGRATIONS", "").lower() == "true", "candidate migration toggle drifted")
    return {"schema":"honua-db-migration-function-audit-v1", "kind":"candidate", "functionArn":arn,
            "version":"40", "revisionId":config["RevisionId"], "resolvedImageDigest":IMAGE_DIGEST,
            "skipMigrations":True}


def sanitize_preflight(raw: dict) -> dict:
    config = raw.get("Configuration")
    req(type(config) is dict, "preflight response missing")
    arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-candidate-preflight:3"
    req(config.get("FunctionName") == "honua-demo-demo-candidate-preflight" and config.get("FunctionArn") == arn, "preflight identity drifted")
    req(config.get("Version") == "3" and config.get("RevisionId") == "8114746a-223f-4358-a260-bd5699d7f992", "preflight helper drifted")
    code_sha = config.get("CodeSha256")
    req(type(code_sha) is str and SHA64.fullmatch(code_sha) is not None, "preflight code digest missing")
    return {"schema":"honua-db-migration-function-audit-v1", "kind":"preflight", "functionArn":arn,
            "version":"3", "revisionId":config["RevisionId"], "codeSha256":code_sha}


def sanitize_live(raw: dict) -> dict:
    req(raw.get("AliasArn") == f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-honua:live", "live ARN drifted")
    req(raw.get("Name") == "live" and raw.get("FunctionVersion") == "39", "live alias drifted")
    req(raw.get("RevisionId") == "4f73dd76-0294-44d3-8362-c6f8606f034e" and not raw.get("RoutingConfig"), "live routing/revision drifted")
    return {"schema":"honua-db-migration-function-audit-v1", "kind":"live", "aliasArn":raw["AliasArn"],
            "name":"live", "functionVersion":"39", "revisionId":raw["RevisionId"], "routing":{}}


SANITIZERS = {"candidate": sanitize_candidate, "preflight": sanitize_preflight, "live": sanitize_live}


def sanitize(kind: str, raw: dict, state: dict | None = None) -> dict:
    if kind == "runner":
        req(type(state) is dict, "runner sanitizer requires isolated postapply state")
        return sanitize_runner(raw, state_pins(state))
    req(state is None, "state is accepted only for runner sanitization")
    return SANITIZERS[kind](raw)


def build(args: argparse.Namespace) -> dict:
    runner, candidate, live, preflight, db, state = map(load, (args.runner, args.candidate, args.live, args.preflight, args.db, args.state))
    pins = state_pins(state)
    exact_keys(runner, {"schema","kind","functionName","functionArn","version","revisionId","codeSha256","runtime","handler","role","memorySize","timeout","reservedConcurrentExecutions","architectures","vpcId","subnetIds","securityGroupIds","environmentKeys","environmentSha256"}, "runner evidence")
    exact_keys(candidate, {"schema","kind","functionArn","version","revisionId","resolvedImageDigest","skipMigrations"}, "candidate evidence")
    exact_keys(live, {"schema","kind","aliasArn","name","functionVersion","revisionId","routing"}, "live evidence")
    exact_keys(preflight, {"schema","kind","functionArn","version","revisionId","codeSha256"}, "preflight evidence")
    req(runner["schema"] == candidate["schema"] == live["schema"] == preflight["schema"] == "honua-db-migration-function-audit-v1", "function evidence schema drifted")
    req(runner["kind"] == "runner" and runner["codeSha256"] == ARCHIVE_BASE64_SHA256, "runner digest evidence drifted")
    req(runner["runtime"] == "python3.13" and runner["handler"] == "handler.handler" and runner["role"] == RUNNER_ROLE and runner["memorySize"] == 512, "runner deployed configuration drifted")
    req(runner["timeout"] == 900 and runner["reservedConcurrentExecutions"] == 1 and runner["architectures"] == ["arm64"], "runner bounds evidence drifted")
    req(runner["functionArn"] == pins["runnerQualifiedArn"] and runner["version"] == pins["runnerVersion"], "runner state identity binding drifted")
    req(runner["vpcId"] == VPC_ID and runner["subnetIds"] == SUBNETS and runner["securityGroupIds"] == [pins["securityGroupId"]], "runner VPC evidence drifted")
    req(candidate == {"schema":"honua-db-migration-function-audit-v1","kind":"candidate","functionArn":f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-honua:40","version":"40","revisionId":"0326e209-4231-4acd-9bb4-d3cb89402db0","resolvedImageDigest":IMAGE_DIGEST,"skipMigrations":True}, "candidate evidence drifted")
    req(live["functionVersion"] == "39" and live["revisionId"] == "4f73dd76-0294-44d3-8362-c6f8606f034e" and live["routing"] == {}, "live evidence drifted")
    req(preflight["version"] == "3" and preflight["revisionId"] == "8114746a-223f-4358-a260-bd5699d7f992", "preflight evidence drifted")
    exact_keys(db, {"DBInstanceIdentifier","DBInstanceArn","DbiResourceId","DBInstanceStatus","Engine","EngineVersion","StorageEncrypted","KmsKeyId"}, "DB evidence")
    req(db == {"DBInstanceIdentifier":"honua-demo-demo-postgres","DBInstanceArn":"arn:aws:rds:us-west-2:585192672263:db:honua-demo-demo-postgres","DbiResourceId":"db-WNTITZLSHMDLINGEGSB6TEZQYI","DBInstanceStatus":"available","Engine":"postgres","EngineVersion":"15.17","StorageEncrypted":True,"KmsKeyId":"arn:aws:kms:us-west-2:585192672263:key/4bccd8dc-27dc-4390-9393-bd2dfad9cbc8"}, "DB identity/status drifted")
    req(type(state.get("lineage")) is str and type(state.get("serial")) is int, "state identity missing")
    return {"schema":"honua-db-migration-runtime-receipt-v1","qualifiedArn":runner["functionArn"],"version":runner["version"],"revisionId":runner["revisionId"],"codeSha256":runner["codeSha256"],"environmentSha256":runner["environmentSha256"],"securityGroupId":pins["securityGroupId"],"dbSecretArn":pins["dbSecretArn"],"stateIdentitySha256":pins["stateIdentitySha256"],"stateLineage":state["lineage"],"stateSerial":state["serial"],"candidateVersion":"40","liveVersion":"39","preflightVersion":"3","runnerSha256":sha(args.runner),"candidateSha256":sha(args.candidate),"liveSha256":sha(args.live),"preflightSha256":sha(args.preflight),"dbSha256":sha(args.db)}


def verify(expected: dict, actual: dict) -> None:
    req(expected == actual, "runtime identity/state/digest drifted")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    sanitize_parser = sub.add_parser("sanitize")
    sanitize_parser.add_argument("kind", choices=("runner", *SANITIZERS))
    sanitize_parser.add_argument("--state", type=Path)
    for mode in ("create", "verify"):
        item = sub.add_parser(mode)
        for name in ("runner","candidate","live","preflight","db","state","receipt"):
            item.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "sanitize":
        raw = json.load(sys.stdin)
        state = load(args.state) if args.state else None
        print(json.dumps(sanitize(args.kind, raw, state), sort_keys=True, separators=(",", ":")))
        return
    actual = build(args)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        verify(load(args.receipt), actual)
    print(f"db migration runtime {args.mode}: PASS")


if __name__ == "__main__":
    main()
