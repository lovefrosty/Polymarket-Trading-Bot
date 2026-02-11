import unittest

from scripts.run_system import RolloverHealthGate


class TestRolloverHealthGate(unittest.TestCase):
    def test_freeze_triggers_at_threshold(self) -> None:
        gate = RolloverHealthGate(abort_threshold=3, abort_window_ms=600_000, cooldown_ms=120_000)
        self.assertIsNone(gate.note_abort(1_000))
        self.assertIsNone(gate.note_abort(2_000))
        freeze = gate.note_abort(3_000)
        self.assertIsNotNone(freeze)
        assert freeze is not None
        self.assertEqual(freeze["abort_count_window"], 3)
        self.assertTrue(gate.is_frozen(3_001))
        self.assertFalse(gate.is_frozen(123_001))

    def test_window_prunes_old_aborts(self) -> None:
        gate = RolloverHealthGate(abort_threshold=3, abort_window_ms=1_000, cooldown_ms=5_000)
        self.assertIsNone(gate.note_abort(0))
        self.assertIsNone(gate.note_abort(400))
        self.assertIsNone(gate.note_abort(1_200))
        self.assertFalse(gate.is_frozen(1_201))


if __name__ == "__main__":
    unittest.main()
