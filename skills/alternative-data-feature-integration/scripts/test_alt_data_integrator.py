import math
import unittest
from datetime import datetime, timedelta, timezone

from alt_data_integrator import (
    AlignedValue,
    AltDataIntegrator,
    AltDataIntegratorError,
    AltDataValidationError,
    PointInTimeAlignmentError,
    RawAltDataEvent,
    SourceConfig,
    StalenessState,
)


class TestAltDataIntegrator(unittest.TestCase):
    def setUp(self):
        self.integrator = AltDataIntegrator()

    # ------------------------------------------------------------------ #
    # Core PIT mapping
    # ------------------------------------------------------------------ #
    def test_pit_publication_lag_mapping(self):
        # Event occurs on Monday at 12:00, but takes 48 hours to publish.
        event_time = datetime(2026, 1, 5, 12, 0)
        lag = timedelta(hours=48)

        event = RawAltDataEvent(
            source_id="SAT_IMG_01",
            event_timestamp=event_time,
            publication_lag=lag,
            feature_value=150.5,
        )

        self.integrator.ingest_events([event])

        # Knowledge timestamp should strictly be Wednesday at 12:00.
        expected_knowledge_time = datetime(2026, 1, 7, 12, 0)
        self.assertEqual(
            self.integrator.pit_features[0].knowledge_timestamp,
            expected_knowledge_time,
        )

    def test_zero_publication_lag_is_allowed(self):
        # A zero lag is valid (data known immediately); only negative is rejected.
        event = RawAltDataEvent("A", datetime(2026, 1, 5, 12, 0), timedelta(0), 1.0)
        self.integrator.ingest_events([event])
        self.assertEqual(
            self.integrator.pit_features[0].knowledge_timestamp,
            datetime(2026, 1, 5, 12, 0),
        )

    # ------------------------------------------------------------------ #
    # Alignment + safe forward-fill
    # ------------------------------------------------------------------ #
    def test_safe_trading_schedule_alignment(self):
        # Event 1: known on Tuesday 10:00 AM
        e1 = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        # Event 2: known on Thursday 10:00 AM
        e2 = RawAltDataEvent("A", datetime(2026, 1, 8, 8, 0), timedelta(hours=2), 20.0)

        self.integrator.ingest_events([e1, e2])

        trading_times = [
            datetime(2026, 1, 5, 16, 0),  # Monday (before e1 knowledge)
            datetime(2026, 1, 6, 16, 0),  # Tuesday (after e1, before e2)
            datetime(2026, 1, 7, 16, 0),  # Wednesday (after e1, before e2) -> forward fill e1
            datetime(2026, 1, 8, 16, 0),  # Thursday (after e2)
        ]

        aligned = self.integrator.align_to_trading_schedule(trading_times)

        # Monday: unknown (None), UNKNOWN state.
        self.assertIsNone(aligned[trading_times[0]]["A"].value)
        self.assertEqual(
            aligned[trading_times[0]]["A"].staleness_state, StalenessState.UNKNOWN
        )
        # Tuesday: 10.0, FRESH.
        self.assertEqual(aligned[trading_times[1]]["A"].value, 10.0)
        self.assertEqual(
            aligned[trading_times[1]]["A"].staleness_state, StalenessState.FRESH
        )
        # Wednesday: 10.0 (safely forward-filled from Tuesday).
        self.assertEqual(aligned[trading_times[2]]["A"].value, 10.0)
        # Thursday: 20.0 (new data arrived).
        self.assertEqual(aligned[trading_times[3]]["A"].value, 20.0)

    def test_alignment_preserves_caller_order(self):
        self.integrator.ingest_events(
            [RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)]
        )
        # Deliberately unsorted trading times.
        trading_times = [
            datetime(2026, 1, 8, 16, 0),
            datetime(2026, 1, 6, 16, 0),
            datetime(2026, 1, 5, 16, 0),
        ]
        aligned = self.integrator.align_to_trading_schedule(trading_times)
        self.assertEqual(list(aligned.keys()), trading_times)

    def test_alignment_age_and_knowledge_provenance(self):
        e1 = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        self.integrator.ingest_events([e1])
        t = datetime(2026, 1, 7, 16, 0)
        aligned = self.integrator.align_to_trading_schedule([t])
        av = aligned[t]["A"]
        self.assertEqual(av.knowledge_timestamp, datetime(2026, 1, 6, 10, 0))
        self.assertEqual(av.age, t - av.knowledge_timestamp)

    # ------------------------------------------------------------------ #
    # Multi-source: no scalar clobbering
    # ------------------------------------------------------------------ #
    def test_multi_source_alignment_does_not_clobber(self):
        # Two sources known at the same time; the old scalar API would have
        # collapsed them to whichever value sorted last.
        e_a = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        e_b = RawAltDataEvent("B", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 99.0)
        self.integrator.ingest_events([e_a, e_b])

        t = datetime(2026, 1, 6, 16, 0)
        aligned = self.integrator.align_to_trading_schedule([t])
        self.assertEqual(aligned[t]["A"].value, 10.0)
        self.assertEqual(aligned[t]["B"].value, 99.0)

    # ------------------------------------------------------------------ #
    # Bounded staleness / max_age TTL
    # ------------------------------------------------------------------ #
    def test_stale_value_is_not_silently_forward_filled(self):
        # Known at Jan 6 10:00; trading time is 10 days later -> exceeds max_age.
        e = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        self.integrator.ingest_events([e])
        far_future = datetime(2026, 1, 20, 16, 0)

        aligned = self.integrator.align_to_trading_schedule(
            [far_future], max_age=timedelta(days=3)
        )
        av = aligned[far_future]["A"]
        self.assertIsNone(av.value)
        self.assertEqual(av.staleness_state, StalenessState.STALE)
        # Provenance is still exposed so downstream can decide to downweight.
        self.assertEqual(av.knowledge_timestamp, datetime(2026, 1, 6, 10, 0))

    def test_per_source_max_age_overrides_global(self):
        e = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        integrator = AltDataIntegrator(
            source_configs={"A": SourceConfig("A", max_age=timedelta(days=3))}
        )
        integrator.ingest_events([e])
        far_future = datetime(2026, 1, 20, 16, 0)
        # Global max_age is generous, but per-source config (3 days) wins.
        aligned = integrator.align_to_trading_schedule(
            [far_future], max_age=timedelta(days=365)
        )
        self.assertEqual(aligned[far_future]["A"].staleness_state, StalenessState.STALE)

    # ------------------------------------------------------------------ #
    # Data revisions: appended, not overwritten
    # ------------------------------------------------------------------ #
    def test_restatement_is_appended_and_served_after_revised_date(self):
        # Original: event Jan 5 12:00, +48h -> known Jan 7 12:00, value 150.5.
        original = RawAltDataEvent(
            "SAT_IMG_01", datetime(2026, 1, 5, 12, 0), timedelta(hours=48), 150.5
        )
        # Restatement: revised_date Jan 9 09:00, +24h -> known Jan 10 09:00.
        restatement = RawAltDataEvent(
            "SAT_IMG_01",
            event_timestamp=datetime(2026, 1, 5, 12, 0),
            publication_lag=timedelta(hours=24),
            feature_value=160.0,
            revised_date=datetime(2026, 1, 9, 9, 0),
        )
        self.integrator.ingest_events([original, restatement])

        # Both facts are present (appended, not overwritten).
        self.assertEqual(len(self.integrator.pit_features), 2)

        t_before = datetime(2026, 1, 9, 12, 0)   # after original, before restatement
        t_after = datetime(2026, 1, 10, 12, 0)  # after restatement
        aligned = self.integrator.align_to_trading_schedule([t_before, t_after])
        self.assertEqual(aligned[t_before]["SAT_IMG_01"].value, 150.5)
        self.assertEqual(aligned[t_after]["SAT_IMG_01"].value, 160.0)

    # ------------------------------------------------------------------ #
    # Atomic + idempotent ingest, reset
    # ------------------------------------------------------------------ #
    def test_ingest_is_atomic_on_validation_failure(self):
        good = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        bad = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=-1), 20.0)
        with self.assertRaises(AltDataValidationError):
            self.integrator.ingest_events([good, bad])
        # No partial state: nothing was ingested.
        self.assertEqual(len(self.integrator.pit_features), 0)

    def test_idempotent_upsert_dedups_same_key(self):
        e1 = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)
        e2 = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 30.0)
        # Same (source_id, knowledge_timestamp) -> last-write-wins, one fact.
        self.integrator.ingest_events([e1, e2])
        self.assertEqual(len(self.integrator.pit_features), 1)
        self.assertEqual(self.integrator.pit_features[0].feature_value, 30.0)

    def test_reset_clears_state(self):
        self.integrator.ingest_events(
            [RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)]
        )
        self.assertEqual(len(self.integrator.pit_features), 1)
        self.integrator.reset()
        self.assertEqual(len(self.integrator.pit_features), 0)

    # ------------------------------------------------------------------ #
    # Read-only accessor
    # ------------------------------------------------------------------ #
    def test_pit_features_accessor_is_immutable(self):
        self.integrator.ingest_events(
            [RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)]
        )
        snapshot = self.integrator.pit_features
        self.assertIsInstance(snapshot, tuple)
        # A tuple is immutable by construction; re-calling yields an equal snapshot
        # and the accessor exposes no handle into internal state.
        self.assertEqual(snapshot, self.integrator.pit_features)

    # ------------------------------------------------------------------ #
    # Validation guards
    # ------------------------------------------------------------------ #
    def test_negative_publication_lag_rejected(self):
        event = RawAltDataEvent(
            "A", datetime(2026, 1, 6, 8, 0), timedelta(hours=-1), 10.0
        )
        with self.assertRaises(AltDataValidationError):
            self.integrator.ingest_events([event])

    def test_non_finite_feature_value_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            event = RawAltDataEvent(
                "A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), bad
            )
            with self.assertRaises(AltDataValidationError):
                self.integrator.ingest_events([event])

    def test_timezone_aware_event_timestamp_rejected(self):
        event = RawAltDataEvent(
            "A",
            datetime(2026, 1, 6, 8, 0, tzinfo=timezone.utc),
            timedelta(hours=2),
            10.0,
        )
        with self.assertRaises(AltDataValidationError):
            self.integrator.ingest_events([event])

    def test_timezone_aware_revised_date_rejected(self):
        event = RawAltDataEvent(
            "A",
            datetime(2026, 1, 6, 8, 0),
            timedelta(hours=2),
            10.0,
            revised_date=datetime(2026, 1, 9, 9, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(AltDataValidationError):
            self.integrator.ingest_events([event])

    def test_timezone_aware_trading_time_rejected(self):
        self.integrator.ingest_events(
            [RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0)]
        )
        with self.assertRaises(PointInTimeAlignmentError):
            self.integrator.align_to_trading_schedule(
                [datetime(2026, 1, 6, 16, 0, tzinfo=timezone.utc)]
            )

    def test_schema_version_drift_rejected(self):
        integrator = AltDataIntegrator(
            source_configs={"A": SourceConfig("A", expected_schema_version="1.0")}
        )
        event = RawAltDataEvent(
            "A",
            datetime(2026, 1, 6, 8, 0),
            timedelta(hours=2),
            10.0,
            schema_version="2.0",  # drift
        )
        with self.assertRaises(AltDataValidationError):
            integrator.ingest_events([event])

    def test_exception_hierarchy_base(self):
        # Backtest harnesses can catch the base class for any integrator error.
        self.assertTrue(issubclass(AltDataValidationError, AltDataIntegratorError))
        self.assertTrue(issubclass(PointInTimeAlignmentError, AltDataIntegratorError))

    # ------------------------------------------------------------------ #
    # Deterministic ordering
    # ------------------------------------------------------------------ #
    def test_ingest_order_independent_sort(self):
        a = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 1.0)
        b = RawAltDataEvent("B", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 2.0)
        # Same knowledge timestamp; tie broken by source_id deterministically.
        i1 = AltDataIntegrator()
        i2 = AltDataIntegrator()
        i1.ingest_events([a, b])
        i2.ingest_events([b, a])
        self.assertEqual(i1.pit_features, i2.pit_features)
        self.assertEqual(i1.pit_features[0].source_id, "A")
        self.assertEqual(i1.pit_features[1].source_id, "B")


if __name__ == "__main__":
    unittest.main()
