import unittest
from reproducible_build_pinner import (
    ReproducibleBuildPinnerEngine, DependencyAuditReport
)

class TestReproducibleBuildPinnerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine(target_python_version="3.11.8")

    def test_unpinned_dependencies_detected_and_score_reduced(self):
        reqs = [
            "pandas>=2.0.0",                                                   # Unpinned
            "numpy",                                                           # Unpinned
            "ccxt==4.2.0 --hash=sha256:abcd1234efgh5678abcd1234efgh5678"        # Pinned with Hash!
        ]
        report = self.engine.audit_requirements_file(reqs)
        
        self.assertEqual(report.total_packages_audited, 3)
        self.assertEqual(report.pinned_packages_count, 1)
        self.assertEqual(report.unpinned_packages_count, 2)
        self.assertEqual(report.unpinned_packages, ["pandas", "numpy"])
        self.assertFalse(report.is_reproducible_build_guaranteed)
        self.assertEqual(report.reproducibility_score, 30.0) # 100 - (2*25 + 2*10) = 30.0

    def test_fully_pinned_lockfile_guarantees_reproducibility(self):
        reqs = [
            "pandas==2.2.1 --hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "numpy==1.26.4 --hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        ]
        report = self.engine.audit_requirements_file(reqs)
        
        self.assertTrue(report.is_reproducible_build_guaranteed)
        self.assertEqual(report.reproducibility_score, 100.0)

if __name__ == '__main__':
    unittest.main()
