import unittest

from scripts.run_system import _effective_auto_discover


class TestRunSystemAutoDiscoverFlag(unittest.TestCase):
    def test_cli_true_settings_false(self) -> None:
        self.assertTrue(_effective_auto_discover(True, False))

    def test_cli_false_settings_true(self) -> None:
        self.assertTrue(_effective_auto_discover(False, True))

    def test_both_false(self) -> None:
        self.assertFalse(_effective_auto_discover(False, False))


if __name__ == "__main__":
    unittest.main()
