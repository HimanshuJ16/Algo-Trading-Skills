"""Behavioural tests for the multi-source price reconciliation engine.

Expected values are derived independently of the implementation (by hand, in the
comments) so a test cannot pass merely by re-running the code's own formula.
"""

import logging
import math
import unittest

from price_reconciliation import (
    COMPOSITE_WEIGHTED,
    MIN_SOURCES_FOR_ATTRIBUTION,
    MultiSourcePriceReconcilerEngine,
    NO_WINNING_VENDOR,
    PriceReconciliationReport,
    ReconciliationConfig,
    ReconciliationStatus,
    Resolution,
    TieBreakerMethod,
    VendorPriceQuote,
)


def quote(vendor, price, **kwargs):
    """A quote for AAPL at t=1000.0 unless overridden."""
    kwargs.setdefault("timestamp", 1000.0)
    return VendorPriceQuote(vendor, "AAPL", price=price, **kwargs)


class ReconcilerTestCase(unittest.TestCase):
    """Silences the engine's own warning/error logging for readable test output."""

    def setUp(self):
        self.engine = MultiSourcePriceReconcilerEngine()
        logging.getLogger("price_reconciliation").setLevel(logging.CRITICAL)


# --------------------------------------------------------------------------- happy path


class TestConsensusPricing(ReconcilerTestCase):

    def test_outlier_is_filtered_and_survivors_form_a_composite(self):
        # Bloomberg 100.00, Refinitiv 100.02, Polygon 105.00.
        # median of {100.00, 100.02, 105.00} = 100.02 (odd n -> a real quote).
        # |105.00 - 100.02| / 100.02 = 4.979% > 1% default -> Polygon is the outlier.
        # Survivors' spread = (100.02 - 100.00) / 100.01 = 0.0199...% <= 0.05% -> agree.
        # Equal weights -> composite = (100.00 + 100.02) / 2 = 100.01 exactly.
        report = self.engine.reconcile_prices("AAPL", [
            quote("BLOOMBERG", 100.00, vendor_priority=1),
            quote("REFINITIV", 100.02, vendor_priority=2),
            quote("POLYGON_BAD", 105.00, vendor_priority=3),
        ])

        self.assertEqual(report.status, ReconciliationStatus.SUCCESS)
        self.assertTrue(report.is_cross_verified)
        self.assertEqual(report.total_quotes_received, 3)
        self.assertEqual(report.valid_quotes_count, 2)
        self.assertEqual(report.outlier_quotes_count, 1)
        self.assertEqual([q.vendor_id for q in report.outlier_quotes], ["POLYGON_BAD"])
        self.assertAlmostEqual(report.canonical_price, 100.01, places=10)
        self.assertEqual(report.winning_vendor_id, COMPOSITE_WEIGHTED)
        self.assertEqual(report.tie_breaker_used, Resolution.WEIGHTED_AVERAGE)
        self.assertEqual(report.contributing_vendor_ids, ("BLOOMBERG", "REFINITIV"))

    def test_reliability_weights_shift_the_composite_toward_the_trusted_vendor(self):
        # Weights 3 and 1 over 100.00 and 100.04:
        #   (3*100.00 + 1*100.04) / 4 = 400.04 / 4 = 100.01 exactly.
        cfg = ReconciliationConfig(tolerance_pct=0.001)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("A_TRUSTED", 100.00, reliability_weight=3.0),
            quote("B_MINOR", 100.04, reliability_weight=1.0),
        ])
        self.assertAlmostEqual(report.canonical_price, 100.01, places=10)
        self.assertTrue(report.is_cross_verified)

    def test_composite_always_lies_within_the_surviving_quote_range(self):
        # Invariant of any convex combination with strictly positive weights.
        cfg = ReconciliationConfig(tolerance_pct=0.01, max_deviation_pct=0.05)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("A", 99.50, reliability_weight=0.25),
            quote("B", 100.00, reliability_weight=7.0),
            quote("C", 100.30, reliability_weight=1.5),
        ])
        self.assertGreaterEqual(report.canonical_price, 99.50)
        self.assertLessEqual(report.canonical_price, 100.30)


# ------------------------------------------------------------------- input validation


class TestQuoteValidation(ReconcilerTestCase):

    def test_nan_price_is_rejected_at_construction(self):
        # Regression: NaN fails every comparison, so `abs(nan - m)/m > bound` is False.
        # Before this fix a NaN quote passed the outlier filter and was published.
        with self.assertRaises(ValueError):
            quote("BROKEN", float("nan"))

    def test_infinite_price_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            quote("BROKEN", float("inf"))

    def test_non_positive_price_is_rejected_at_construction(self):
        # Regression: a non-positive median disabled the outlier filter entirely and
        # forced the spread to 0.0, so garbage read as "all vendors agree".
        for bad in (0.0, -12.5):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    quote("BROKEN", bad)

    def test_non_positive_reliability_weight_is_rejected(self):
        # Regression: all-zero weights raised ZeroDivisionError inside the composite;
        # negative weights placed the composite outside the quote range.
        for bad in (0.0, -1.0):
            with self.subTest(weight=bad):
                with self.assertRaises(ValueError):
                    quote("A", 100.0, reliability_weight=bad)

    def test_empty_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.reconcile_prices("AAPL", [])

    def test_quote_for_another_instrument_is_rejected(self):
        # Regression: the symbol argument was never compared to the quotes, so an
        # MSFT quote could be reconciled into a confident AAPL price.
        quotes = [
            quote("BLOOMBERG", 100.00),
            VendorPriceQuote("REFINITIV", "MSFT", price=410.00, timestamp=1000.0),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.reconcile_prices("AAPL", quotes)
        self.assertIn("REFINITIV", str(ctx.exception))

    def test_duplicate_vendor_is_rejected(self):
        # Regression: a replayed quote from one vendor counted twice in the median,
        # in the composite, and in the apparent number of independent sources.
        quotes = [quote("BLOOMBERG", 100.00), quote("BLOOMBERG", 100.02)]
        with self.assertRaises(ValueError) as ctx:
            self.engine.reconcile_prices("AAPL", quotes)
        self.assertIn("BLOOMBERG", str(ctx.exception))


class TestConfigValidation(ReconcilerTestCase):

    def test_unknown_tie_breaker_method_is_rejected(self):
        # Regression: an unknown method silently degraded to "first quote in the list",
        # making the canonical price depend on caller iteration order.
        with self.assertRaises(ValueError):
            ReconciliationConfig(tie_breaker_method="PRIORTY")

    def test_tie_breaker_method_is_case_insensitive_and_normalised(self):
        cfg = ReconciliationConfig(tie_breaker_method="freshness")
        self.assertIs(cfg.tie_breaker_method, TieBreakerMethod.FRESHNESS)
        self.assertEqual(cfg.tie_breaker_method, "FRESHNESS")

    def test_tolerance_wider_than_deviation_bound_is_rejected(self):
        with self.assertRaises(ValueError):
            ReconciliationConfig(max_deviation_pct=0.001, tolerance_pct=0.01)

    def test_two_source_outlier_filtering_cannot_be_configured(self):
        with self.assertRaises(ValueError):
            ReconciliationConfig(min_sources_for_outlier_filter=2)
        self.assertEqual(MIN_SOURCES_FOR_ATTRIBUTION, 3)

    def test_non_positive_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            ReconciliationConfig(max_deviation_pct=0.0)
        with self.assertRaises(ValueError):
            ReconciliationConfig(tolerance_pct=-0.001)
        with self.assertRaises(ValueError):
            ReconciliationConfig(max_quote_age_seconds=0.0)


# --------------------------------------------------------------- attribution limits


class TestOutlierAttributionLimits(ReconcilerTestCase):

    def test_two_sources_are_never_filtered_because_neither_can_be_attributed(self):
        # median(100.00, 105.00) = 102.50; both are 2.4390% away, identically.
        # Filtering can only reject both or neither, so it is skipped entirely.
        cfg = ReconciliationConfig(max_deviation_pct=0.05, tie_breaker_method="PRIORITY")
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("REFINITIV", 105.00, vendor_priority=2),
            quote("BLOOMBERG", 100.00, vendor_priority=1),
        ])
        self.assertEqual(report.outlier_quotes_count, 0)
        self.assertEqual(report.valid_quotes_count, 2)
        self.assertEqual(report.status, ReconciliationStatus.UNRESOLVED)
        self.assertFalse(report.is_cross_verified)

    def test_bimodal_split_reports_a_deadlock_rather_than_zero_outliers(self):
        # Regression, audit integrity. 100, 100, 105, 105 -> median 102.50; every quote
        # is 2.4390% away, so all four fail a 1% bound. The old code silently reset the
        # rejection list and reported RECONCILIATION_SUCCESS with 0 outliers.
        report = self.engine.reconcile_prices("AAPL", [
            quote("A", 100.00, vendor_priority=1),
            quote("B", 100.00, vendor_priority=2),
            quote("C", 105.00, vendor_priority=3),
            quote("D", 105.00, vendor_priority=4),
        ])
        self.assertTrue(report.filter_deadlocked)
        self.assertEqual(report.status, ReconciliationStatus.UNRESOLVED)
        self.assertFalse(report.is_cross_verified)
        self.assertEqual(report.valid_quotes_count, 4)
        self.assertIn("no outlier could be attributed", report.audit_notes)
        # A deadlock must never be resolved as a composite, even though the spread
        # test would otherwise be evaluated against the retained quotes.
        self.assertEqual(report.tie_breaker_used, Resolution.PRIORITY_RANK)
        self.assertEqual(report.winning_vendor_id, "A")

    def test_a_single_surviving_quote_is_reported_uncorroborated(self):
        # 100.00, 105.00, 96.00 -> median 100.00. 105.00 is +5%, 96.00 is -4%; both
        # exceed the 1% bound, leaving only the median quote.
        report = self.engine.reconcile_prices("AAPL", [
            quote("GOOD", 100.00),
            quote("HIGH", 105.00),
            quote("LOW", 96.00),
        ])
        self.assertEqual(report.valid_quotes_count, 1)
        self.assertEqual(report.outlier_quotes_count, 2)
        self.assertEqual(report.status, ReconciliationStatus.UNCORROBORATED)
        self.assertFalse(report.is_cross_verified)
        self.assertAlmostEqual(report.canonical_price, 100.00, places=10)

    def test_single_quote_input_is_usable_but_not_cross_verified(self):
        report = self.engine.reconcile_prices("AAPL", [quote("BLOOMBERG", 100.00)])
        self.assertEqual(report.status, ReconciliationStatus.UNCORROBORATED)
        self.assertEqual(report.tie_breaker_used, Resolution.SINGLE_QUOTE)
        self.assertFalse(report.is_cross_verified)
        self.assertAlmostEqual(report.canonical_price, 100.00, places=10)


# ------------------------------------------------------------------- tolerance bounds


class TestToleranceBounds(ReconcilerTestCase):

    def test_spread_exactly_at_tolerance_is_treated_as_agreement(self):
        # Dyadic values so the boundary is exact in binary floating point:
        # median(63.5, 64.5) = 64.0, spread = 1.0, 1.0/64.0 = 0.015625 exactly.
        # The documented rule is `<=`, so this is agreement, not divergence.
        # (With decimal thresholds such as 0.0005 the boundary is *not* exactly
        # representable - see the tolerance-calibration note in references/standards.md.)
        cfg = ReconciliationConfig(max_deviation_pct=0.02, tolerance_pct=0.015625)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("A", 63.5), quote("B", 64.5),
        ])
        self.assertEqual(report.observed_spread_pct, 0.015625)
        self.assertTrue(report.is_cross_verified)
        self.assertEqual(report.status, ReconciliationStatus.SUCCESS)

    def test_spread_just_beyond_tolerance_is_a_divergence(self):
        cfg = ReconciliationConfig(max_deviation_pct=0.02, tolerance_pct=0.015625)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("A", 63.0), quote("B", 65.0),
        ])
        self.assertEqual(report.observed_spread_pct, 0.03125)
        self.assertFalse(report.is_cross_verified)
        self.assertEqual(report.status, ReconciliationStatus.UNRESOLVED)

    def test_tick_floor_stops_one_lawful_penny_looking_like_a_divergence(self):
        # A $12.00 NMS stock quotes in $0.01 increments (17 CFR 242.612). One tick is
        # 0.01/12.00 = 8.33 bps, wider than the 5 bps default tolerance, so a legal
        # one-cent disagreement would breach it. min_absolute_tolerance=0.01 floors
        # the effective tolerance at exactly one tick.
        quotes = [quote("A", 12.00), quote("B", 12.01)]

        without_floor = self.engine.reconcile_prices("AAPL", quotes)
        self.assertFalse(without_floor.is_cross_verified)

        cfg = ReconciliationConfig(min_absolute_tolerance=0.01)
        with_floor = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", quotes)
        self.assertTrue(with_floor.is_cross_verified)
        # median(12.00, 12.01) = 12.005 -> effective tolerance = 0.01 / 12.005.
        self.assertAlmostEqual(with_floor.effective_tolerance_pct, 0.01 / 12.005, places=12)

    def test_tick_floor_also_protects_the_outlier_bound(self):
        # A sub-dollar name: 0.30, 0.30, 0.3040. The 1% bound is 0.003 in price units,
        # so the 0.0040 difference would be rejected. One tick for an NMS stock under
        # $1.00 is $0.0001, but a venue quoting in $0.005 increments needs the floor.
        quotes = [quote("A", 0.30), quote("B", 0.30), quote("C", 0.3040)]
        strict = self.engine.reconcile_prices("AAPL", quotes)
        self.assertEqual(strict.outlier_quotes_count, 1)

        cfg = ReconciliationConfig(min_absolute_tolerance=0.005, tolerance_pct=0.0005)
        floored = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", quotes)
        self.assertEqual(floored.outlier_quotes_count, 0)


# ------------------------------------------------------------------------ tie-breaking


class TestTieBreaking(ReconcilerTestCase):

    def _conflicting(self):
        # Spread 0.50% >> 0.05% tolerance, and inside a 5% outlier bound so nothing
        # is filtered out first.
        return [
            quote("REFINITIV", 100.50, vendor_priority=2, timestamp=1002.0, volume_depth=50.0),
            quote("BLOOMBERG", 100.00, vendor_priority=1, timestamp=1000.0, volume_depth=900.0),
        ]

    def _engine(self, method):
        return MultiSourcePriceReconcilerEngine(
            ReconciliationConfig(max_deviation_pct=0.05, tie_breaker_method=method)
        )

    def test_priority_selects_the_lowest_rank_number(self):
        report = self._engine("PRIORITY").reconcile_prices("AAPL", self._conflicting())
        self.assertAlmostEqual(report.canonical_price, 100.00, places=10)
        self.assertEqual(report.winning_vendor_id, "BLOOMBERG")
        self.assertEqual(report.tie_breaker_used, Resolution.PRIORITY_RANK)
        self.assertEqual(report.contributing_vendor_ids, ("BLOOMBERG",))

    def test_freshness_selects_the_most_recent_timestamp(self):
        report = self._engine("FRESHNESS").reconcile_prices("AAPL", self._conflicting())
        self.assertAlmostEqual(report.canonical_price, 100.50, places=10)
        self.assertEqual(report.winning_vendor_id, "REFINITIV")
        self.assertEqual(report.tie_breaker_used, Resolution.FRESHNESS_TIMESTAMP)

    def test_volume_weighted_selects_the_deepest_book(self):
        report = self._engine("VOLUME_WEIGHTED").reconcile_prices("AAPL", self._conflicting())
        self.assertAlmostEqual(report.canonical_price, 100.00, places=10)
        self.assertEqual(report.winning_vendor_id, "BLOOMBERG")
        self.assertEqual(report.tie_breaker_used, Resolution.VOLUME_DEPTH)

    def test_a_tie_broken_price_is_never_reported_as_cross_verified(self):
        report = self._engine("PRIORITY").reconcile_prices("AAPL", self._conflicting())
        self.assertEqual(report.status, ReconciliationStatus.UNRESOLVED)
        self.assertFalse(report.is_cross_verified)

    def test_tie_breaking_is_independent_of_caller_list_order(self):
        # Regression: with every ranking key equal, `min`/`max` returned whichever quote
        # came first, so a dict-ordered caller could price differently run to run.
        base = [
            quote("ZULU", 100.90, vendor_priority=1, timestamp=1000.0, volume_depth=100.0),
            quote("ALPHA", 100.10, vendor_priority=1, timestamp=1000.0, volume_depth=100.0),
            quote("MIKE", 100.50, vendor_priority=1, timestamp=1000.0, volume_depth=100.0),
        ]
        for method in ("PRIORITY", "FRESHNESS", "VOLUME_WEIGHTED"):
            engine = self._engine(method)
            winners = set()
            for rotation in range(len(base)):
                ordered = base[rotation:] + base[:rotation]
                winners.add(engine.reconcile_prices("AAPL", ordered).winning_vendor_id)
            with self.subTest(method=method):
                self.assertEqual(winners, {"ALPHA"})

    def test_composite_is_bit_identical_regardless_of_caller_list_order(self):
        # float addition is not associative, so summation order must be pinned.
        cfg = ReconciliationConfig(tolerance_pct=0.01, max_deviation_pct=0.05)
        engine = MultiSourcePriceReconcilerEngine(cfg)
        base = [
            quote("A", 100.10, reliability_weight=0.1),
            quote("B", 100.23, reliability_weight=0.7),
            quote("C", 100.37, reliability_weight=0.2),
        ]
        prices = {
            engine.reconcile_prices("AAPL", base[r:] + base[:r]).canonical_price
            for r in range(len(base))
        }
        self.assertEqual(len(prices), 1)

    def test_weighted_average_method_blends_disagreeing_quotes_but_flags_them(self):
        cfg = ReconciliationConfig(max_deviation_pct=0.05, tie_breaker_method="WEIGHTED_AVERAGE")
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", self._conflicting())
        # Equal weights over 100.00 and 100.50 -> 100.25, a price neither vendor quoted.
        self.assertAlmostEqual(report.canonical_price, 100.25, places=10)
        self.assertEqual(report.status, ReconciliationStatus.UNRESOLVED)
        self.assertFalse(report.is_cross_verified)


# ------------------------------------------------------------------------- staleness


class TestStalenessGating(ReconcilerTestCase):

    def _engine(self):
        return MultiSourcePriceReconcilerEngine(
            ReconciliationConfig(max_quote_age_seconds=2.0, max_deviation_pct=0.05)
        )

    def test_stale_quote_is_excluded_before_any_pricing_arithmetic(self):
        # Regression: timestamps used to matter only as a tie-breaker, so a frozen
        # vendor still participated in the median, the outlier filter and the composite.
        # 100.20 is only 0.79% from median(101.00, 101.02, 100.20) = 101.00, i.e. inside
        # the 1% outlier bound, so the filter cannot remove it. Ungated it widens the
        # spread to 0.81% and turns two fresh vendors that agree to 2 bps into an
        # unresolved divergence. Gating drops it before any arithmetic runs.
        quotes = [
            quote("FRESH_A", 101.00, timestamp=1000.0),
            quote("FRESH_B", 101.02, timestamp=1000.0),
            quote("FROZEN", 100.20, timestamp=900.0),
        ]

        ungated = self.engine.reconcile_prices("AAPL", quotes)
        self.assertEqual(ungated.valid_quotes_count, 3)
        self.assertEqual(ungated.status, ReconciliationStatus.UNRESOLVED)

        report = self._engine().reconcile_prices("AAPL", quotes, as_of=1000.5)
        self.assertEqual([q.vendor_id for q in report.stale_quotes], ["FROZEN"])
        self.assertEqual(report.valid_quotes_count, 2)
        self.assertEqual(report.total_quotes_received, 3)
        self.assertEqual(report.status, ReconciliationStatus.SUCCESS)
        self.assertAlmostEqual(report.canonical_price, 101.01, places=10)

    def test_quote_exactly_at_the_age_limit_is_still_fresh(self):
        report = self._engine().reconcile_prices("AAPL", [
            quote("A", 100.00, timestamp=1000.0),
            quote("B", 100.02, timestamp=998.0),
        ], as_of=1000.0)
        self.assertEqual(report.stale_quotes, [])
        self.assertEqual(report.valid_quotes_count, 2)

    def test_total_blackout_emits_no_price_at_all(self):
        # Regression: publishing the newest of several stale quotes is how a dead feed
        # reaches an order router. canonical_price must be None, not a cached value.
        report = self._engine().reconcile_prices("AAPL", [
            quote("A", 100.00, timestamp=900.0),
            quote("B", 100.02, timestamp=901.0),
        ], as_of=1000.0)

        self.assertEqual(report.status, ReconciliationStatus.NO_USABLE_QUOTE)
        self.assertIsNone(report.canonical_price)
        self.assertIsNone(report.median_price)
        self.assertIsNone(report.observed_spread_pct)
        self.assertFalse(report.is_cross_verified)
        self.assertEqual(report.winning_vendor_id, NO_WINNING_VENDOR)
        self.assertEqual(report.valid_quotes_count, 0)
        self.assertEqual(len(report.stale_quotes), 2)

    def test_staleness_gating_requires_an_explicit_clock(self):
        # Anchoring age to the freshest quote in the batch can never detect a batch in
        # which every vendor has stopped updating, so as_of is mandatory, not defaulted.
        with self.assertRaises(ValueError):
            self._engine().reconcile_prices("AAPL", [quote("A", 100.0), quote("B", 100.02)])

    def test_gating_is_off_by_default_and_as_of_is_then_inert(self):
        report = self.engine.reconcile_prices(
            "AAPL", [quote("A", 100.00, timestamp=1.0), quote("B", 100.02, timestamp=2.0)],
            as_of=1e9,
        )
        self.assertEqual(report.stale_quotes, [])
        self.assertEqual(report.status, ReconciliationStatus.SUCCESS)


# -------------------------------------------------------------------------- precision


class TestPrecision(ReconcilerTestCase):

    def test_default_no_rounding_preserves_sub_cent_crypto_prices(self):
        # Regression: a hardcoded round(price, 4) collapsed a 0.00002181 token quote to
        # 0.0 - the skill explicitly targets Binance and Coinbase feeds.
        cfg = ReconciliationConfig(tolerance_pct=0.001)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("BINANCE", 0.00002181),
            quote("COINBASE", 0.00002181),
        ])
        self.assertAlmostEqual(report.canonical_price, 0.00002181, places=12)
        self.assertGreater(report.canonical_price, 0.0)

    def test_explicit_precision_is_applied_when_configured(self):
        cfg = ReconciliationConfig(price_precision=2, tolerance_pct=0.001)
        report = MultiSourcePriceReconcilerEngine(cfg).reconcile_prices("AAPL", [
            quote("A", 100.004), quote("B", 100.008),
        ])
        self.assertEqual(report.canonical_price, 100.01)


# ---------------------------------------------------------------------- audit record


class TestAuditRecord(ReconcilerTestCase):

    def test_report_never_aliases_the_caller_list(self):
        quotes = [quote("A", 100.00), quote("B", 100.02)]
        report = self.engine.reconcile_prices("AAPL", quotes)
        quotes.append(quote("C", 999.00))
        self.assertEqual(len(report.reconciled_quotes), 2)

    def test_counts_and_partitions_are_mutually_consistent(self):
        engine = MultiSourcePriceReconcilerEngine(
            ReconciliationConfig(max_quote_age_seconds=2.0)
        )
        report = engine.reconcile_prices("AAPL", [
            quote("FRESH_A", 100.00, timestamp=1000.0),
            quote("FRESH_B", 100.02, timestamp=1000.0),
            quote("SPIKE", 130.00, timestamp=1000.0),
            quote("FROZEN", 100.01, timestamp=100.0),
        ], as_of=1000.0)

        self.assertEqual(report.total_quotes_received, 4)
        self.assertEqual(
            report.valid_quotes_count + report.outlier_quotes_count + len(report.stale_quotes),
            report.total_quotes_received,
        )
        self.assertEqual(report.valid_quotes_count, len(report.reconciled_quotes))
        self.assertEqual(report.outlier_quotes_count, len(report.outlier_quotes))

    def test_status_is_a_plain_string_not_an_enum_member(self):
        # Enum members render as "ReconciliationStatus.SUCCESS" in an f-string on
        # Python <= 3.10 and as the value on >= 3.11. The audit record must not depend
        # on the interpreter version, so the report stores the plain value.
        report = self.engine.reconcile_prices("AAPL", [quote("A", 100.00), quote("B", 100.02)])
        self.assertIsInstance(report, PriceReconciliationReport)
        self.assertIs(type(report.status), str)
        self.assertIs(type(report.tie_breaker_used), str)
        self.assertEqual(report.status, "RECONCILIATION_SUCCESS")
        self.assertEqual(f"{report.status}", "RECONCILIATION_SUCCESS")
        self.assertTrue(math.isfinite(report.observed_spread_pct))

    def test_a_bound_that_can_never_fire_is_flagged(self):
        # min_absolute_tolerance in the wrong price units silently disables the check.
        cfg = ReconciliationConfig(min_absolute_tolerance=10.0, tolerance_pct=0.0005)
        engine = MultiSourcePriceReconcilerEngine(cfg)
        with self.assertLogs("price_reconciliation", level="WARNING") as logs:
            logging.getLogger("price_reconciliation").setLevel(logging.WARNING)
            report = engine.reconcile_prices("AAPL", [quote("A", 0.30), quote("B", 0.90)])
        self.assertTrue(any("can never fire" in line for line in logs.output))
        self.assertTrue(report.is_cross_verified)  # the disabled check passes everything


if __name__ == "__main__":
    unittest.main()
