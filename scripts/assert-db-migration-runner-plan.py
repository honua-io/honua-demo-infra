#!/usr/bin/env python3
"""Allowlist the isolated runner Terraform plan; reject every other graph/action."""
from __future__ import annotations
import argparse,json
from pathlib import Path

MANAGED={"aws_cloudwatch_log_group.runner","aws_iam_role.runner","aws_iam_role_policy.runner","aws_security_group.runner","aws_lambda_function.runner"}
DATA={"data.aws_iam_policy_document.assume","data.aws_secretsmanager_secret.db_connection"}
ARCHIVE_BASE64_SHA256="7HtndesCzzenO9cFPm2ipSS+NDt3heY4S0Ozpwiyx5o="
def req(v,m):
    if not v: raise RuntimeError(m)
def main():
    p=argparse.ArgumentParser(); p.add_argument("show",type=Path); a=p.parse_args(); plan=json.loads(a.show.read_text(encoding="utf-8"))
    req(plan.get("applyable") is True and plan.get("complete") is True and plan.get("errored") is False,"plan is not complete/applyable")
    req(not plan.get("resource_drift") and not plan.get("deferred_changes"),"plan contains drift/deferred changes")
    for k in ("actions","action_invocations","action_triggers"): req(not plan.get(k),f"plan contains {k}")
    changes={x["address"]:x for x in plan.get("resource_changes",[])}
    req(set(changes)==MANAGED,f"managed plan graph drifted: {sorted(changes)}")
    req(all(x["change"]["actions"]==["create"] for x in changes.values()),"runner deployment is not exact create-only")
    cfg=plan["configuration"]["root_module"]["resources"]
    req({x["address"] for x in cfg}==MANAGED|DATA,"configuration graph drifted")
    req(not any(x.get("provider_config_key","") != "aws" for x in cfg),"provider alias drifted")
    configured={x["address"]:x for x in cfg}
    raw=json.dumps(plan).lower()
    for forbidden in ("secret_string","secret_binary","terraform_remote_state","terraform_data","archive_file","aws_db_instance","aws_db_snapshot","aws_lambda_alias","aws_lambda_invocation"):
        req(forbidden not in raw,f"forbidden plan surface: {forbidden}")
    fn=changes["aws_lambda_function.runner"]["change"]["after"]
    req(fn["publish"] is True and fn["reserved_concurrent_executions"]==1 and fn["timeout"]==900,"runner execution bounds drifted")
    req(fn["architectures"]==["arm64"] and fn["runtime"]=="python3.13" and fn["handler"]=="handler.handler","runner runtime drifted")
    req(fn["filename"]=="./db-migration-runner.zip" and fn["source_code_hash"]==ARCHIVE_BASE64_SHA256,"runner canonical archive digest drifted")
    req(len(fn["vpc_config"])==1 and len(fn["vpc_config"][0]["subnet_ids"])==3 and "security_group_ids" not in fn["vpc_config"][0],"runner VPC planned values drifted")
    vpc_expression=configured["aws_lambda_function.runner"]["expressions"]["vpc_config"]
    req(vpc_expression==[{"security_group_ids":{"references":["aws_security_group.runner.id","aws_security_group.runner"]},"subnet_ids":{"references":["local.private_subnet_ids"]}}],"runner VPC configuration reference drifted")
    req(set(plan.get("output_changes",{}))=={"db_migration_runner_qualified_arn","db_migration_runner_version"},"output set drifted")
    print("db migration runner plan scope: PASS")
if __name__=="__main__": main()
