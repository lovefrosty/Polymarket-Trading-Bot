from __future__ import annotations

import unittest

from core.market_discovery import GammaFetchError
from scripts.run_system import _build_discovery_error_payload


class _State:
    reference_symbol = "BTC"


class TestRuntimeDiscoveryErrorPayload(unittest.TestCase):
    def test_gamma_timeout_payload_includes_retry_metadata(self) -> None:
        payload = _build_discovery_error_payload(
            exc=GammaFetchError(
                url="https://gamma-api.polymarket.com/events?active=true&limit=1000&offset=0",
                error_code="TIMEOUT",
                error_detail="timed out",
            ),
            current_state=_State(),
            discovery_summary={"discovery_requests": [{"n_total": 10, "n_btc_15m": 3, "n_active_now": 1}]},
            now_ms=100_000,
            retry_index=2,
            next_retry_ts_ms=105_000,
        )
        self.assertEqual(payload["error_code"], "TIMEOUT")
        self.assertEqual(payload["retry_index"], 2)
        self.assertEqual(payload["next_retry_ts_ms"], 105_000)
        self.assertEqual(payload["requested_symbol"], "BTC")
        self.assertEqual(payload["requested_horizon"], "15m")
        self.assertEqual(payload["n_total"], 10)
        self.assertEqual(payload["n_btc_15m"], 3)
        self.assertEqual(payload["n_active_now"], 1)


if __name__ == "__main__":
    unittest.main()
