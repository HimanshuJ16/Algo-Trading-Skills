"""
Unit tests for survivorship-bias-free-universe-construction skill.

Tests:
1. Legacy Config & Engine init/run.
2. Point-in-time active universe retrieval for historical dates.
3. Delisted instrument retention prior to delisting date.
4. Terminal delisting settlement (bankruptcy vs merger liquidation).
5. Survivorship bias audit reporting.
"""
import datetime
import unittest
from universe_builder import (
    DelistingReason,
    InstrumentMetadata,
    SurvivorshipFreeUniverseEngine,
    UniverseError,
    Config,
    Engine
)

class TestSurvivorshipFreeUniverseEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SurvivorshipFreeUniverseEngine()

        # Active company
        self.engine.add_instrument(
            InstrumentMetadata(
                symbol="AAPL",
                name="Apple Inc",
                listing_date=datetime.date(1980, 12, 12),
            )
        )

        # Bankrupt delisted company (Lehman Brothers)
        self.engine.add_instrument(
            InstrumentMetadata(
                symbol="LEH",
                name="Lehman Brothers",
                listing_date=datetime.date(1994, 5, 31),
                delisting_date=datetime.date(2008, 9, 15),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=0.0,
            )
        )

        # Acquired delisted company (Twitter / X)
        self.engine.add_instrument(
            InstrumentMetadata(
                symbol="TWTR",
                name="Twitter Inc",
                listing_date=datetime.date(2013, 11, 7),
                delisting_date=datetime.date(2022, 10, 28),
                delisting_reason=DelistingReason.MERGER_ACQUISITION,
                delisting_settlement_price=54.20,
            )
        )

    def test_legacy_engine(self):
        config = Config(name="test")
        eng = Engine(config)
        self.assertEqual(eng.config.name, "test")
        self.assertTrue(eng.run())

    def test_point_in_time_active_universe(self):
        # On 2008-01-01: AAPL and LEH should be active, TWTR not listed yet
        univ_2008 = self.engine.get_active_universe(datetime.date(2008, 1, 1))
        self.assertIn("AAPL", univ_2008)
        self.assertIn("LEH", univ_2008)
        self.assertNotIn("TWTR", univ_2008)

        # On 2020-01-01: AAPL and TWTR active, LEH delisted
        univ_2020 = self.engine.get_active_universe(datetime.date(2020, 1, 1))
        self.assertIn("AAPL", univ_2020)
        self.assertIn("TWTR", univ_2020)
        self.assertNotIn("LEH", univ_2020)

    def test_delisting_settlement_bankruptcy(self):
        # Long 100 shares of LEH on bankruptcy delisting date
        cash, msg = self.engine.process_delisting_settlement("LEH", position_qty=100)
        self.assertEqual(cash, 0.0)
        self.assertIn("BANKRUPTCY", msg)

    def test_delisting_settlement_merger(self):
        # Long 100 shares of TWTR at acquisition price $54.20
        cash, msg = self.engine.process_delisting_settlement("TWTR", position_qty=100)
        self.assertEqual(cash, 5420.0)
        self.assertIn("MERGER", msg)

    def test_survivorship_bias_audit(self):
        audit = self.engine.audit_survivorship_bias(datetime.date(2000, 1, 1), datetime.date(2025, 1, 1))
        self.assertEqual(audit["total_instruments"], 3)
        self.assertEqual(audit["delisted_in_period"], 2)
        self.assertTrue(audit["has_bias_protection"])


if __name__ == "__main__":
    unittest.main()
