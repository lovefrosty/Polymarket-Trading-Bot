import os
import unittest
from unittest.mock import patch

from scripts.run_system import _apply_paper_experiment_profile


class TestPaperExperimentProfile(unittest.TestCase):
    def test_aggressive_profile_is_paper_only(self) -> None:
        constitution = {"trading": {}, "policy": {}, "execution": {}}
        with patch.dict(os.environ, {"PAPER_EXPERIMENT_PROFILE": "aggressive_two_sided"}, clear=False):
            profile = _apply_paper_experiment_profile("OBSERVE", constitution)
        self.assertIsNone(profile)
        self.assertNotIn("paper_experiment_profile", constitution["trading"])

    def test_aggressive_profile_mutates_policy_for_paper(self) -> None:
        constitution = {"trading": {}, "policy": {}, "execution": {}}
        with patch.dict(os.environ, {"PAPER_EXPERIMENT_PROFILE": "aggressive_two_sided"}, clear=False):
            profile = _apply_paper_experiment_profile("PAPER", constitution)
        self.assertEqual(profile, "aggressive_two_sided")
        self.assertEqual(constitution["policy"]["max_spread_bps"], 750.0)
        self.assertEqual(constitution["policy"]["max_slippage_bps"], 300.0)
        self.assertEqual(constitution["execution"]["maker_half_spread_bps"], 25.0)
        self.assertEqual(constitution["trading"]["paper_experiment_profile"], "aggressive_two_sided")


if __name__ == "__main__":
    unittest.main()
