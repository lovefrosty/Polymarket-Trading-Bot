import unittest

from src.execution.execution_engine import ExecutionDecision, OrderIntent, submit_order


class _DummyBroker:
    def __init__(self) -> None:
        self.sent = False
        self.payload = None

    def send(self, payload):
        self.sent = True
        self.payload = payload
        return "ok"


class TestSubmitOrder(unittest.TestCase):
    def test_submit_blocks_when_decision_disallows(self) -> None:
        broker = _DummyBroker()
        decision = ExecutionDecision(allow=False, reasons=["block"])
        intent = OrderIntent(broker=broker, payload={"id": 1})
        with self.assertRaises(ValueError):
            submit_order(decision, intent)
        self.assertFalse(broker.sent)

    def test_submit_sends_when_allowed(self) -> None:
        broker = _DummyBroker()
        decision = ExecutionDecision(allow=True, reasons=[])
        intent = OrderIntent(broker=broker, payload={"id": 2})
        result = submit_order(decision, intent)
        self.assertTrue(broker.sent)
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()
