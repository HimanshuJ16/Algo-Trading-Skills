import unittest
from blameless_postmortem_generator import (
    BlamelessPostmortemGenerator, Config,
    BlamelessPostmortemInput, BlamelessPostmortemReport
)

class TestBlamelessPostmortemGenerator(unittest.TestCase):

    def test_init_legacy(self):
        obj = BlamelessPostmortemGenerator(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process_legacy(self):
        obj = BlamelessPostmortemGenerator()
        self.assertTrue(BlamelessPostmortemGenerator.process())

    def test_blameless_postmortem_approved(self):
        inp = BlamelessPostmortemInput(
            incident_id="INC-2026-99",
            incident_date="2026-07-31",
            summary="API gateway latency spike caused order execution timeout.",
            systemic_factors=[
                "Network buffer size was undersized for peak market volatility.",
                "Staging load test suite omitted microsecond burst traffic."
            ],
            narrative="During high market volatility, incoming message rates exceeded TCP buffer capacity, causing 50ms latency gapping.",
            proposed_actions=[
                "Increase TCP socket buffer sizes on execution gateway hosts.",
                "Add burst traffic simulation to staging CI/CD pipeline."
            ]
        )
        generator = BlamelessPostmortemGenerator()
        report = generator.generate_blameless_postmortem(inp)

        self.assertEqual(report.status, "BLAMELESS_POSTMORTEM_APPROVED")
        self.assertTrue(report.is_approved)
        self.assertFalse(report.blame_detected)
        self.assertIn("# BLAMELESS POST-MORTEM REPORT: INC-2026-99", report.markdown_document)

    def test_blame_language_detected(self):
        inp = BlamelessPostmortemInput(
            incident_id="INC-2026-BAD",
            incident_date="2026-07-31",
            summary="Order routing failure.",
            systemic_factors=["Lack of validation"],
            narrative="Developer forgot to set the limit parameter and was careless in deployment.",
            proposed_actions=["Fix parameter"]
        )
        generator = BlamelessPostmortemGenerator(Config(strict_blame_check=True))
        report = generator.generate_blameless_postmortem(inp)

        self.assertEqual(report.status, "BLAME_LANGUAGE_DETECTED")
        self.assertFalse(report.is_approved)
        self.assertTrue(report.blame_detected)
        self.assertIn("forgot", report.detected_blame_terms)
        self.assertIn("careless", report.detected_blame_terms)

if __name__ == '__main__':
    unittest.main()
