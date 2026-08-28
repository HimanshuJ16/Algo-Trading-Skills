import unittest
from datetime import date

from point_in_time_index_constituent_tracking import (
    IndexConstituentError,
    IndexConstituentEvent,
    PITIndexQuery,
    PointInTimeIndexConstituentTrackingConfig,
    PointInTimeIndexConstituentTrackingEngine,
)


def event(symbol, event_type, effective_date, **kwargs):
    return IndexConstituentEvent("SP500", symbol, event_type, effective_date, **kwargs)


class TestPointInTimeIndexConstituentTracking(unittest.TestCase):
    def setUp(self):
        self.config = PointInTimeIndexConstituentTrackingConfig(enabled=True, index_name="SP500")
        self.engine = PointInTimeIndexConstituentTrackingEngine(self.config)

    def _resolve(self, as_of, current_static_universe=None):
        return self.engine.query_pit_universe(
            PITIndexQuery("SP500", as_of), current_static_universe=current_static_universe
        )

    # ------------------------------------------------------------------ baseline

    def test_membership_moves_with_the_as_of_date(self):
        """A name in the index in 2000 and a name added in 2020 must not coexist.

        ENE (Enron) and TSLA dates are illustrative; only TSLA's effective date is
        taken from a primary source (S&P DJI, effective prior to the open 2020-12-21).
        """
        self.engine.insert_events(
            [
                event("ENE", "ADDITION", "1990-01-01"),
                event("ENE", "DELETION", "2001-11-30"),
                event("AAPL", "ADDITION", "1982-11-30"),
                event("TSLA", "ADDITION", "2020-12-21"),
            ]
        )

        y2000 = self._resolve("2000-06-01")
        self.assertEqual(y2000.status, "UNIVERSE_RESOLVED_PIT")
        self.assertEqual(y2000.active_constituents, ["AAPL", "ENE"])
        self.assertEqual(y2000.total_active_count, 2)

        # The 2023 leg the skill's Verification section claims, which the previous
        # test suite constructed and then never executed.
        y2023 = self._resolve("2023-01-01")
        self.assertEqual(y2023.active_constituents, ["AAPL", "TSLA"])
        self.assertNotIn("ENE", y2023.active_constituents)

    def test_events_after_the_as_of_date_are_invisible(self):
        self.engine.insert_events(
            [event("XYZ", "ADDITION", "1990-01-01"), event("XYZ", "DELETION", "2010-01-01")]
        )
        self.assertEqual(self._resolve("2005-01-01").active_constituents, ["XYZ"])

    def test_readdition_after_deletion_restores_membership(self):
        self.engine.insert_events(
            [
                event("RJN", "ADDITION", "1995-01-01"),
                event("RJN", "DELETION", "2003-01-01"),
                event("RJN", "ADDITION", "2011-01-01"),
            ]
        )
        self.assertEqual(self._resolve("2002-12-31").active_constituents, ["RJN"])
        self.assertEqual(self._resolve("2005-01-01").active_constituents, [])
        self.assertEqual(self._resolve("2012-01-01").active_constituents, ["RJN"])

    # -------------------------------------------- half-open interval boundaries

    def test_addition_effective_date_is_already_a_member(self):
        """Changes are effective prior to the open, so add_date == T is a member."""
        self.engine.insert_events([event("TSLA", "ADDITION", "2020-12-21")])
        self.assertEqual(self._resolve("2020-12-21").active_constituents, ["TSLA"])
        self.assertEqual(self._resolve("2020-12-18").active_constituents, [])

    def test_deletion_effective_date_is_already_out(self):
        """Membership is [add, del): del_date == T is *not* a member on T."""
        self.engine.insert_events(
            [event("DEL", "ADDITION", "1990-01-01"), event("DEL", "DELETION", "2001-11-30")]
        )
        self.assertEqual(self._resolve("2001-11-29").active_constituents, ["DEL"])
        self.assertEqual(self._resolve("2001-11-30").active_constituents, [])

    # ------------------------------------------------------- determinism (regression)

    def test_result_is_independent_of_ingest_order(self):
        """Regression: resolution used a stable sort on date alone, so same-date events
        were decided by the order they happened to be inserted."""
        forward = [
            event("ORD", "ADDITION", "2000-01-01"),
            event("ORD", "DELETION", "2005-01-01"),
            event("ORD", "ADDITION", "2008-01-01"),
        ]
        self.engine.insert_events(forward)
        reversed_engine = PointInTimeIndexConstituentTrackingEngine(self.config)
        reversed_engine.insert_events(list(reversed(forward)))

        for as_of in ("2001-01-01", "2006-01-01", "2009-01-01"):
            self.assertEqual(
                self._resolve(as_of).active_constituents,
                reversed_engine.query_pit_universe(
                    PITIndexQuery("SP500", as_of)
                ).active_constituents,
                msg=f"ingest order changed the resolved universe on {as_of}",
            )

    def test_same_day_delete_and_readd_is_deterministic_and_flagged(self):
        """Deletion is applied before addition, and the ambiguity is always reported."""
        events = [
            event("SDY", "ADDITION", "1990-01-01"),
            event("SDY", "DELETION", "2020-06-15"),
            event("SDY", "ADDITION", "2020-06-15"),
        ]
        self.engine.insert_events(list(reversed(events)))
        report = self._resolve("2020-06-15")
        self.assertEqual(report.active_constituents, ["SDY"])
        self.assertTrue(
            any("conflicting" in w and "SDY" in w for w in report.data_quality_warnings),
            report.data_quality_warnings,
        )

    def test_supplied_sequence_overrides_event_type_ordering(self):
        self.engine.insert_events(
            [
                event("SEQ", "ADDITION", "1990-01-01", sequence=1),
                event("SEQ", "ADDITION", "2020-06-15", sequence=2),
                event("SEQ", "DELETION", "2020-06-15", sequence=3),
            ]
        )
        report = self._resolve("2020-06-15")
        self.assertEqual(report.active_constituents, [])

    def test_partial_sequence_coverage_is_flagged(self):
        self.engine.insert_events(
            [
                event("MIX", "ADDITION", "2020-06-15", sequence=7),
                event("MIX", "DELETION", "2020-06-15"),
            ]
        )
        report = self._resolve("2020-06-15")
        self.assertTrue(
            any("mix supplied and missing" in w for w in report.data_quality_warnings),
            report.data_quality_warnings,
        )

    # ------------------------------------------------------------ input validation

    def test_non_padded_date_is_rejected_at_ingest(self):
        """Regression: raw string comparison made '2020-1-5' silently mis-order."""
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("BAD", "ADDITION", "2020-1-5")])

    def test_non_iso_date_format_is_rejected(self):
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("BAD", "ADDITION", "01/05/2020")])

    def test_impossible_calendar_date_is_rejected(self):
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("BAD", "ADDITION", "2021-02-30")])

    def test_unknown_event_type_is_rejected(self):
        """Regression: an unrecognised type used to silently mean 'not a member'."""
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("TYP", "ADD", "2020-01-01")])

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("  ", "ADDITION", "2020-01-01")])

    def test_invalid_event_aborts_the_whole_batch(self):
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events(
                [event("GOOD", "ADDITION", "2020-01-01"), event("BAD", "ADDITION", "not-a-date")]
            )
        self.assertEqual(self.engine.events, [])
        self.assertEqual(self._resolve("2021-01-01").status, "INDEX_NOT_FOUND")

    def test_non_numeric_weight_is_rejected(self):
        with self.assertRaises(IndexConstituentError):
            self.engine.insert_events([event("W", "ADDITION", "2020-01-01", weight="heavy")])

    def test_mutating_an_event_after_ingest_does_not_change_history(self):
        """Events are snapshotted at ingest, so the timeline cannot drift underneath a query."""
        ev = event("SNAP", "ADDITION", "1990-01-01")
        self.engine.insert_events([ev])
        self.assertEqual(self._resolve("2000-01-01").active_constituents, ["SNAP"])
        ev.event_type = "DELETION"
        ev.effective_date = "not-a-date"
        self.assertEqual(self._resolve("2000-01-01").active_constituents, ["SNAP"])

    def test_malformed_query_date_is_rejected(self):
        self.engine.insert_events([event("AAPL", "ADDITION", "1982-11-30")])
        with self.assertRaises(IndexConstituentError):
            self._resolve("June 1 2000")

    def test_date_objects_are_accepted(self):
        self.engine.insert_events([event("AAPL", "ADDITION", date(1982, 11, 30))])
        report = self._resolve(date(2000, 6, 1))
        self.assertEqual(report.active_constituents, ["AAPL"])
        self.assertEqual(report.as_of_date, "2000-06-01")

    def test_case_is_normalised(self):
        self.engine.insert_events(
            [IndexConstituentEvent("sp500", "aapl", "addition", "1982-11-30")]
        )
        report = self.engine.query_pit_universe(PITIndexQuery("Sp500", "2000-06-01"))
        self.assertEqual(report.active_constituents, ["AAPL"])
        self.assertEqual(report.index_name, "SP500")

    # ------------------------------------------------------------- survivorship audit

    def test_ghost_count_is_none_when_no_current_universe_supplied(self):
        """None must not be reported as 0: 'not audited' is not 'no bias found'."""
        self.engine.insert_events(
            [event("ENE", "ADDITION", "1990-01-01"), event("AAPL", "ADDITION", "1982-11-30")]
        )
        report = self._resolve("2000-06-01")
        self.assertIsNone(report.survivorship_bias_ghost_count)
        self.assertEqual(report.ghost_symbols, [])
        self.assertIn("not audited", report.audit_notes)

    def test_ghost_symbols_are_named(self):
        self.engine.insert_events(
            [
                event("ENE", "ADDITION", "1990-01-01"),
                event("LEH", "ADDITION", "1994-01-01"),
                event("AAPL", "ADDITION", "1982-11-30"),
            ]
        )
        report = self._resolve("2000-06-01", current_static_universe={"AAPL", "MSFT", "NVDA"})
        self.assertEqual(report.survivorship_bias_ghost_count, 2)
        self.assertEqual(report.ghost_symbols, ["ENE", "LEH"])

    def test_ghost_audit_is_case_insensitive(self):
        self.engine.insert_events([event("AAPL", "ADDITION", "1982-11-30")])
        report = self._resolve("2000-06-01", current_static_universe={"aapl"})
        self.assertEqual(report.survivorship_bias_ghost_count, 0)

    # ------------------------------------------------------------------ ticker reuse

    def test_security_id_keeps_reused_tickers_apart(self):
        """A ticker held by two different issuers must not collapse into one timeline.

        Old General Motors traded as GM until its 2009 bankruptcy (its shares became
        GMGMQ, then MTLQQ) and the ticker was later reassigned to the new General
        Motors Company. Dates below are illustrative and deliberately overlapping to
        expose the collapse.
        """
        overlapping = [
            event("GM", "ADDITION", "1990-01-01", security_id="OLD-GM"),
            event("GM", "DELETION", "2009-06-08", security_id="OLD-GM"),
            event("GM", "ADDITION", "2005-01-01", security_id="NEW-GM"),
        ]
        self.engine.insert_events(overlapping)
        keyed = self._resolve("2007-01-01")
        self.assertEqual(keyed.active_constituents, ["GM", "GM"])
        self.assertEqual(sorted(keyed.constituent_weights), ["NEW-GM", "OLD-GM"])

        ticker_only = PointInTimeIndexConstituentTrackingEngine(self.config)
        ticker_only.insert_events(
            [
                IndexConstituentEvent(e.index_name, e.symbol, e.event_type, e.effective_date)
                for e in overlapping
            ]
        )
        collapsed = ticker_only.query_pit_universe(PITIndexQuery("SP500", "2007-01-01"))
        self.assertEqual(collapsed.active_constituents, ["GM"])

    # ------------------------------------------------------------------ status paths

    def test_unknown_index_reports_index_not_found(self):
        """An empty universe from an unknown index is a config error, not an empty index."""
        self.engine.insert_events([event("AAPL", "ADDITION", "1982-11-30")])
        report = self.engine.query_pit_universe(PITIndexQuery("NIFTY50", "2000-06-01"))
        self.assertEqual(report.status, "INDEX_NOT_FOUND")
        self.assertEqual(report.active_constituents, [])
        self.assertIsNone(report.survivorship_bias_ghost_count)

    def test_indices_do_not_leak_into_each_other(self):
        self.engine.insert_events(
            [
                event("AAPL", "ADDITION", "1982-11-30"),
                IndexConstituentEvent("NDX", "ADSK", "ADDITION", "1990-01-01"),
            ]
        )
        self.assertEqual(self._resolve("2000-06-01").active_constituents, ["AAPL"])
        self.assertEqual(
            self.engine.query_pit_universe(
                PITIndexQuery("NDX", "2000-06-01")
            ).active_constituents,
            ["ADSK"],
        )

    def test_empty_index_after_every_member_removed(self):
        self.engine.insert_events(
            [event("ONE", "ADDITION", "1990-01-01"), event("ONE", "DELETION", "1995-01-01")]
        )
        report = self._resolve("2000-01-01")
        self.assertEqual(report.status, "UNIVERSE_RESOLVED_PIT")
        self.assertEqual(report.active_constituents, [])

    def test_disabled_engine_resolves_nothing(self):
        engine = PointInTimeIndexConstituentTrackingEngine(
            PointInTimeIndexConstituentTrackingConfig(enabled=False)
        )
        engine.insert_events([event("AAPL", "ADDITION", "1982-11-30")])
        report = engine.query_pit_universe(PITIndexQuery("SP500", "2000-06-01"))
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertEqual(report.active_constituents, [])
        self.assertIsNone(report.survivorship_bias_ghost_count)


if __name__ == "__main__":
    unittest.main()
