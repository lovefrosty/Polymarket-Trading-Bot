import unittest

from scripts.run_readonly import _force_observe_mode


class TestRunReadonlyWrapper(unittest.TestCase):
    def test_injects_observe_when_mode_missing(self) -> None:
        args = _force_observe_mode(["--markets", "config/markets.yaml"])
        self.assertEqual(args[:2], ["--mode", "OBSERVE"])
        self.assertIn("--markets", args)

    def test_overrides_explicit_mode_flag(self) -> None:
        args = _force_observe_mode(["--mode", "TRADE", "--log-dir", "logs"])
        self.assertEqual(args[:2], ["--mode", "OBSERVE"])
        self.assertNotIn("TRADE", args)

    def test_overrides_inline_mode_flag(self) -> None:
        args = _force_observe_mode(["--mode=PAPER", "--db-path", "runtime.db"])
        self.assertEqual(args[:2], ["--mode", "OBSERVE"])
        self.assertNotIn("--mode=PAPER", args)


if __name__ == "__main__":
    unittest.main()
