"""Bind source declarations without treating catalogue references as seed receipts."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

PROVENANCE_PATH = Path(__file__).with_name("source-provenance.v1.json")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"source provenance: {message}")


def valid_hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_entry(identifier: str, entry: dict) -> None:
    require(entry.get("status") in {"verified", "missing-seed-receipts"}, f"{identifier}: status")
    sources = entry.get("sources")
    require(isinstance(sources, list) and bool(sources), f"{identifier}: sources")
    for source in sources:
        url = urlparse(source.get("url", ""))
        require(url.scheme in {"https", "s3"} and bool(url.netloc), f"{identifier}: source URL")
        require(source.get("role") in {"seed-reference", "catalogue", "attribution", "input"}, f"{identifier}: source role")
        require(source.get("sha256") is None or valid_hash(source["sha256"]), f"{identifier}: source hash")
    license_record = entry.get("license", {})
    for key in ("id", "url", "attribution"):
        require(isinstance(license_record.get(key), str) and bool(license_record[key].strip()), f"{identifier}: license {key}")
    obligations = license_record.get("obligations")
    require(isinstance(obligations, list) and bool(obligations) and all(isinstance(item, str) and item.strip() for item in obligations), f"{identifier}: obligations")
    require(bool(entry.get("processing", {}).get("description")), f"{identifier}: processing")
    artifact = entry.get("artifact", {})
    require(artifact.get("sha256") is None or valid_hash(artifact["sha256"]), f"{identifier}: artifact hash")
    if entry["status"] == "verified":
        inputs = [source for source in sources if source["role"] == "input"]
        require(bool(inputs) and all(valid_hash(source.get("sha256")) for source in inputs), f"{identifier}: verified input receipts")
        require(valid_hash(artifact.get("sha256")) and bool(entry.get("vintage")), f"{identifier}: verified artifact and vintage")
    else:
        require(isinstance(entry.get("gap"), str) and bool(entry["gap"].strip()), f"{identifier}: missing receipt gap")


def bind_provenance(services: list, assets: dict, path: Path = PROVENANCE_PATH) -> str:
    raw = path.read_bytes()
    definition = json.loads(raw)
    require(definition.get("format") == "honua.demo-source-provenance.v1", "format")
    entries = definition.get("entries", {})
    targets = {service["id"]: service for service in services if service["id"] != "demo-stac"}
    targets.update(assets)
    require(set(entries) == set(targets), "entries must exactly cover non-STAC services and assets")
    for identifier, entry in entries.items():
        validate_entry(identifier, entry)
    for identifier, target in targets.items():
        target["provenance"] = entries[identifier]
        target["attribution"] = entries[identifier]["license"]["attribution"]
    return hashlib.sha256(raw).hexdigest()
