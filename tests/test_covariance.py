import unittest

from src.features.covariance import CovarianceEstimator, ReturnSample


class TestCovariance(unittest.TestCase):
    def test_diagonal_only_for_small_samples(self) -> None:
        estimator = CovarianceEstimator(
            winsor_limit=10.0,
            shrinkage_lambda=0.01,
            adaptive_shrinkage_scale=0.1,
            min_eigenvalue=1e-6,
            max_condition_number=1e6,
        )
        samples = [ReturnSample([0.1, -0.2], event_ts=i) for i in range(9)]
        result = estimator.estimate(samples, as_of_ts=100)
        self.assertTrue(result.diagonal_only)
        self.assertFalse(result.whitening_allowed)
        self.assertEqual(result.sample_count, 9)
        self.assertEqual(result.dims, 2)


if __name__ == "__main__":
    unittest.main()
