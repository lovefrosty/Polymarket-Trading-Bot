import unittest

from core.model_fit_offset import predict_proba_offset


class TestOffsetLogitIdentity(unittest.TestCase):
    def test_identity_when_x_zero(self) -> None:
        import numpy as np
        p_market = 0.62
        logit = _logit(p_market)
        X = np.zeros((1, 3))
        offset = np.array([logit])
        w = np.zeros(3)
        b = 0.0
        p_pred = predict_proba_offset(X, offset, w, b)[0]
        self.assertAlmostEqual(p_pred, p_market, places=9)


def _logit(p: float) -> float:
    import math

    return math.log(p / (1.0 - p))


if __name__ == "__main__":
    unittest.main()
