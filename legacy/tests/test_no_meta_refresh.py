import unittest

from dashboard.app import TERMINAL_CSS


class TestNoMetaRefresh(unittest.TestCase):
    def test_dashboard_css_has_no_meta_refresh(self) -> None:
        self.assertNotIn("http-equiv", TERMINAL_CSS.lower())
        self.assertNotIn("refresh", TERMINAL_CSS.lower())


if __name__ == "__main__":
    unittest.main()
