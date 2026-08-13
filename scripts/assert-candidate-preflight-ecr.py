#!/usr/bin/env python3
"""Normalize and fail-closed validate exact ECR image provenance metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ACCOUNT = "585192672263"
REGION = "us-west-2"
REPOSITORY = "honua-server"
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
CONFIG_DIGEST = "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f"
SOURCE_COMMIT = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
ENTRYPOINT = ["/var/task/Honua.Server"]
SCHEMA = "honua-candidate-preflight-ecr-evidence-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("ECR manifest/config proof is missing or malformed") from exc
    require(isinstance(value, dict), "ECR manifest/config proof must be an object")
    return value


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def parse_image(path: Path) -> tuple[dict, dict, str]:
    response = load(path)
    require(set(response) == {"images", "failures"}, "ECR batch response schema drifted")
    require(response["failures"] == [], "ECR batch response contains failures")
    require(isinstance(response["images"], list) and len(response["images"]) == 1, "ECR image proof is not unique")
    image = response["images"][0]
    require(
        set(image) == {"registryId", "repositoryName", "imageId", "imageManifest", "imageManifestMediaType"},
        "ECR image result schema drifted",
    )
    require(image["registryId"] == ACCOUNT and image["repositoryName"] == REPOSITORY, "ECR repository identity drifted")
    require(image["imageId"] == {"imageDigest": IMAGE_DIGEST}, "ECR image digest identity drifted")
    require(image["imageManifestMediaType"] == MANIFEST_MEDIA_TYPE, "ECR manifest media type drifted")
    manifest_text = image["imageManifest"]
    require(isinstance(manifest_text, str), "ECR manifest body is invalid")
    require(sha256_bytes(manifest_text.encode("utf-8")) == IMAGE_DIGEST, "ECR manifest content digest drifted")
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ECR image manifest is malformed") from exc
    require(isinstance(manifest, dict) and manifest.get("schemaVersion") == 2, "OCI manifest schema drifted")
    require(manifest.get("mediaType") == MANIFEST_MEDIA_TYPE, "OCI manifest media type drifted")
    require(
        manifest.get("config")
        == {"mediaType": CONFIG_MEDIA_TYPE, "digest": CONFIG_DIGEST, "size": 6052},
        "OCI config descriptor drifted",
    )
    require(isinstance(manifest.get("layers"), list) and manifest["layers"], "OCI layers are missing")
    return image, manifest, manifest_text


def config_digest(path: Path) -> str:
    _, manifest, _ = parse_image(path)
    return manifest["config"]["digest"]


def normalize(image_path: Path, config_path: Path) -> dict:
    _, manifest, manifest_text = parse_image(image_path)
    raw_config = config_path.read_bytes()
    require(sha256_bytes(raw_config) == CONFIG_DIGEST, "OCI config blob digest drifted")
    try:
        config = json.loads(raw_config)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OCI config blob is malformed") from exc
    require(isinstance(config, dict), "OCI config blob must be an object")
    require(config.get("architecture") == "arm64" and config.get("os") == "linux", "OCI platform drifted")
    runtime = config.get("config")
    require(isinstance(runtime, dict), "OCI runtime config is missing")
    require(runtime.get("Entrypoint") == ENTRYPOINT, "OCI entrypoint drifted")
    require(runtime.get("Cmd") is None and runtime.get("WorkingDir") == "/var/task", "OCI command/working directory drifted")
    labels = runtime.get("Labels")
    require(isinstance(labels, dict), "OCI labels are missing")
    require(labels.get("honua.runtime.compilation") == "native-aot", "native-AOT label drifted")
    require(labels.get("honua.runtime.entrypoint") == ENTRYPOINT[0], "runtime entrypoint label drifted")
    require(labels.get("org.opencontainers.image.revision") == SOURCE_COMMIT, "OCI source revision drifted")
    require(labels.get("org.opencontainers.image.source") == "https://github.com/honua-io/honua-server", "OCI source repository drifted")
    env = runtime.get("Env")
    require(isinstance(env, list) and all(isinstance(item, str) for item in env), "OCI environment metadata drifted")
    git_sha = [item for item in env if item.startswith("HONUA_GIT_SHA=")]
    require(git_sha == [f"HONUA_GIT_SHA={SOURCE_COMMIT}"], "OCI HONUA_GIT_SHA proof drifted")
    return {
        "schema": SCHEMA,
        "registryId": ACCOUNT,
        "region": REGION,
        "repositoryName": REPOSITORY,
        "imageDigest": IMAGE_DIGEST,
        "manifestMediaType": MANIFEST_MEDIA_TYPE,
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "config": {
            "digest": CONFIG_DIGEST,
            "mediaType": CONFIG_MEDIA_TYPE,
            "size": manifest["config"]["size"],
            "sha256": sha256_bytes(raw_config),
            "architecture": config["architecture"],
            "os": config["os"],
            "entrypoint": runtime["Entrypoint"],
            "cmd": runtime.get("Cmd"),
            "workingDir": runtime["WorkingDir"],
            "nativeAot": labels["honua.runtime.compilation"],
            "runtimeEntrypoint": labels["honua.runtime.entrypoint"],
            "ociRevision": labels["org.opencontainers.image.revision"],
            "source": labels["org.opencontainers.image.source"],
            "honuaGitSha": SOURCE_COMMIT,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    digest_parser = subparsers.add_parser("config-digest")
    digest_parser.add_argument("--image", type=Path, required=True)
    assert_parser = subparsers.add_parser("assert")
    assert_parser.add_argument("--image", type=Path, required=True)
    assert_parser.add_argument("--config", type=Path, required=True)
    assert_parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "config-digest":
        print(config_digest(args.image))
        return
    evidence = normalize(args.image, args.config)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("candidate-preflight ECR provenance: PASS")


if __name__ == "__main__":
    main()
