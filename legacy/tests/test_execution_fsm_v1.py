import unittest

from core.execution_fsm import ExecutionFSM, ExecutionState


class TestExecutionFSMV1(unittest.TestCase):
    def test_moves_to_one_leg_on_fill(self) -> None:
        fsm = ExecutionFSM(rebalance_timeout_ms=5000)
        fsm.on_fill(side="buy", qty=1.0, ts_ms=1000)
        status = fsm.status()
        self.assertEqual(status.state, ExecutionState.ONE_SIDE_FILLED)
        self.assertIsNotNone(status.one_leg_since_ms)

    def test_timeout_triggers_unwind(self) -> None:
        fsm = ExecutionFSM(rebalance_timeout_ms=100)
        fsm.on_fill(side="buy", qty=1.0, ts_ms=1000)
        triggered = fsm.on_rebalance_tick(now_ms=1200)
        self.assertTrue(triggered)
        self.assertEqual(fsm.status().state, ExecutionState.UNWINDING)

    def test_returns_to_quote_when_flat(self) -> None:
        fsm = ExecutionFSM(rebalance_timeout_ms=5000)
        fsm.on_fill(side="buy", qty=1.0, ts_ms=1000)
        fsm.on_fill(side="sell", qty=1.0, ts_ms=1010)
        status = fsm.status()
        self.assertEqual(status.state, ExecutionState.QUOTING_BOTH)


if __name__ == "__main__":
    unittest.main()
