import threading
import unittest
from cross_asset_hedge_execution_synchronization import (
    CrossAssetHedgeSynchronizer, PrimaryFillEvent, HedgeOrder, HedgeSynchronizationStatus
)

class TestCrossAssetHedgeSynchronizer(unittest.TestCase):

    def setUp(self):
        # Options Delta Hedge Strategy:
        # Buy +1 Option Contract -> Short -50 Stock shares (Hedge Ratio = 50.0
        # = 0.50 delta x 100-share contract multiplier)
        self.synchronizer = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01",
            primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL",
            hedge_ratio=50.0,
            max_sync_delay_ms=100.0,
            unhedged_timeout_ms=500.0
        )

    def _fill(self, fill_id, qty, timestamp_ms=1000.0, strategy_id="OPT_DELTA_HEDGE_01"):
        return PrimaryFillEvent(
            strategy_id=strategy_id, fill_id=fill_id,
            primary_symbol="AAPL_250516_C200", fill_qty=qty,
            fill_price=5.50, timestamp_ms=timestamp_ms
        )

    def test_hedge_order_generation(self):
        # Primary fill: +10 call option contracts
        fill_event = self._fill("FILL_001", 10.0)

        hedge_order = self.synchronizer.generate_hedge_order(fill_event)
        # Required Hedge Qty = -1.0 * 10 * 50.0 = -500 shares (SELL AAPL)
        self.assertEqual(hedge_order.hedge_symbol, "AAPL")
        self.assertEqual(hedge_order.target_hedge_qty, -500.0)
        self.assertEqual(hedge_order.side, "SELL")

    def test_synchronized_fill_within_sla(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_002", 2.0))

        # Hedge filled at t = 1045 ms (45 ms delay <= 100 ms SLA)
        status = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-100.0,
            hedge_fill_timestamp_ms=1045.0
        )

        self.assertTrue(status.is_sync_sla_met)
        self.assertEqual(status.hedge_status, "SYNCHRONIZED_OK")
        self.assertEqual(status.sync_delay_ms, 45.0)

    def test_sync_delay_breach_on_late_full_fill(self):
        # Full fill arrives at t = 1250 ms: 100 ms < 250 ms <= 500 ms timeout
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_004", 2.0))

        status = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-100.0,
            hedge_fill_timestamp_ms=1250.0
        )

        self.assertFalse(status.is_sync_sla_met)
        self.assertEqual(status.hedge_status, "SYNC_DELAY_BREACH")
        self.assertEqual(status.sync_delay_ms, 250.0)
        self.assertEqual(status.unhedged_exposure_qty, 0.0)

    def test_unhedged_timeout_trigger(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_003", 5.0))

        # Hedge filled at t = 1600 ms (600 ms delay > 500 ms timeout!)
        status = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-250.0,
            hedge_fill_timestamp_ms=1600.0
        )

        self.assertFalse(status.is_sync_sla_met)
        self.assertEqual(status.hedge_status, "UNHEDGED_TIMEOUT_UNWIND")

    def test_partial_fills_accumulate_until_complete(self):
        # Regression: the first partial fill must NOT close the order — the
        # residual stays tracked and the next fill completes the hedge.
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_005", 10.0))

        first = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-200.0,
            hedge_fill_timestamp_ms=1030.0
        )
        self.assertEqual(first.hedge_status, "PARTIALLY_FILLED")
        self.assertTrue(first.is_sync_sla_met)
        self.assertEqual(first.unhedged_exposure_qty, 300.0)
        self.assertEqual(first.filled_hedge_qty, -200.0)
        # Order must still be pending so the residual is visible to the timeout sweep
        self.assertIn(h_order.hedge_order_id, self.synchronizer.pending_hedges)

        second = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-300.0,
            hedge_fill_timestamp_ms=1060.0
        )
        self.assertEqual(second.hedge_status, "SYNCHRONIZED_OK")
        self.assertEqual(second.unhedged_exposure_qty, 0.0)
        self.assertEqual(second.filled_hedge_qty, -500.0)
        self.assertNotIn(h_order.hedge_order_id, self.synchronizer.pending_hedges)

    def test_partial_fill_completed_late_is_delay_breach(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_006", 10.0))

        self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-200.0, hedge_fill_timestamp_ms=1040.0
        )
        final = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-300.0, hedge_fill_timestamp_ms=1300.0
        )
        self.assertEqual(final.hedge_status, "SYNC_DELAY_BREACH")
        self.assertFalse(final.is_sync_sla_met)

    def test_timeout_enforcement_with_no_fill_at_all(self):
        # Regression: a hedge that NEVER fills must be force-unwound by the timer
        # sweep, not silently tracked forever.
        unwind_calls = []
        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0,
            unwind_callback=lambda order: unwind_calls.append(order)
        )
        h_order = sync.generate_hedge_order(self._fill("FILL_007", 4.0))

        # Before timeout: nothing expires
        self.assertEqual(sync.enforce_unhedged_timeouts(now_ms=1200.0), [])
        # After timeout (t_fill=1000, timeout 500ms): unwound
        expired = sync.enforce_unhedged_timeouts(now_ms=2000.0)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].hedge_status, "UNHEDGED_TIMEOUT_UNWIND")
        self.assertEqual(expired[0].unhedged_exposure_qty, 200.0)
        self.assertEqual(expired[0].sync_delay_ms, 1000.0)
        self.assertNotIn(h_order.hedge_order_id, sync.pending_hedges)
        self.assertEqual(len(unwind_calls), 1)
        self.assertEqual(unwind_calls[0].hedge_order_id, h_order.hedge_order_id)

    def test_timeout_enforcement_after_partial_fill(self):
        # Residual exposure after a partial fill must be caught by the timer sweep.
        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0
        )
        h_order = sync.generate_hedge_order(self._fill("FILL_008", 10.0))
        sync.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-200.0, hedge_fill_timestamp_ms=1040.0
        )

        expired = sync.enforce_unhedged_timeouts(now_ms=2000.0)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].hedge_status, "UNHEDGED_TIMEOUT_UNWIND")
        self.assertEqual(expired[0].unhedged_exposure_qty, 300.0)
        self.assertEqual(expired[0].filled_hedge_qty, -200.0)

    def test_unwind_callback_failure_does_not_break_sweep(self):
        def broken_callback(order):
            raise RuntimeError("OMS gateway down")

        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0,
            unwind_callback=broken_callback
        )
        sync.generate_hedge_order(self._fill("FILL_009", 2.0))

        with self.assertLogs(level="CRITICAL"):
            expired = sync.enforce_unhedged_timeouts(now_ms=2000.0)
        self.assertEqual(len(expired), 1)

    def test_mark_dispatched_latency_and_breach(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_010", 2.0))

        latency = self.synchronizer.mark_dispatched(
            hedge_order_id=h_order.hedge_order_id, dispatch_timestamp_ms=1030.0
        )
        self.assertEqual(latency, 30.0)
        # Idempotent resend with the same timestamp is a no-op
        self.assertEqual(
            self.synchronizer.mark_dispatched(h_order.hedge_order_id, 1030.0), 30.0
        )
        # Dispatch SLA breach is reported, not raised
        h2 = self.synchronizer.generate_hedge_order(self._fill("FILL_011", 2.0))
        with self.assertLogs(level="WARNING"):
            breach_latency = self.synchronizer.mark_dispatched(h2.hedge_order_id, 1200.0)
        self.assertEqual(breach_latency, 200.0)

    def test_duplicate_dispatch_with_different_timestamp_rejected(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_012", 2.0))
        self.synchronizer.mark_dispatched(h_order.hedge_order_id, 1030.0)
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            self.synchronizer.mark_dispatched(h_order.hedge_order_id, 1060.0)

    def test_duplicate_fill_for_completed_order_rejected(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_013", 2.0))
        self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-100.0, hedge_fill_timestamp_ms=1040.0
        )
        with self.assertRaisesRegex(ValueError, "already finalized"):
            self.synchronizer.process_hedge_fill(
                hedge_order_id=h_order.hedge_order_id,
                filled_hedge_qty=-100.0, hedge_fill_timestamp_ms=1050.0
            )

    def test_wrong_side_hedge_fill_rejected(self):
        # Target is SELL -500; a BUY fill means the position books disagree.
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_014", 10.0))
        with self.assertRaisesRegex(ValueError, "Wrong-side"):
            self.synchronizer.process_hedge_fill(
                hedge_order_id=h_order.hedge_order_id,
                filled_hedge_qty=100.0, hedge_fill_timestamp_ms=1040.0
            )

    def test_overfill_completes_and_reports_negative_exposure(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_015", 2.0))
        with self.assertLogs(level="WARNING") as captured:
            status = self.synchronizer.process_hedge_fill(
                hedge_order_id=h_order.hedge_order_id,
                filled_hedge_qty=-120.0, hedge_fill_timestamp_ms=1040.0
            )
        self.assertTrue(any("overfill" in msg.lower() for msg in captured.output))
        self.assertEqual(status.hedge_status, "SYNCHRONIZED_OK")
        self.assertEqual(status.unhedged_exposure_qty, -20.0)
        self.assertNotIn(h_order.hedge_order_id, self.synchronizer.pending_hedges)

    def test_primary_symbol_mismatch_rejected(self):
        bad = PrimaryFillEvent(
            strategy_id="OPT_DELTA_HEDGE_01", fill_id="FILL_016",
            primary_symbol="MSFT_250516_C400", fill_qty=1.0,
            fill_price=3.00, timestamp_ms=1000.0
        )
        with self.assertRaisesRegex(ValueError, "Primary symbol mismatch"):
            self.synchronizer.generate_hedge_order(bad)

    def test_strategy_id_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "Strategy ID mismatch"):
            self.synchronizer.generate_hedge_order(self._fill("FILL_017", 1.0, strategy_id="OTHER_STRATEGY"))

    def test_malformed_fill_events_rejected(self):
        for qty in (float("nan"), float("inf"), 0.0):
            with self.assertRaisesRegex(ValueError, "fill_qty"):
                self.synchronizer.generate_hedge_order(self._fill("FILL_018", qty))
        for price in (float("nan"), -1.0):
            with self.assertRaisesRegex(ValueError, "fill_price"):
                self.synchronizer.generate_hedge_order(
                    PrimaryFillEvent(
                        strategy_id="OPT_DELTA_HEDGE_01", fill_id="FILL_019",
                        primary_symbol="AAPL_250516_C200", fill_qty=1.0,
                        fill_price=price, timestamp_ms=1000.0
                    )
                )

    def test_malformed_hedge_fill_rejected(self):
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_020", 2.0))
        for qty in (float("nan"), float("inf"), 0.0):
            with self.assertRaisesRegex(ValueError, "filled_hedge_qty"):
                self.synchronizer.process_hedge_fill(h_order.hedge_order_id, qty, 1040.0)
        with self.assertRaisesRegex(ValueError, "precedes primary fill"):
            self.synchronizer.process_hedge_fill(h_order.hedge_order_id, -100.0, 900.0)
        with self.assertRaisesRegex(ValueError, "Unknown hedge order ID"):
            self.synchronizer.process_hedge_fill("HEDGE_NONEXISTENT", -100.0, 1040.0)

    def test_constructor_validation(self):
        with self.assertRaisesRegex(ValueError, "hedge_ratio"):
            CrossAssetHedgeSynchronizer("S", "P", "H", hedge_ratio=float("nan"))
        with self.assertRaisesRegex(ValueError, "hedge_ratio"):
            CrossAssetHedgeSynchronizer("S", "P", "H", hedge_ratio=0.0)
        with self.assertRaisesRegex(ValueError, "max_sync_delay_ms"):
            CrossAssetHedgeSynchronizer("S", "P", "H", max_sync_delay_ms=0.0)
        with self.assertRaisesRegex(ValueError, "unhedged_timeout_ms"):
            # Timeout shorter than the sync SLA makes breach states unobservable
            CrossAssetHedgeSynchronizer("S", "P", "H", max_sync_delay_ms=100.0, unhedged_timeout_ms=50.0)

    # --- duplicate primary fill events (FIX resend / gateway replay) -------

    def test_duplicate_primary_fill_while_hedge_live_is_rejected(self):
        # A redelivered execution report must not become a second hedge order.
        # Regenerating used to overwrite the live order, wiping accumulated fill
        # state and handing the caller a fresh order to dispatch.
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_021", 10.0))
        self.synchronizer.mark_dispatched(h_order.hedge_order_id, 1020.0)
        self.synchronizer.process_hedge_fill(h_order.hedge_order_id, -200.0, 1030.0)

        with self.assertRaisesRegex(ValueError, "Duplicate primary fill"):
            self.synchronizer.generate_hedge_order(self._fill("FILL_021", 10.0))

        # Fill and dispatch state survived the rejected duplicate.
        live = self.synchronizer.pending_hedges[h_order.hedge_order_id]
        self.assertEqual(live.filled_hedge_qty, -200.0)
        self.assertEqual(live.dispatched_timestamp_ms, 1020.0)
        self.assertEqual(len(self.synchronizer.pending_hedges), 1)

    def test_duplicate_primary_fill_after_completion_is_rejected(self):
        # Re-hedging an already-hedged fill doubles the position. The finalized
        # guard used to be bypassed because regeneration re-added the id to
        # pending_hedges.
        h_order = self.synchronizer.generate_hedge_order(self._fill("FILL_022", 2.0))
        self.synchronizer.process_hedge_fill(h_order.hedge_order_id, -100.0, 1040.0)

        with self.assertRaisesRegex(ValueError, "already finalized"):
            self.synchronizer.generate_hedge_order(self._fill("FILL_022", 2.0))
        self.assertNotIn(h_order.hedge_order_id, self.synchronizer.pending_hedges)

    def test_distinct_fill_ids_still_generate_separate_hedges(self):
        # Deduplication must key on fill_id only — two genuine fills of the same
        # size at the same timestamp are two hedges, not a duplicate.
        a = self.synchronizer.generate_hedge_order(self._fill("FILL_023A", 2.0))
        b = self.synchronizer.generate_hedge_order(self._fill("FILL_023B", 2.0))
        self.assertNotEqual(a.hedge_order_id, b.hedge_order_id)
        self.assertEqual(len(self.synchronizer.pending_hedges), 2)

    # --- timeout routing: late fill vs timer sweep must agree --------------

    def test_late_partial_fill_routes_to_unwind_callback(self):
        # The severe case: a partial fill arriving past the timeout finalizes the
        # order, so the sweep can never see it again. Without routing it to the
        # unwind callback the residual exposure left tracking entirely and the
        # primary leg was never unwound by any path.
        unwind_calls = []
        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0,
            unwind_callback=lambda order: unwind_calls.append(order)
        )
        h_order = sync.generate_hedge_order(self._fill("FILL_024", 10.0))

        status = sync.process_hedge_fill(h_order.hedge_order_id, -200.0, 1600.0)

        self.assertEqual(status.hedge_status, "UNHEDGED_TIMEOUT_UNWIND")
        self.assertEqual(status.unhedged_exposure_qty, 300.0)
        self.assertEqual(len(unwind_calls), 1)
        self.assertEqual(unwind_calls[0].hedge_order_id, h_order.hedge_order_id)
        # Finalized, so the sweep finds nothing left to do.
        self.assertEqual(sync.enforce_unhedged_timeouts(now_ms=99999.0), [])

    def test_late_fill_and_timer_sweep_route_to_unwind_identically(self):
        # Same breach, two observers. Which one happens to see it first must not
        # decide whether the primary leg gets unwound.
        def build():
            calls = []
            sync = CrossAssetHedgeSynchronizer(
                strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
                hedge_symbol="AAPL", hedge_ratio=50.0,
                unwind_callback=lambda order: calls.append(order)
            )
            return sync, calls

        via_fill, fill_calls = build()
        h1 = via_fill.generate_hedge_order(self._fill("FILL_025", 10.0))
        via_fill.process_hedge_fill(h1.hedge_order_id, -200.0, 1600.0)

        via_sweep, sweep_calls = build()
        h2 = via_sweep.generate_hedge_order(self._fill("FILL_025", 10.0))
        via_sweep.process_hedge_fill(h2.hedge_order_id, -200.0, 1040.0)
        via_sweep.enforce_unhedged_timeouts(now_ms=1600.0)

        self.assertEqual(len(fill_calls), len(sweep_calls), 1)
        self.assertEqual(len(fill_calls), 1)

    def test_unwind_callback_failure_on_late_fill_does_not_mask_status(self):
        def broken_callback(order):
            raise RuntimeError("OMS gateway down")

        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0, unwind_callback=broken_callback
        )
        h_order = sync.generate_hedge_order(self._fill("FILL_026", 10.0))
        with self.assertLogs(level="CRITICAL"):
            status = sync.process_hedge_fill(h_order.hedge_order_id, -200.0, 1600.0)
        self.assertEqual(status.hedge_status, "UNHEDGED_TIMEOUT_UNWIND")

    # --- degenerate hedge quantity ----------------------------------------

    def test_hedge_quantity_rounding_to_zero_is_rejected(self):
        # A target that rounds away to 0.0 used to yield a zero-quantity order
        # still tagged with a side, which the OMS would happily dispatch.
        tiny = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=1e-9
        )
        with self.assertRaisesRegex(ValueError, "rounds to zero"):
            tiny.generate_hedge_order(self._fill("FILL_027", 1.0))

    # --- concurrency -------------------------------------------------------

    def test_unwind_callback_is_invoked_outside_the_lock(self):
        # The docs tell operators to drive the sweep from a scheduling thread while
        # fills arrive on the gateway thread. If the unwind callback ran while the
        # synchronizer lock was held, any callback touching the synchronizer from
        # another thread would deadlock the sweep.
        reentered = []

        def callback_reentering_from_another_thread(order):
            def worker():
                reentered.append(sync.enforce_unhedged_timeouts(now_ms=2500.0))
            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive(), "unwind callback holds the lock — deadlocked")

        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0,
            unwind_callback=callback_reentering_from_another_thread
        )
        sync.generate_hedge_order(self._fill("FILL_028", 2.0))
        expired = sync.enforce_unhedged_timeouts(now_ms=2000.0)
        self.assertEqual(len(expired), 1)
        self.assertEqual(reentered, [[]])

    def test_concurrent_fills_and_sweeps_keep_state_consistent(self):
        # Gateway thread delivering fills against a timer thread sweeping: every
        # hedge must end up finalized exactly once, with no lost update to the
        # accumulated quantity and no double-delete from pending_hedges.
        sync = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01", primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL", hedge_ratio=50.0
        )
        order_ids = [
            sync.generate_hedge_order(
                self._fill(f"FILL_C{i}", 10.0, timestamp_ms=1000.0 + i)
            ).hedge_order_id
            for i in range(40)
        ]
        errors = []

        def fill_worker():
            for oid in order_ids:
                try:
                    sync.process_hedge_fill(oid, -250.0, 1100.0)
                    sync.process_hedge_fill(oid, -250.0, 1120.0)
                except ValueError:
                    pass                       # lost the race to the sweep: expected
                except Exception as exc:       # anything else is a real defect
                    errors.append(exc)

        def sweep_worker():
            for _ in range(40):
                try:
                    sync.enforce_unhedged_timeouts(now_ms=2000.0)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=fill_worker), threading.Thread(target=sweep_worker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            self.assertFalse(t.is_alive(), "worker thread deadlocked")

        self.assertEqual(errors, [])
        self.assertEqual(sync.pending_hedges, {})
        self.assertEqual(len(sync.completed_hedge_ids), 40)


if __name__ == '__main__':
    unittest.main()
