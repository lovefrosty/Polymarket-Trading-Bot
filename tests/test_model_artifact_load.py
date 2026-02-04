import unittest
from pathlib import Path

from core.model_artifact import load_model


class TestModelArtifactLoad(unittest.TestCase):
    def test_load_model(self) -> None:
        path = Path(__file__).parent / "fixtures" / "model_artifact.json"
        model = load_model(path)
        self.assertEqual(model.schema_version, "model_ridge_logit_v1")
        self.assertEqual(len(model.feature_order), len(model.w))
        self.assertIsNotNone(model.platt)
        self.assertAlmostEqual(model.b, 0.0)


if __name__ == "__main__":
    unittest.main()
