"""Evaluate the deployed Studio provider configuration with a mocked AWS provider."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StudioAiProviderTests(unittest.TestCase):
    def test_provider_configuration(self):
        with tempfile.TemporaryDirectory(prefix="honua-studio-provider-") as temporary:
            stack = Path(temporary)
            fixture = ROOT / "scripts" / "fixtures" / "studio-ai"
            shutil.copytree(fixture, stack, dirs_exist_ok=True)
            shutil.copy(ROOT / "stacks" / "aws" / "studio-ai.tf", stack)
            variables = (ROOT / "stacks" / "aws" / "variables.tf").read_text()
            blocks = re.findall(
                r'^variable "(?:enable_studio_ai|studio_ai_[^"]+)" \{.*?^\}',
                variables,
                re.MULTILINE | re.DOTALL,
            )
            (stack / "variables.tf").write_text("\n".join(blocks))
            environment = {**os.environ, "TF_IN_AUTOMATION": "1"}
            for arguments in (
                ["init", "-backend=false", "-input=false", "-no-color"],
                ["test", "-no-color"],
            ):
                result = subprocess.run(
                    ["terraform", *arguments], cwd=stack, env=environment,
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
