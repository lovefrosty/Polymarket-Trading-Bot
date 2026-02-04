import unittest

from src.execution.state_machine import BrokerState, HedgeState, HedgeStateMachine


class TestHedgeUnwind(unittest.TestCase):
    def test_unwind_after_hedge_timeout(self) -> None:
        machine = HedgeStateMachine(hedge_timeout_ms=5000)
        broker_state = BrokerState(primary_position=1.0, hedge_position=0.0, ts=1000)

        machine.submit_primary(ts=1000)
        machine.primary_filled(filled_qty=1.0, ts=1000, broker_state=broker_state)
        unwind_intent = machine.tick(ts=6001, broker_state=broker_state)

        self.assertEqual(machine.status.state, HedgeState.UNWINDING)
        self.assertIsNotNone(unwind_intent)
        self.assertTrue(machine.block_new_exposure(broker_state))


if __name__ == "__main__":
    unittest.main()
