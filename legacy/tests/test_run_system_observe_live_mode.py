import unittest

from scripts.run_system import _resolve_runtime_mode


class TestRunSystemObserveLiveMode(unittest.TestCase):
    def test_observe_live_alias_maps_to_observe(self) -> None:
        mode, alias = _resolve_runtime_mode("OBSERVE_LIVE")
        self.assertEqual(mode, "OBSERVE")
        self.assertTrue(alias)

    def test_other_modes_unchanged(self) -> None:
        mode, alias = _resolve_runtime_mode("TRADE")
        self.assertEqual(mode, "TRADE")
        self.assertFalse(alias)


if __name__ == "__main__":
    unittest.main()
