#!/usr/bin/env python3
"""Validate the AWS root stack without downloading the private honua-iac module.

The root resources and provider schemas remain real. Only the private module is
replaced, in a temporary copy, by an explicit output-interface stub. This makes
CI validation independent of repository credentials while failing closed when
the root stack consumes a module output not represented here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STACK = REPOSITORY_ROOT / "stacks" / "aws"

MODULE_OUTPUTS = {
    "api_endpoint": '"https://validation.execute-api.us-east-1.amazonaws.com"',
    "control_plane_backend_name": '"validation-backend"',
    "control_plane_target_id": '"validation-target"',
    "control_plane_target_kind": '"lambda"',
    "db_connection_secret_arn": '"arn:aws:secretsmanager:us-east-1:000000000000:secret:validation-db"',
    "db_connection_string": '"Host=validation.cluster.local;Database=honua;Username=honua;Password=validation"',
    "db_endpoint": '"validation.cluster.local"',
    "gp_batch_enabled": "false",
    "gp_job_definition_arns": "{}",
    "gp_job_queue_arn": '"arn:aws:batch:us-east-1:000000000000:job-queue/validation"',
    "lambda_alias_arn": '"arn:aws:lambda:us-east-1:000000000000:function:validation:live"',
    "lambda_alias_name": '"live"',
    "lambda_function_arn": '"arn:aws:lambda:us-east-1:000000000000:function:validation"',
    "lambda_function_name": '"validation"',
    "private_route_table_ids": '["rtb-00000000000000000"]',
    "private_subnet_ids": '["subnet-00000000000000000", "subnet-00000000000000001"]',
    "pro_license_enabled": "false",
    "pro_license_secret_arn": '"arn:aws:secretsmanager:us-east-1:000000000000:secret:validation-pro"',
    "redis_connection_secret_arn": '"arn:aws:secretsmanager:us-east-1:000000000000:secret:validation-redis"',
    "vpc_id": '"vpc-00000000000000000"',
}


def honua_module_span(text: str) -> tuple[int, int]:
    match = re.search(r'^module\s+"honua"\s*\{', text, re.MULTILINE)
    if match is None:
        raise RuntimeError('module "honua" was not found in stacks/aws/main.tf')

    depth = 0
    for index in range(match.start(), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    raise RuntimeError('module "honua" has no closing brace')


def write_interface_stub(stack: Path) -> None:
    main_path = stack / "main.tf"
    main_text = main_path.read_text(encoding="utf-8")
    start, end = honua_module_span(main_text)
    block = main_text[start:end]

    arguments = set(re.findall(r"(?m)^  ([A-Za-z_][A-Za-z0-9_]*)\s*=", block))
    arguments -= {"source", "version", "providers", "count", "for_each", "depends_on"}

    block, replacements = re.subn(
        r'(?m)^(  source\s*=\s*)"[^"]+"\s*$',
        r'\1"./validation-honua-module"',
        block,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError('module "honua" must have exactly one single-line source')
    block = re.sub(r"(?m)^  version\s*=.*\n?", "", block)
    main_path.write_text(main_text[:start] + block + main_text[end:], encoding="utf-8")

    referenced_outputs: set[str] = set()
    for terraform_file in stack.glob("*.tf"):
        referenced_outputs.update(
            re.findall(
                r"module\.honua\.([A-Za-z_][A-Za-z0-9_]*)",
                terraform_file.read_text(encoding="utf-8"),
            )
        )
    unknown = referenced_outputs - MODULE_OUTPUTS.keys()
    if unknown:
        raise RuntimeError(f"validation stub lacks module outputs: {', '.join(sorted(unknown))}")

    stub = stack / "validation-honua-module"
    stub.mkdir()
    variables = "\n".join(f'variable "{name}" {{\n  type = any\n}}\n' for name in sorted(arguments))
    outputs = "\n".join(
        f'output "{name}" {{\n  value = {MODULE_OUTPUTS[name]}\n}}\n'
        for name in sorted(referenced_outputs)
    )
    (stub / "variables.tf").write_text(variables, encoding="utf-8")
    (stub / "outputs.tf").write_text(outputs, encoding="utf-8")


def run(command: list[str], *, cwd: Path | None = None) -> None:
    environment = os.environ.copy()
    environment["TF_IN_AUTOMATION"] = "1"
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def main() -> None:
    run(["terraform", "fmt", "-check", "-recursive", str(STACK)])
    with tempfile.TemporaryDirectory(prefix="honua-terraform-validate-") as temporary:
        validation_root = Path(temporary) / "repository"
        validation_stack = validation_root / "stacks" / "aws"
        shutil.copytree(
            STACK,
            validation_stack,
            ignore=shutil.ignore_patterns(".terraform", "terraform.tfstate", "terraform.tfstate.*"),
        )
        shutil.copytree(REPOSITORY_ROOT / "manifest", validation_root / "manifest")
        write_interface_stub(validation_stack)
        run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            cwd=validation_stack,
        )
        run(["terraform", "validate", "-no-color"], cwd=validation_stack)


if __name__ == "__main__":
    main()
