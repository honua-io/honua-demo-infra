#!/usr/bin/env python3
"""Validate both AWS roots and the pinned private-module caller interface.

The root resources and provider schemas remain real. CI replaces only the
credential-inaccessible private module with a checked-in typed caller-interface
stub pinned to the same source ref. This does not validate private module
implementation behavior.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STACK = REPOSITORY_ROOT / "stacks" / "aws"
CANDIDATE_STACK = REPOSITORY_ROOT / "stacks" / "aws-candidate-preflight"
MIGRATION_STACK = REPOSITORY_ROOT / "stacks" / "aws-db-migration-runner"
STACKS = REPOSITORY_ROOT / "stacks"
INTERFACE_STUB = STACK / "validation" / "honua-module-interface"


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


def bind_interface_stub(stack: Path) -> None:
    contract = json.loads(
        (stack / "validation" / "honua-module-interface" / "interface-contract.json").read_text(
            encoding="utf-8"
        )
    )
    main_path = stack / "main.tf"
    main_text = main_path.read_text(encoding="utf-8")
    start, end = honua_module_span(main_text)
    block = main_text[start:end]
    source_match = re.search(r'(?m)^  source\s*=\s*"([^"]+)"\s*$', block)
    if source_match is None:
        raise RuntimeError('module "honua" must have one single-line source')
    if source_match.group(1) != contract["source"]:
        raise RuntimeError(
            "honua-iac source changed without refreshing the checked-in typed interface contract"
        )

    block = block[: source_match.start(1)] + "./validation/honua-module-interface" + block[source_match.end(1) :]
    main_path.write_text(main_text[:start] + block + main_text[end:], encoding="utf-8")


def run(command: list[str], *, cwd: Path | None = None) -> None:
    environment = os.environ.copy()
    environment["TF_IN_AUTOMATION"] = "1"
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def assert_invalid_contract(stack: Path, mutated_main: str, expected_diagnostic: str) -> None:
    main_path = stack / "main.tf"
    original = main_path.read_text(encoding="utf-8")
    main_path.write_text(mutated_main, encoding="utf-8")
    environment = os.environ.copy()
    environment["TF_IN_AUTOMATION"] = "1"
    try:
        result = subprocess.run(
            ["terraform", "validate", "-no-color"],
            cwd=stack,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        main_path.write_text(original, encoding="utf-8")

    diagnostics = result.stdout + result.stderr
    if result.returncode == 0:
        raise RuntimeError("typed interface negative regression unexpectedly validated")
    if expected_diagnostic not in diagnostics:
        raise RuntimeError(
            f"typed interface negative regression lacked {expected_diagnostic!r}:\n{diagnostics}"
        )


def validate_negative_contracts(stack: Path) -> None:
    main_text = (stack / "main.tf").read_text(encoding="utf-8")

    unknown_argument = main_text.replace(
        "  name_prefix = var.name_prefix",
        "  unsupported_name_prefix = var.name_prefix",
        1,
    )
    if unknown_argument == main_text:
        raise RuntimeError("could not inject unknown module argument regression")
    assert_invalid_contract(stack, unknown_argument, "Unsupported argument")

    wrong_type = main_text.replace(
        "  lambda_memory_size   = var.lambda_memory_size",
        '  lambda_memory_size   = "wrong-type"',
        1,
    )
    if wrong_type == main_text:
        raise RuntimeError("could not inject wrong-type module argument regression")
    assert_invalid_contract(stack, wrong_type, "Invalid value for input variable")


def validate_stac_environment_contract() -> None:
    """Execute the actual input validation without providers, credentials or resources."""
    variables = (STACK / "variables.tf").read_text(encoding="utf-8")
    match = re.search(
        r'^variable "stac_seed_metadata_environment" \{.*?^\}', variables,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise RuntimeError("STAC metadata environment input was not found")
    with tempfile.TemporaryDirectory(prefix="honua-stac-environment-") as temporary:
        root = Path(temporary)
        (root / "main.tf").write_text(
            match.group(0) + '\noutput "environment" { value = var.stac_seed_metadata_environment }\n',
            encoding="utf-8",
        )
        run(["terraform", "init", "-backend=false", "-input=false", "-no-color"], cwd=root)
        for value in ("Production", "default", "production", "staging", ""):
            result = subprocess.run(
                ["terraform", "plan", "-input=false", "-no-color",
                 f"-var=stac_seed_metadata_environment={value}"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            diagnostics = result.stdout + result.stderr
            if value == "Production":
                if result.returncode != 0:
                    raise RuntimeError(f"valid STAC environment rejected:\n{diagnostics}")
            elif result.returncode == 0 or "Invalid value for variable" not in diagnostics:
                raise RuntimeError(f"STAC environment {value!r} was not rejected:\n{diagnostics}")
            print(f"STAC environment {value!r}: expected plan exit {result.returncode}")


def main() -> None:
    if not INTERFACE_STUB.is_dir():
        raise RuntimeError("checked-in honua module interface stub is missing")
    run(["terraform", "fmt", "-check", "-recursive", str(STACKS)])
    validate_stac_environment_contract()
    with tempfile.TemporaryDirectory(prefix="honua-terraform-validate-") as temporary:
        validation_root = Path(temporary) / "repository"
        validation_stack = validation_root / "stacks" / "aws"
        validation_candidate_stack = validation_root / "stacks" / "aws-candidate-preflight"
        validation_migration_stack = validation_root / "stacks" / "aws-db-migration-runner"
        shutil.copytree(
            STACKS,
            validation_root / "stacks",
            ignore=shutil.ignore_patterns(".terraform", "terraform.tfstate", "terraform.tfstate.*"),
        )
        shutil.copytree(REPOSITORY_ROOT / "manifest", validation_root / "manifest")
        bind_interface_stub(validation_stack)
        run(
            [
                sys.executable,
                str(validation_migration_stack / "runner" / "build.py"),
                "--source",
                str(validation_migration_stack / "runner"),
                "--output",
                str(validation_migration_stack / "db-migration-runner.zip"),
            ]
        )
        run(["terraform", "init", "-backend=false", "-input=false", "-no-color"], cwd=validation_stack)
        run(["terraform", "validate", "-no-color"], cwd=validation_stack)
        validate_negative_contracts(validation_stack)
        run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            cwd=validation_candidate_stack,
        )
        run(["terraform", "validate", "-no-color"], cwd=validation_candidate_stack)
        run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            cwd=validation_migration_stack,
        )
        run(["terraform", "validate", "-no-color"], cwd=validation_migration_stack)


if __name__ == "__main__":
    main()
