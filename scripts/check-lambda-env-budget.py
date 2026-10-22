#!/usr/bin/env python3
"""Fail when the planned serving-Lambda environment exceeds the AWS 4096-byte limit.

Usage: terraform show -json plan.tfplan | scripts/check-lambda-env-budget.py [--limit 4096]

AWS counts the JSON-serialized environment: each variable costs
len(key) + len(value) + 6 bytes of framing. Only names and sizes are printed.
"""
import json
import sys

LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 4096
ADDRESS = "module.honua.aws_lambda_function.this"

plan = json.load(sys.stdin)
for change in plan.get("resource_changes", []):
    if change["address"] != ADDRESS:
        continue
    after = change["change"].get("after") or {}
    env = (after.get("environment") or [{}])[0].get("variables") or {}
    size = sum(len(k.encode()) + len(v.encode()) + 6 for k, v in env.items())
    print(f"{ADDRESS}: {len(env)} variables, {size} bytes of {LIMIT}")
    for cost, key in sorted(((len(k) + len(v) + 6, k) for k, v in env.items()), reverse=True)[:5]:
        print(f"  {cost:4d}  {key}")
    sys.exit(0 if size <= LIMIT else 1)
print(f"{ADDRESS} not in plan", file=sys.stderr)
sys.exit(2)
