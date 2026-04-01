from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


_LEGACY_ROOT = Path(__file__).resolve().parents[1]
_LEGACY_DATA_ROOT = _LEGACY_ROOT / "data"
if str(_LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_ROOT))
if str(_LEGACY_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_DATA_ROOT))

_scripts_pkg = types.ModuleType("scripts")
_walkforward_stub = types.ModuleType("scripts.walkforward_report")
_walkforward_stub.generate_report = lambda *args, **kwargs: None
_walkforward_stub.write_report = lambda *args, **kwargs: None
_scripts_pkg.walkforward_report = _walkforward_stub
sys.modules.setdefault("scripts", _scripts_pkg)
sys.modules.setdefault("scripts.walkforward_report", _walkforward_stub)

_MODULE_PATH = _LEGACY_ROOT / "scripts" / "replay_runner.py"
_SPEC = spec_from_file_location("legacy_replay_runner", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
replay_runner = module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay_runner)


class TestReplayRunnerCLIExecGuard(unittest.TestCase):
    def test_cli_exec_is_forbidden(self) -> None:
        args = mock.Mock(cli_exec=True)
        with mock.patch.object(replay_runner, "_parse_args", return_value=args):
            with self.assertRaisesRegex(ValueError, "replay_cli_exec_forbidden"):
                replay_runner.main()


if __name__ == "__main__":
    unittest.main()
