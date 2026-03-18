import unittest

from core.market_time import parse_end_epoch_from_slug, window_start_end_ms


class TestLabelingFromSlugAndReference(unittest.TestCase):
    def test_parse_end_epoch(self) -> None:
        slug = "btc-updown-15m-1768014000"
        end_sec = parse_end_epoch_from_slug(slug)
        self.assertEqual(end_sec, 1768014000)

    def test_window_start_end_ms(self) -> None:
        slug = "btc-updown-15m-1768014000"
        window = window_start_end_ms(slug)
        self.assertIsNotNone(window)
        start_ms, end_ms = window
        self.assertEqual(end_ms, 1768014000 * 1000)
        self.assertEqual(start_ms, end_ms - 15 * 60 * 1000)

    def test_reference_labeling(self) -> None:
        end_sec = 1768014000
        start_ms = (end_sec - 15 * 60) * 1000
        end_ms = end_sec * 1000
        p_start = 100.0
        p_end_up = 101.0
        p_end_down = 99.0
        y_up = 1 if p_end_up >= p_start else 0
        y_down = 1 - y_up
        self.assertEqual(y_up, 1)
        self.assertEqual(y_down, 0)
        y_up2 = 1 if p_end_down >= p_start else 0
        y_down2 = 1 - y_up2
        self.assertEqual(y_up2, 0)
        self.assertEqual(y_down2, 1)
        self.assertTrue(start_ms < end_ms)


if __name__ == "__main__":
    unittest.main()
