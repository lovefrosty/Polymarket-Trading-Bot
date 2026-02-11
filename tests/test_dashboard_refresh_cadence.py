import unittest

from dashboard.app import RefreshPolicy, should_refresh_heavy


class TestDashboardRefreshCadence(unittest.TestCase):
    def test_should_refresh_heavy_every_n_ticks(self) -> None:
        policy = RefreshPolicy(auto_refresh=True, topbar_refresh_ms=1000, heavy_every_ticks=5)
        self.assertFalse(should_refresh_heavy(1, policy))
        self.assertFalse(should_refresh_heavy(4, policy))
        self.assertTrue(should_refresh_heavy(5, policy))
        self.assertTrue(should_refresh_heavy(10, policy))


if __name__ == "__main__":
    unittest.main()
