import unittest

from scripts.run_system import _candidate_rejection_reason, _switch_abort_rejection_reason


class TestRolloverRejectionCauseSplit(unittest.TestCase):
    def test_candidate_metadata_reasons_mapped(self) -> None:
        self.assertEqual(_candidate_rejection_reason("CANDIDATE_CLOSED"), "META_NON_TRADABLE_CLOSED")
        self.assertEqual(_candidate_rejection_reason("CANDIDATE_INACTIVE"), "META_NON_TRADABLE_INACTIVE")
        self.assertEqual(
            _candidate_rejection_reason("CANDIDATE_NOT_ACCEPTING_ORDERS"),
            "META_NON_TRADABLE_NOT_ACCEPTING",
        )

    def test_switch_abort_ws_reason_mapped(self) -> None:
        self.assertEqual(_switch_abort_rejection_reason("CONFIRM_TIMEOUT"), "WS_NOT_LIVE_CONFIRM_TIMEOUT")
        self.assertEqual(_switch_abort_rejection_reason("SWITCH_ABORT"), "WS_NOT_LIVE_SWITCH_ABORT")


if __name__ == "__main__":
    unittest.main()

