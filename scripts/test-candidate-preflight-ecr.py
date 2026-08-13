#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-candidate-preflight-ecr.py"
FIXTURE = ROOT / "scripts" / "fixtures" / "candidate-preflight"


def load_module():
    spec = importlib.util.spec_from_file_location("ecr_assertion", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load ECR assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ECR = load_module()


class CandidatePreflightEcrTests(unittest.TestCase):
    def setUp(self):
        self.image_path = FIXTURE / "ecr-image-67d96f75.json"
        self.config_path = FIXTURE / "ecr-config-c57f3a4a.json"

    def test_exact_registry_metadata_passes(self):
        evidence = ECR.normalize(self.image_path, self.config_path)
        self.assertEqual(ECR.IMAGE_DIGEST, evidence["manifestSha256"])
        self.assertEqual(ECR.SOURCE_COMMIT, evidence["config"]["ociRevision"])
        self.assertEqual(ECR.SOURCE_COMMIT, evidence["config"]["honuaGitSha"])
        self.assertEqual("native-aot", evidence["config"]["nativeAot"])

    def test_wrong_or_missing_manifest_and_config_proof_fails_closed(self):
        image = json.loads(self.image_path.read_text(encoding="utf-8"))
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        mutations = {
            "image digest": ("image", lambda value: value["images"][0]["imageId"].update(imageDigest="sha256:" + "0" * 64)),
            "manifest body": ("image", lambda value: value["images"][0].update(imageManifest="{}")),
            "architecture": ("config", lambda value: value.update(architecture="amd64")),
            "entrypoint": ("config", lambda value: value["config"].update(Entrypoint=["/bin/false"])),
            "native AOT": ("config", lambda value: value["config"]["Labels"].update(**{"honua.runtime.compilation": "framework-dependent"})),
            "OCI revision": ("config", lambda value: value["config"]["Labels"].update(**{"org.opencontainers.image.revision": "0" * 40})),
            "HONUA_GIT_SHA": ("config", lambda value: value["config"].update(Env=[item for item in value["config"]["Env"] if not item.startswith("HONUA_GIT_SHA=")])),
        }
        for label, (kind, mutate) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                image_value, config_value = copy.deepcopy(image), copy.deepcopy(config)
                mutate(image_value if kind == "image" else config_value)
                image_file, config_file = root / "image.json", root / "config.json"
                image_file.write_text(json.dumps(image_value), encoding="utf-8")
                config_file.write_text(json.dumps(config_value), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    ECR.normalize(image_file, config_file)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                ECR.normalize(Path(temporary) / "missing.json", self.config_path)


if __name__ == "__main__":
    unittest.main()
