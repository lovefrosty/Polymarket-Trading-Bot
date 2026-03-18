import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestRuntimeImportBoundary(unittest.TestCase):
    def test_no_training_modules_in_runtime_import_graph(self) -> None:
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys; "
            "import scripts.run_readonly; "
            "forbidden={'core.model_fit','scripts.train_model'}; "
            "assert forbidden.isdisjoint(sys.modules)"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root)
        env["RUNTIME_MODE"] = "test"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
