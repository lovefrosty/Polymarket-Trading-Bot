import unittest

from core_mm.execution import ExecutionAdapter


class _FakeOrderArgs:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeOrderType:
    GTC = "GTC"


class _FakeClient:
    def __init__(self) -> None:
        self.orders = []
        self.cancels = []
        self.cancel_all_calls = 0
        self.positions = [{"token_id": "token-1", "size": 10}]
        self.fail_post = []

    def create_order(self, order_args):
        self.orders.append(order_args.kwargs)
        return {"signed": True, "order": order_args.kwargs}

    def post_order(self, signed, order_type):
        if self.fail_post:
            exc = self.fail_post.pop(0)
            raise exc
        return {"orderID": "abc123", "orderType": order_type, "signed": signed}

    def cancel(self, order_id=None):
        self.cancels.append(order_id)
        return {"canceled": [order_id]}

    def cancel_all(self):
        self.cancel_all_calls += 1
        return {"canceled": "all"}

    def get_orders(self):
        return [{"order_id": "open-1"}]

    def get_positions(self):
        return list(self.positions)


class _StatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class TestExecutionAdapter(unittest.TestCase):
    def test_place_and_cancel_and_snapshot(self) -> None:
        client = _FakeClient()
        adapter = ExecutionAdapter(client, order_args_type=_FakeOrderArgs, order_type=_FakeOrderType)
        placed = adapter.place_order(token_id="token-1", side="buy", price=0.45, size=5)
        self.assertTrue(placed.success)
        self.assertEqual(placed.payload["orderID"], "abc123")

        canceled = adapter.cancel_order("abc123")
        self.assertTrue(canceled.success)
        self.assertEqual(client.cancels, ["abc123"])

        open_orders = adapter.get_open_orders()
        self.assertTrue(open_orders.success)
        self.assertEqual(open_orders.payload["orders"][0]["order_id"], "open-1")

        positions = adapter.get_positions()
        self.assertTrue(positions.success)
        self.assertEqual(positions.payload["positions"][0]["token_id"], "token-1")

    def test_retryable_error_recovers(self) -> None:
        client = _FakeClient()
        client.fail_post = [_StatusError("429 rate limit", 429)]
        sleeps = []
        adapter = ExecutionAdapter(
            client,
            order_args_type=_FakeOrderArgs,
            order_type=_FakeOrderType,
            sleep_fn=lambda secs: sleeps.append(secs),
        )
        result = adapter.place_order(token_id="token-1", side="buy", price=0.45, size=5)
        self.assertTrue(result.success)
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(adapter.call_log[-1].success, True)

    def test_cancel_all(self) -> None:
        client = _FakeClient()
        adapter = ExecutionAdapter(client, order_args_type=_FakeOrderArgs, order_type=_FakeOrderType)
        result = adapter.cancel_all()
        self.assertTrue(result.success)
        self.assertEqual(client.cancel_all_calls, 1)


if __name__ == "__main__":
    unittest.main()
