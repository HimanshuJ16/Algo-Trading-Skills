import logging
import math
import unittest

import pandas as pd

from synthetic_continuous_futures import (
    AdjustmentMethod,
    ContinuousFuturesSeries,
    RollEvent,
    RollMethod,
    SyntheticContinuousFuturesEngine,
)

# The engine logs roll events and data-quality warnings by design; keep test
# output readable.
logging.getLogger("synthetic_continuous_futures").setLevel(logging.CRITICAL + 1)

SESSIONS = [
    "2024-03-01", "2024-03-02", "2024-03-03",
    "2024-03-04", "2024-03-05", "2024-03-06",
]


def frame(closes, volumes=None, open_interest=None, sessions=None, ohl=False):
    """Build one contract's history.

    ``ohl`` adds open/high/low offset a fixed distance from the close, so an
    adjustment applied to all four price columns is checkable independently.
    """
    sessions = sessions or SESSIONS
    data = {"close": [float(c) for c in closes]}
    if ohl:
        data["open"] = [float(c) - 0.5 for c in closes]
        data["high"] = [float(c) + 1.0 for c in closes]
        data["low"] = [float(c) - 1.0 for c in closes]
    if volumes is not None:
        data["volume"] = list(volumes)
    if open_interest is not None:
        data["open_interest"] = list(open_interest)
    return pd.DataFrame(data, index=list(sessions))


# --- Three-contract ES fixture -------------------------------------------------
#
# Every contract gains exactly 1.00 per session and each deferred contract trades
# exactly 10.00 above the one in front of it. The economically correct continuous
# series is therefore a clean +1.00-per-session ramp, and — because back
# adjustment leaves the newest contract at real prices — it must end on ESU24's
# own closes: 120, 121, 122, 123, 124, 125.
#
# Volume crossovers: ESM24 overtakes ESH24 on session 2 (850 > 800), so the
# series switches on session 3. ESU24 overtakes ESM24 on session 4 (850 > 800),
# so the series switches on session 5.
ESH24_CLOSES = [100, 101, 102, 103, 104, 105]
ESM24_CLOSES = [110, 111, 112, 113, 114, 115]
ESU24_CLOSES = [120, 121, 122, 123, 124, 125]
ESH24_VOLUME = [900, 800, 700, 600, 500, 400]
ESM24_VOLUME = [100, 850, 900, 800, 700, 600]
ESU24_VOLUME = [10, 20, 30, 850, 900, 1000]

EXPECTED_CONTINUOUS = [120.0, 121.0, 122.0, 123.0, 124.0, 125.0]


def es_three_contracts(ohl=False):
    return {
        "ESH24": frame(ESH24_CLOSES, ESH24_VOLUME, ohl=ohl),
        "ESM24": frame(ESM24_CLOSES, ESM24_VOLUME, ohl=ohl),
        "ESU24": frame(ESU24_CLOSES, ESU24_VOLUME, ohl=ohl),
    }


class TestAdditiveBackAdjustment(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine()
        self.series = self.engine.construct_continuous_series("ES", es_three_contracts())

    def test_returns_continuous_series_dataclass(self):
        self.assertIsInstance(self.series, ContinuousFuturesSeries)
        self.assertEqual(self.series.ticker, "ES")
        self.assertEqual(self.series.adjustment_method, AdjustmentMethod.ADDITIVE_BACK_ADJUSTMENT)
        self.assertEqual(self.series.roll_method, RollMethod.VOLUME_CROSSOVER)

    def test_adjusted_series_matches_independently_derived_ramp(self):
        # Derived from the fixture's construction, not from the engine's formula.
        self.assertEqual(
            list(self.series.df_continuous["adjusted_close"]), EXPECTED_CONTINUOUS
        )

    def test_newest_segment_is_left_at_real_market_prices(self):
        # Back adjustment anchors the *newest* contract: the final bar must be a
        # price you could have traded. A forward-adjusted series fails this.
        last = self.series.df_continuous.iloc[-1]
        self.assertEqual(last["adjusted_close"], last["raw_close"])
        self.assertEqual(last["raw_close"], 125.0)
        self.assertEqual(last["adjustment_offset"], 0.0)

    def test_history_is_shifted_by_cumulative_gap_of_later_rolls(self):
        # Two rolls, each +10.00, so the oldest segment is lifted by 20.00.
        self.assertEqual(self.series.cumulative_gap, 20.0)
        first = self.series.df_continuous.iloc[0]
        self.assertEqual(first["raw_close"], 100.0)
        self.assertEqual(first["adjusted_close"], 120.0)

    def test_no_artificial_jump_at_any_roll_session(self):
        adjusted = list(self.series.df_continuous["adjusted_close"])
        deltas = [b - a for a, b in zip(adjusted, adjusted[1:])]
        # Every contract moves +1.00 per session, so every spliced session must
        # too. A residual roll gap would show up as a +10 or -10 delta here.
        for delta in deltas:
            self.assertAlmostEqual(delta, 1.0, places=9)

    def test_raw_close_still_reports_the_traded_contract_price(self):
        self.assertEqual(
            list(self.series.df_continuous["raw_close"]),
            [100.0, 101.0, 112.0, 113.0, 124.0, 125.0],
        )

    def test_open_high_low_receive_the_same_offset_as_close(self):
        series = self.engine.construct_continuous_series("ES", es_three_contracts(ohl=True))
        first = series.df_continuous.iloc[0]
        self.assertEqual(first["adjusted_open"], 119.5)   # 99.5 + 20
        self.assertEqual(first["adjusted_high"], 121.0)   # 101.0 + 20
        self.assertEqual(first["adjusted_low"], 119.0)    # 99.0 + 20
        # Bar geometry must survive the shift.
        self.assertEqual(first["adjusted_high"] - first["adjusted_low"], 2.0)

    def test_volume_is_carried_through_unadjusted(self):
        self.assertEqual(
            list(self.series.df_continuous["volume"]),
            [900, 800, 900, 800, 900, 1000],
        )


class TestRollTiming(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine()
        self.series = self.engine.construct_continuous_series("ES", es_three_contracts())

    def test_two_rolls_detected(self):
        self.assertEqual(self.series.total_roll_events, 2)
        self.assertEqual(len(self.series.roll_events), 2)
        self.assertIsInstance(self.series.roll_events[0], RollEvent)

    def test_switch_happens_the_session_after_the_crossover(self):
        # The crossover on 2024-03-02 uses that session's completed volume, which
        # is not known until it closes; pricing 03-02 off the back contract would
        # be look-ahead. 03-02 stays on ESH24; 03-03 is the first ESM24 bar.
        contracts = list(self.series.df_continuous["active_contract"])
        self.assertEqual(
            contracts, ["ESH24", "ESH24", "ESM24", "ESM24", "ESU24", "ESU24"]
        )

    def test_roll_event_records_reference_and_effective_sessions(self):
        event = self.series.roll_events[0]
        self.assertEqual(event.from_contract, "ESH24")
        self.assertEqual(event.to_contract, "ESM24")
        self.assertEqual(event.reference_session, "2024-03-02")
        self.assertEqual(event.effective_session, "2024-03-03")
        self.assertEqual(event.front_close, 101.0)
        self.assertEqual(event.next_close, 111.0)
        self.assertEqual(event.gap, 10.0)
        self.assertEqual(event.trigger, RollMethod.VOLUME_CROSSOVER)

    def test_gap_is_measured_on_the_last_front_contract_session(self):
        # Both closes must come from the same session, or the "gap" also contains
        # a day of market move.
        for event in self.series.roll_events:
            self.assertEqual(event.gap, event.next_close - event.front_close)
            self.assertEqual(event.gap, 10.0)

    def test_first_bar_of_each_new_segment_is_flagged(self):
        self.assertEqual(
            list(self.series.df_continuous["is_roll_session"]),
            [False, False, True, False, True, False],
        )
        self.assertEqual(list(self.series.df_continuous["segment_id"]), [0, 0, 1, 1, 2, 2])

    def test_open_interest_crossover_is_evaluated_on_open_interest(self):
        # Volume says never roll; open interest says roll after session 2.
        data = {
            "ESH24": frame([100, 101, 102], [900, 900, 900], [500, 400, 300],
                           sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111, 112], [1, 1, 1], [100, 600, 900],
                           sessions=SESSIONS[:3]),
        }
        engine = SyntheticContinuousFuturesEngine(
            roll_method=RollMethod.OPEN_INTEREST_CROSSOVER
        )
        series = engine.construct_continuous_series("ES", data)
        self.assertEqual(series.total_roll_events, 1)
        self.assertEqual(series.roll_events[0].reference_session, "2024-03-02")
        self.assertEqual(list(series.df_continuous["active_contract"]),
                         ["ESH24", "ESH24", "ESM24"])

    def test_no_roll_when_liquidity_never_migrates(self):
        data = {
            "ESH24": frame([100, 101, 102], [900, 900, 900], sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111, 112], [1, 2, 3], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series("ES", data)
        self.assertEqual(series.total_roll_events, 0)
        self.assertEqual(series.cumulative_gap, 0.0)
        self.assertEqual(series.cumulative_ratio, 1.0)
        # With no roll, adjusted must equal raw everywhere.
        self.assertEqual(
            list(series.df_continuous["adjusted_close"]),
            list(series.df_continuous["raw_close"]),
        )

    def test_equal_volume_is_not_a_crossover(self):
        data = {
            "ESH24": frame([100, 101, 102], [900, 900, 900], sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111, 112], [900, 900, 900], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series("ES", data)
        self.assertEqual(series.total_roll_events, 0)

    def test_confirmation_window_suppresses_a_one_session_volume_spike(self):
        # ESM24 outprints ESH24 on session 2 only, then collapses again.
        data = {
            "ESH24": frame([100, 101, 102, 103], [900, 800, 900, 900],
                           sessions=SESSIONS[:4]),
            "ESM24": frame([110, 111, 112, 113], [100, 850, 100, 100],
                           sessions=SESSIONS[:4]),
        }
        rolled = SyntheticContinuousFuturesEngine().construct_continuous_series("ES", data)
        self.assertEqual(rolled.total_roll_events, 1)

        held = SyntheticContinuousFuturesEngine(
            min_confirmation_sessions=2
        ).construct_continuous_series("ES", data)
        self.assertEqual(held.total_roll_events, 0)


class TestDaysBeforeExpiryRoll(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine(
            roll_method=RollMethod.DAYS_BEFORE_EXPIRY, days_before_expiry=2
        )
        self.expiries = {"ESH24": "2024-03-04", "ESM24": "2024-06-21"}
        self.data = {
            "ESH24": frame(ESH24_CLOSES[:4], sessions=SESSIONS[:4]),
            "ESM24": frame(ESM24_CLOSES[:4], sessions=SESSIONS[:4]),
        }

    def test_calendar_roll_actually_rolls(self):
        series = self.engine.construct_continuous_series("ES", self.data, self.expiries)
        # 2024-03-02 is two calendar days before the 03-04 expiry, so the series
        # switches on 03-03.
        self.assertEqual(series.total_roll_events, 1)
        self.assertEqual(series.roll_events[0].reference_session, "2024-03-02")
        self.assertEqual(series.roll_events[0].effective_session, "2024-03-03")
        self.assertEqual(series.roll_events[0].trigger, RollMethod.DAYS_BEFORE_EXPIRY)
        self.assertEqual(
            list(series.df_continuous["active_contract"]),
            ["ESH24", "ESH24", "ESM24", "ESM24"],
        )

    def test_calendar_roll_without_expiries_is_rejected_not_silently_skipped(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", self.data)
        self.assertIn("contract_expiries", str(ctx.exception))

    def test_no_close_column_requirement_beyond_close(self):
        # DAYS_BEFORE_EXPIRY must not demand volume or open interest.
        series = self.engine.construct_continuous_series("ES", self.data, self.expiries)
        self.assertEqual(series.total_roll_events, 1)

    def test_wider_window_rolls_earlier(self):
        engine = SyntheticContinuousFuturesEngine(
            roll_method=RollMethod.DAYS_BEFORE_EXPIRY, days_before_expiry=3
        )
        series = engine.construct_continuous_series("ES", self.data, self.expiries)
        self.assertEqual(series.roll_events[0].effective_session, "2024-03-02")


class TestContractOrdering(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine()

    def test_contracts_are_ordered_by_expiry_not_alphabetically(self):
        # Lexicographically 'ESH25' < 'ESZ24', which would invert the series.
        data = {
            "ESH25": frame([110, 111, 112], [100, 850, 900], sessions=SESSIONS[:3]),
            "ESZ24": frame([100, 101, 102], [900, 800, 700], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series("ES", data)
        self.assertEqual(
            list(series.df_continuous["active_contract"]), ["ESZ24", "ESZ24", "ESH25"]
        )
        self.assertEqual(series.roll_events[0].from_contract, "ESZ24")
        self.assertEqual(series.roll_events[0].to_contract, "ESH25")
        self.assertEqual(series.cumulative_gap, 10.0)

    def test_month_codes_order_within_a_year(self):
        # F(Jan) < H(Mar) < N(Jul) < Z(Dec) — not the alphabetical F, H, N, Z by
        # accident: alphabetical ordering of Q(Aug) and N(Jul) would invert.
        data = {
            "CLQ24": frame([110, 111, 112], [100, 850, 900], sessions=SESSIONS[:3]),
            "CLN24": frame([100, 101, 102], [900, 800, 700], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series("CL", data)
        self.assertEqual(series.roll_events[0].from_contract, "CLN24")
        self.assertEqual(series.roll_events[0].to_contract, "CLQ24")

    def test_explicit_expiries_override_symbol_parsing(self):
        data = {
            "FRONT": frame([100, 101, 102], [900, 800, 700], sessions=SESSIONS[:3]),
            "BACK": frame([110, 111, 112], [100, 850, 900], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series(
            "X", data, {"FRONT": "2024-03-15", "BACK": "2024-06-21"}
        )
        self.assertEqual(series.roll_events[0].from_contract, "FRONT")

    def test_unparseable_symbol_is_rejected(self):
        data = {
            "FRONT": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "BACK": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("X", data)
        self.assertIn("contract_expiries", str(ctx.exception))

    def test_single_digit_year_code_is_rejected_as_ambiguous(self):
        data = {
            "ESZ4": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "ESH5": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_mixing_two_products_is_rejected(self):
        data = {
            "ESH24": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "NQM24": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("mixes products", str(ctx.exception))

    def test_incomplete_expiry_map_is_rejected(self):
        data = {
            "ESH24": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "ESM24": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series("ES", data, {"ESH24": "2024-03-15"})

    def test_duplicate_expiry_dates_are_rejected(self):
        data = {
            "ESH24": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "ESM24": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series(
                "ES", data, {"ESH24": "2024-03-15", "ESM24": "2024-03-15"}
            )

    def test_malformed_expiry_date_is_rejected(self):
        data = {"ESH24": frame([100, 101], [900, 800], sessions=SESSIONS[:2])}
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series("ES", data, {"ESH24": "15/03/2024"})


class TestProportionalAdjustment(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine(
            adjustment_method=AdjustmentMethod.PROPORTIONAL_RATIO
        )
        self.series = self.engine.construct_continuous_series("ES", es_three_contracts())

    def test_proportional_actually_adjusts_the_history(self):
        # The whole point of the ratio method: older prices must move.
        frame_out = self.series.df_continuous
        self.assertNotEqual(frame_out["adjusted_close"].iloc[0], frame_out["raw_close"].iloc[0])
        self.assertGreater(self.series.cumulative_ratio, 1.0)

    def test_newest_segment_is_left_at_real_market_prices(self):
        last = self.series.df_continuous.iloc[-1]
        self.assertEqual(last["adjusted_close"], last["raw_close"])
        self.assertEqual(last["adjustment_factor"], 1.0)

    def test_percentage_returns_are_preserved_across_a_roll(self):
        # Continuity requirement, derived from the roll definition rather than
        # the engine: the last ESH24 bar scaled by the cumulative factor must
        # land on the ESM24 price observed the same session, itself scaled by the
        # later roll's factor.
        adjusted = list(self.series.df_continuous["adjusted_close"])
        # ESM24 close on the first roll's reference session, carried forward by
        # the second roll's ratio: 111 * (123 / 113).
        self.assertAlmostEqual(adjusted[1], 111.0 * (123.0 / 113.0), places=9)
        # ESU24 close on the second roll's reference session.
        self.assertAlmostEqual(adjusted[3], 123.0, places=9)

    def test_no_return_discontinuity_at_a_roll(self):
        adjusted = list(self.series.df_continuous["adjusted_close"])
        returns = [b / a - 1.0 for a, b in zip(adjusted, adjusted[1:])]
        # Session-over-session returns come from a single contract throughout,
        # so no return may exceed the largest single-contract move (~1%).
        for value in returns:
            self.assertLess(abs(value), 0.011)

    def test_offset_column_is_identity_under_proportional_adjustment(self):
        self.assertTrue((self.series.df_continuous["adjustment_offset"] == 0.0).all())

    def test_non_positive_close_at_a_roll_is_rejected(self):
        # April 2020 WTI settled below zero; a ratio adjustment there is
        # meaningless and must not be produced silently.
        data = {
            "CLK20": frame([10.0, -37.63, 5.0], [900, 800, 700], sessions=SESSIONS[:3]),
            "CLM20": frame([20.0, 20.0, 21.0], [100, 850, 900], sessions=SESSIONS[:3]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("CL", data)
        self.assertIn("ADDITIVE_BACK_ADJUSTMENT", str(ctx.exception))

    def test_additive_handles_the_same_negative_close(self):
        data = {
            "CLK20": frame([10.0, -37.63, 5.0], [900, 800, 700], sessions=SESSIONS[:3]),
            "CLM20": frame([20.0, 20.0, 21.0], [100, 850, 900], sessions=SESSIONS[:3]),
        }
        series = SyntheticContinuousFuturesEngine().construct_continuous_series("CL", data)
        # Gap = 20.00 - (-37.63) = 57.63, added to the pre-roll history.
        self.assertAlmostEqual(series.cumulative_gap, 57.63, places=9)
        self.assertAlmostEqual(
            series.df_continuous["adjusted_close"].iloc[0], 67.63, places=9
        )


class TestUnadjustedConcatenation(unittest.TestCase):
    def test_unadjusted_preserves_the_raw_discontinuity(self):
        engine = SyntheticContinuousFuturesEngine(
            adjustment_method=AdjustmentMethod.UNADJUSTED_CONCATENATED
        )
        series = engine.construct_continuous_series("ES", es_three_contracts())
        self.assertEqual(
            list(series.df_continuous["adjusted_close"]),
            list(series.df_continuous["raw_close"]),
        )
        # The roll gaps are still reported, so the caller can see what was left in.
        self.assertEqual(series.total_roll_events, 2)
        self.assertEqual(series.cumulative_gap, 0.0)
        self.assertEqual(series.cumulative_ratio, 1.0)


class TestDataQuality(unittest.TestCase):
    def setUp(self):
        self.engine = SyntheticContinuousFuturesEngine()

    def test_empty_contract_data_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series("ES", {})

    def test_empty_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series(
                "ES", {"ESH24": pd.DataFrame(columns=["close", "volume"])}
            )

    def test_non_dataframe_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.construct_continuous_series("ES", {"ESH24": [1, 2, 3]})

    def test_missing_trigger_column_is_rejected(self):
        data = {
            "ESH24": frame([100, 101], sessions=SESSIONS[:2]),
            "ESM24": frame([110, 111], [100, 850], sessions=SESSIONS[:2]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("volume", str(ctx.exception))

    def test_duplicate_session_labels_are_rejected(self):
        data = {
            "ESH24": frame([100, 101], [900, 800], sessions=["2024-03-01", "2024-03-01"]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_mixed_index_label_types_are_rejected(self):
        data = {
            "ESH24": frame([100, 101], [900, 800], sessions=SESSIONS[:2]),
            "ESM24": frame(
                [110, 111], [100, 850],
                sessions=[pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-02")],
            ),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("index", str(ctx.exception).lower())

    def test_nan_close_on_a_used_bar_is_rejected_not_propagated(self):
        data = {
            "ESH24": frame([100, math.nan, 102], [900, 800, 700], sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111, 112], [1, 2, 3], sessions=SESSIONS[:3]),
        }
        with self.assertRaises(ValueError) as ctx:
            self.engine.construct_continuous_series("ES", data)
        self.assertIn("non-finite close", str(ctx.exception))

    def test_nan_trigger_value_is_not_read_as_no_crossover(self):
        data = {
            "ESH24": frame([100, 101, 102], [900, math.nan, 900], sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111, 112], [100, 850, 100], sessions=SESSIONS[:3]),
        }
        series = self.engine.construct_continuous_series("ES", data)
        self.assertEqual(series.total_roll_events, 0)
        self.assertEqual(series.unevaluable_trigger_sessions, 1)

    def test_sessions_without_a_bar_on_the_active_contract_are_counted(self):
        # ESM24 does not trade on 2024-03-03; ESH24 does. After the roll the
        # series has no bar for that session, and says so.
        data = {
            "ESH24": frame([100, 101, 102], [900, 800, 700], sessions=SESSIONS[:3]),
            "ESM24": frame([110, 111], [100, 850], sessions=["2024-03-01", "2024-03-02"]),
        }
        series = self.engine.construct_continuous_series("ES", data)
        self.assertEqual(series.total_roll_events, 1)
        self.assertEqual(series.sessions_without_active_bar, 1)
        self.assertEqual(len(series.df_continuous), 2)

    def test_input_frames_are_not_mutated(self):
        data = es_three_contracts()
        before = {symbol: df.copy(deep=True) for symbol, df in data.items()}
        self.engine.construct_continuous_series("ES", data)
        for symbol, df in data.items():
            pd.testing.assert_frame_equal(df, before[symbol])


class TestEngineConfiguration(unittest.TestCase):
    def test_rejects_unknown_roll_method(self):
        with self.assertRaises(ValueError):
            SyntheticContinuousFuturesEngine(roll_method="ROLL_WHENEVER")

    def test_rejects_unknown_adjustment_method(self):
        with self.assertRaises(ValueError):
            SyntheticContinuousFuturesEngine(adjustment_method="MAKE_IT_SMOOTH")

    def test_accepts_the_enum_string_values(self):
        engine = SyntheticContinuousFuturesEngine(
            roll_method="OPEN_INTEREST_CROSSOVER",
            adjustment_method="PROPORTIONAL_RATIO",
        )
        self.assertEqual(engine.roll_method, RollMethod.OPEN_INTEREST_CROSSOVER)
        self.assertEqual(engine.adjustment_method, AdjustmentMethod.PROPORTIONAL_RATIO)

    def test_rejects_negative_days_before_expiry(self):
        with self.assertRaises(ValueError):
            SyntheticContinuousFuturesEngine(days_before_expiry=-1)

    def test_rejects_non_integer_days_before_expiry(self):
        with self.assertRaises(ValueError):
            SyntheticContinuousFuturesEngine(days_before_expiry=2.5)

    def test_rejects_zero_confirmation_sessions(self):
        with self.assertRaises(ValueError):
            SyntheticContinuousFuturesEngine(min_confirmation_sessions=0)


if __name__ == "__main__":
    unittest.main()
