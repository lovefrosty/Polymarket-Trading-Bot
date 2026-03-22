import unittest

from core.clob_discovery import FeeRateClient


class TestFeeEnrichment(unittest.TestCase):
    def test_fee_metadata_ok(self) -> None:
        def fetcher(_url, timeout=5):
            return {"fee_rate_bps": 25}

        client = FeeRateClient(fetcher=fetcher, ttl_secs=0, time_fn=lambda: 0.0)
        status, fee_rate = client.get_fee_metadata("t1")
        self.assertEqual(status, "ok")
        self.assertEqual(fee_rate, 25.0)

    def test_fee_metadata_invalid_token(self) -> None:
        def fetcher(_url, timeout=5):
            return {"error": "Invalid token id"}

        client = FeeRateClient(fetcher=fetcher, ttl_secs=0, time_fn=lambda: 0.0)
        status, fee_rate = client.get_fee_metadata("bad")
        self.assertEqual(status, "not_fee_addressable")
        self.assertIsNone(fee_rate)
        self.assertEqual(client.invalid_token_id_count, 1)

    def test_fee_metadata_unknown(self) -> None:
        def fetcher(_url, timeout=5):
            return {"error": "something_else"}

        client = FeeRateClient(fetcher=fetcher, ttl_secs=0, time_fn=lambda: 0.0)
        status, fee_rate = client.get_fee_metadata("t2")
        self.assertEqual(status, "unknown")
        self.assertIsNone(fee_rate)


if __name__ == "__main__":
    unittest.main()
