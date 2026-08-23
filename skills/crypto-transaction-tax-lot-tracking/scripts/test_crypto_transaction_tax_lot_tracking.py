import unittest

from crypto_transaction_tax_lot_tracking import (
    METHOD_FIFO,
    METHOD_HIFO,
    METHOD_LIFO,
    TERM_LONG,
    TERM_SHORT,
    CryptoTaxLot,
    CryptoTaxLotTrackerEngine,
    is_long_term,
    parse_timestamp,
)


class TestHoldingPeriodClassification(unittest.TestCase):
    """The 'more than one year' rule, counted from the day after acquisition."""

    def _term(self, acquired, disposed):
        return is_long_term(parse_timestamp(acquired), parse_timestamp(disposed))

    def test_disposal_on_the_one_year_anniversary_is_short_term(self):
        # Regression: 2024 is a leap year, so 2024-01-01 -> 2025-01-01 is 366
        # elapsed days. The old `days_held > 365` test called that long-term; it is
        # exactly one year, which is not MORE than one year.
        self.assertFalse(self._term("2024-01-01", "2025-01-01"))

    def test_day_after_the_anniversary_is_long_term(self):
        self.assertTrue(self._term("2024-01-01", "2025-01-02"))

    def test_non_leap_year_anniversary_boundary(self):
        self.assertFalse(self._term("2023-03-15", "2024-03-15"))
        self.assertTrue(self._term("2023-03-15", "2024-03-16"))

    def test_same_day_disposal_is_short_term(self):
        self.assertFalse(self._term("2025-04-10 09:00:00", "2025-04-10 17:30:00"))

    def test_leap_day_acquisition_uses_march_1_as_the_anniversary(self):
        # Documented convention: Feb 29 has no anniversary in a common year, so the
        # module resolves it to Mar 1 (the conservative, later boundary).
        self.assertFalse(self._term("2024-02-29", "2025-02-28"))
        self.assertFalse(self._term("2024-02-29", "2025-03-01"))
        self.assertTrue(self._term("2024-02-29", "2025-03-02"))

    def test_multi_year_hold_is_long_term(self):
        self.assertTrue(self._term("2020-06-01", "2025-06-01"))


class TestTimestampParsing(unittest.TestCase):

    def test_accepted_formats_parse_to_the_same_instant(self):
        self.assertEqual(
            parse_timestamp("2025-01-02T03:04:05Z"),
            parse_timestamp("2025-01-02 03:04:05"),
        )
        self.assertEqual(parse_timestamp("2025-01-02").day, 2)

    def test_mixed_separator_formats_order_chronologically(self):
        # Regression: lots used to be ordered by comparing timestamp STRINGS. The
        # space separator (0x20) sorts before 'T' (0x54), so "2025-01-01 23:00:00"
        # compared as a string precedes "2025-01-01T09:00:00" even though 09:00 is
        # the earlier instant — FIFO then consumed the wrong lot's basis.
        engine = CryptoTaxLotTrackerEngine()
        engine.register_acquisition(
            CryptoTaxLot("LATE", "ETH", "2025-01-01 23:00:00", 1.0, 5000.0))
        engine.register_acquisition(
            CryptoTaxLot("EARLY", "ETH", "2025-01-01T09:00:00", 1.0, 1000.0))

        res = engine.process_crypto_disposition(
            disposal_id="DISP_ORDER", asset_sold="ETH", quantity_sold=1.0,
            asset_received="USDC", gross_proceeds_usd=6000.0,
            disposal_timestamp="2025-08-01",
        )

        self.assertEqual([m.lot_id for m in res.lot_matches], ["EARLY"])
        self.assertEqual(res.total_cost_basis_usd, 1000.0)

    def test_unparseable_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_timestamp("01/02/2025")
        with self.assertRaises(ValueError):
            parse_timestamp(20250102)


class TestCryptoTaxLotTrackerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CryptoTaxLotTrackerEngine()
        # Lot 1: 10 ETH @ $1,500 basis, acquired 2025-01-01
        # Lot 2:  5 ETH @ $3,000 basis, acquired 2025-06-01 (higher basis)
        self.engine.register_acquisition(
            CryptoTaxLot("LOT_1", "ETH", "2025-01-01 10:00:00", 10.0, 1500.0))
        self.engine.register_acquisition(
            CryptoTaxLot("LOT_2", "ETH", "2025-06-01 10:00:00", 5.0, 3000.0))

    def test_default_method_is_fifo(self):
        self.assertEqual(self.engine.default_matching_method, METHOD_FIFO)

    def test_fifo_swap_disposition(self):
        # FIFO matches Lot 1: 4 ETH x $1,500 = $6,000 basis.
        # Net proceeds $12,000 - $0 = $12,000 -> gain $6,000.
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_FIFO", asset_sold="ETH", quantity_sold=4.0,
            asset_received="USDC", gross_proceeds_usd=12000.0,
            disposal_timestamp="2025-08-01",
        )

        self.assertEqual(res.matching_method_used, METHOD_FIFO)
        self.assertEqual(res.total_cost_basis_usd, 6000.0)
        self.assertEqual(res.realized_gain_loss_usd, 6000.0)
        self.assertEqual([m.lot_id for m in res.lot_matches], ["LOT_1"])
        self.assertEqual(self.engine.get_open_quantity("ETH"), 11.0)

    def test_hifo_swap_disposition_and_gas_fee(self):
        # HIFO matches Lot 2: 4 ETH x $3,000 = $12,000 basis.
        # Net proceeds $12,000 - $50 gas = $11,950 -> $50 loss.
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_HIFO", asset_sold="ETH", quantity_sold=4.0,
            asset_received="USDC", gross_proceeds_usd=12000.0,
            disposal_timestamp="2025-08-01", gas_fee_usd=50.0,
            matching_method=METHOD_HIFO,
            identification_reference="books-and-records entry TX-9912",
        )

        self.assertEqual(res.total_cost_basis_usd, 12000.0)
        self.assertEqual(res.net_proceeds_usd, 11950.0)
        self.assertEqual(res.realized_gain_loss_usd, -50.0)
        self.assertEqual([m.lot_id for m in res.lot_matches], ["LOT_2"])
        self.assertEqual(res.identification_reference, "books-and-records entry TX-9912")

    def test_lifo_matches_the_newest_lot(self):
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_LIFO", asset_sold="ETH", quantity_sold=2.0,
            asset_received="USDC", gross_proceeds_usd=6000.0,
            disposal_timestamp="2025-08-01", matching_method=METHOD_LIFO,
            identification_reference="standing order SO-1",
        )

        self.assertEqual([m.lot_id for m in res.lot_matches], ["LOT_2"])

    def test_specific_identification_methods_require_an_identification_record(self):
        # HIFO/LIFO are elections of specific identification; without a record of
        # the identification the disposal falls back to FIFO by law, so producing a
        # HIFO basis figure would be unsupportable.
        for method in (METHOD_HIFO, METHOD_LIFO):
            with self.assertRaises(ValueError) as ctx:
                self.engine.process_crypto_disposition(
                    disposal_id="DISP_NOID", asset_sold="ETH", quantity_sold=1.0,
                    asset_received="USDC", gross_proceeds_usd=3000.0,
                    disposal_timestamp="2025-08-01", matching_method=method,
                )
            self.assertIn("identification_reference", str(ctx.exception))
        # And nothing was consumed by the rejected attempts.
        self.assertEqual(self.engine.get_open_quantity("ETH"), 15.0)

    def test_unknown_matching_method_is_rejected_not_silently_fifo(self):
        # Regression: an unrecognised method used to fall through to the FIFO
        # branch, so a typo silently changed which lots were consumed.
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                disposal_id="DISP_TYPO", asset_sold="ETH", quantity_sold=1.0,
                asset_received="USDC", gross_proceeds_usd=3000.0,
                disposal_timestamp="2025-08-01", matching_method="HIF0",
            )
        self.assertEqual(self.engine.get_open_quantity("ETH"), 15.0)

    def test_insufficient_inventory_does_not_consume_lots(self):
        # Regression: lot quantities used to be decremented inside the matching
        # loop and only checked afterwards, so a failed disposal silently emptied
        # the ledger while raising.
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_crypto_disposition(
                disposal_id="DISP_OVER", asset_sold="ETH", quantity_sold=100.0,
                asset_received="USDC", gross_proceeds_usd=300000.0,
                disposal_timestamp="2025-08-01",
            )

        self.assertIn("Insufficient", str(ctx.exception))
        self.assertEqual(self.engine.get_open_quantity("ETH"), 15.0)
        self.assertEqual(self.engine.tax_lots["UNSPECIFIED"]["ETH"][0].quantity, 10.0)
        self.assertEqual(self.engine.tax_lots["UNSPECIFIED"]["ETH"][1].quantity, 5.0)

    def test_partial_lot_depletion_across_two_lots(self):
        # FIFO: 10 ETH from Lot 1 ($15,000) + 2 ETH from Lot 2 ($6,000) = $21,000.
        # Net proceeds $36,000 - $120 gas = $35,880 -> gain $14,880.
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_SPLIT", asset_sold="ETH", quantity_sold=12.0,
            asset_received="USDC", gross_proceeds_usd=36000.0,
            disposal_timestamp="2025-08-01", gas_fee_usd=120.0,
        )

        self.assertEqual(res.total_cost_basis_usd, 21000.0)
        self.assertEqual(res.net_proceeds_usd, 35880.0)
        self.assertEqual(res.realized_gain_loss_usd, 14880.0)
        self.assertEqual(len(res.lot_matches), 2)
        # Net proceeds allocate pro rata: 10/12 and 2/12 of $35,880.
        self.assertEqual(res.lot_matches[0].proceeds_usd, 29900.0)
        self.assertEqual(res.lot_matches[1].proceeds_usd, 5980.0)
        self.assertEqual(self.engine.get_open_quantity("ETH"), 3.0)

    def test_disposal_spanning_short_and_long_term_lots_is_split(self):
        # Regression: a single `is_short_term` flag used to be flipped to False by
        # ANY long-term lot, reporting the whole disposal as long-term. Form 8949
        # splits such a disposal across Part I and Part II.
        engine = CryptoTaxLotTrackerEngine()
        engine.register_acquisition(CryptoTaxLot("OLD", "ETH", "2023-01-01", 2.0, 1000.0))
        engine.register_acquisition(CryptoTaxLot("NEW", "ETH", "2025-05-01", 3.0, 2000.0))

        res = engine.process_crypto_disposition(
            disposal_id="DISP_MIXED", asset_sold="ETH", quantity_sold=4.0,
            asset_received="USDC", gross_proceeds_usd=10000.0,
            disposal_timestamp="2025-06-01",
        )

        self.assertTrue(res.is_mixed_term)
        self.assertFalse(res.is_short_term)
        self.assertEqual(len(res.lot_matches), 2)
        old_row, new_row = res.lot_matches
        # $10,000 over 4 ETH = $2,500/ETH, 2 ETH each -> $5,000 proceeds per row.
        self.assertEqual(old_row.term, TERM_LONG)
        self.assertEqual(old_row.cost_basis_usd, 2000.0)
        self.assertEqual(old_row.gain_loss_usd, 3000.0)
        self.assertEqual(new_row.term, TERM_SHORT)
        self.assertEqual(new_row.cost_basis_usd, 4000.0)
        self.assertEqual(new_row.gain_loss_usd, 1000.0)
        self.assertEqual(res.long_term_gain_loss_usd, 3000.0)
        self.assertEqual(res.short_term_gain_loss_usd, 1000.0)
        self.assertEqual(res.realized_gain_loss_usd, 4000.0)

    def test_all_short_term_disposal_reports_short_term(self):
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_ST", asset_sold="ETH", quantity_sold=1.0,
            asset_received="USDC", gross_proceeds_usd=3000.0,
            disposal_timestamp="2025-08-01",
        )

        self.assertTrue(res.is_short_term)
        self.assertFalse(res.is_mixed_term)
        self.assertEqual(res.lot_matches[0].term, TERM_SHORT)
        self.assertEqual(res.short_term_gain_loss_usd, res.realized_gain_loss_usd)

    def test_wallets_are_isolated(self):
        engine = CryptoTaxLotTrackerEngine()
        engine.register_acquisition(
            CryptoTaxLot("CB_1", "ETH", "2025-01-01", 5.0, 1000.0, wallet_id="COINBASE"))
        engine.register_acquisition(
            CryptoTaxLot("LG_1", "ETH", "2025-02-01", 5.0, 2000.0, wallet_id="LEDGER"))

        res = engine.process_crypto_disposition(
            disposal_id="DISP_LEDGER", asset_sold="ETH", quantity_sold=5.0,
            asset_received="USDC", gross_proceeds_usd=15000.0,
            disposal_timestamp="2025-08-01", wallet_id="LEDGER",
        )

        # Basis comes from the LEDGER lot ($2,000), never the cheaper COINBASE lot.
        self.assertEqual(res.total_cost_basis_usd, 10000.0)
        self.assertEqual(res.wallet_id, "LEDGER")
        self.assertEqual(engine.get_open_quantity("ETH", "COINBASE"), 5.0)
        self.assertEqual(engine.get_open_quantity("ETH", "LEDGER"), 0.0)

    def test_disposal_cannot_reach_into_another_wallet_for_inventory(self):
        engine = CryptoTaxLotTrackerEngine()
        engine.register_acquisition(
            CryptoTaxLot("CB_1", "ETH", "2025-01-01", 5.0, 1000.0, wallet_id="COINBASE"))

        with self.assertRaises(ValueError):
            engine.process_crypto_disposition(
                disposal_id="DISP_WRONG_WALLET", asset_sold="ETH", quantity_sold=1.0,
                asset_received="USDC", gross_proceeds_usd=3000.0,
                disposal_timestamp="2025-08-01", wallet_id="LEDGER",
            )
        self.assertEqual(engine.get_open_quantity("ETH", "COINBASE"), 5.0)

    def test_lots_acquired_after_the_disposal_are_not_matchable(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_crypto_disposition(
                disposal_id="DISP_EARLY", asset_sold="ETH", quantity_sold=1.0,
                asset_received="USDC", gross_proceeds_usd=3000.0,
                disposal_timestamp="2024-12-31",
            )
        self.assertIn("acquired on or before", str(ctx.exception))

    def test_a_later_lot_is_still_matchable_once_acquired(self):
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_MID", asset_sold="ETH", quantity_sold=12.0,
            asset_received="USDC", gross_proceeds_usd=30000.0,
            disposal_timestamp="2025-06-02",
        )
        self.assertEqual(len(res.lot_matches), 2)

    def test_gas_paid_in_crypto_is_itself_a_disposal(self):
        # The gas ETH is disposed of at its FMV; it is not merely an expense.
        res = self.engine.process_crypto_disposition(
            disposal_id="GAS_01", asset_sold="ETH", quantity_sold=0.01,
            asset_received="NETWORK_FEE", gross_proceeds_usd=30.0,
            disposal_timestamp="2025-08-01",
        )

        self.assertEqual(res.total_cost_basis_usd, 15.0)   # 0.01 x $1,500
        self.assertEqual(res.realized_gain_loss_usd, 15.0)
        self.assertEqual(self.engine.get_open_quantity("ETH"), 14.99)

    def test_invalid_amounts_and_quantities_are_rejected(self):
        base = dict(
            disposal_id="DISP_BAD", asset_sold="ETH", asset_received="USDC",
            disposal_timestamp="2025-08-01",
        )
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                quantity_sold=0.0, gross_proceeds_usd=100.0, **base)
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                quantity_sold=-1.0, gross_proceeds_usd=100.0, **base)
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                quantity_sold=float("nan"), gross_proceeds_usd=100.0, **base)
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                quantity_sold=1.0, gross_proceeds_usd=float("inf"), **base)
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                quantity_sold=1.0, gross_proceeds_usd=100.0, gas_fee_usd=-5.0, **base)
        self.assertEqual(self.engine.get_open_quantity("ETH"), 15.0)

    def test_invalid_acquisitions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_acquisition(
                CryptoTaxLot("BAD_QTY", "ETH", "2025-01-01", 0.0, 1500.0))
        with self.assertRaises(ValueError):
            self.engine.register_acquisition(
                CryptoTaxLot("BAD_BASIS", "ETH", "2025-01-01", 1.0, -1.0))
        with self.assertRaises(ValueError):
            self.engine.register_acquisition(
                CryptoTaxLot("BAD_TS", "ETH", "01/01/2025", 1.0, 1500.0))

    def test_unknown_asset_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_crypto_disposition(
                disposal_id="DISP_UNKNOWN", asset_sold="SOL", quantity_sold=1.0,
                asset_received="USDC", gross_proceeds_usd=100.0,
                disposal_timestamp="2025-08-01",
            )

    def test_engine_rejects_an_unsupported_default_method(self):
        with self.assertRaises(ValueError):
            CryptoTaxLotTrackerEngine(default_matching_method="AVERAGE_COST")

    def test_full_depletion_leaves_no_open_quantity(self):
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_ALL", asset_sold="ETH", quantity_sold=15.0,
            asset_received="USDC", gross_proceeds_usd=45000.0,
            disposal_timestamp="2025-08-01",
        )

        self.assertEqual(res.total_cost_basis_usd, 30000.0)   # $15,000 + $15,000
        self.assertEqual(res.realized_gain_loss_usd, 15000.0)
        self.assertEqual(self.engine.get_open_quantity("ETH"), 0.0)


if __name__ == '__main__':
    unittest.main()
