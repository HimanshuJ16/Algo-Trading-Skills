"""Unit tests for secrets-rotation-without-bot-downtime.

The suite is organised around the failure modes that make a rotation unsafe
rather than around the methods: an unproven credential reaching live order flow,
an old credential surviving a "successful" revocation, a trading thread reading
a half-applied swap, and a fallback that is not actually available.
"""
import logging
import threading
import time
import unittest

from secrets_rotator import (
    Credential,
    CredentialInUse,
    NoActiveCredential,
    NoFallbackAvailable,
    OverlapWindowOpen,
    RotationInProgress,
    RotationState,
    SecretsRotator,
    accept_without_validation,
)

logging.getLogger("secrets_rotator").setLevel(logging.CRITICAL)


def _accept(_credential):
    return True


def _reject(_credential):
    return False


def _seeded(**kwargs):
    """A rotator holding key_v1, with an accept-everything probe by default."""
    kwargs.setdefault("validate_fn", _accept)
    rotator = SecretsRotator(**kwargs)
    rotator.set_initial_credential("key_v1", "secret_v1")
    return rotator


class TestRotationHappyPath(unittest.TestCase):
    def test_valid_candidate_is_published_and_old_key_retained(self):
        rotator = _seeded()

        result = rotator.rotate("key_v2", "secret_v2")

        self.assertTrue(result.success)
        self.assertEqual(result.active_key_id, "key_v2")
        self.assertEqual(result.state, RotationState.SWAPPED)
        self.assertEqual(rotator.current().key_id, "key_v2")
        self.assertIsNotNone(rotator.previous_credential)
        self.assertEqual(rotator.previous_credential.key_id, "key_v1")

    def test_candidate_is_probed_before_it_is_published(self):
        """The probe must see the candidate while the OLD key is still active.

        Probing after the swap would mean live order flow reached the candidate
        before anything proved it works.
        """
        active_during_probe = []

        def probe(credential):
            active_during_probe.append(
                (credential.key_id, rotator.current().key_id)
            )
            return True

        rotator = SecretsRotator(validate_fn=probe)
        rotator.set_initial_credential("key_v1", "secret_v1")
        rotator.rotate("key_v2", "secret_v2")

        self.assertEqual(active_during_probe, [("key_v2", "key_v1")])

    def test_status_reports_without_exposing_secrets(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        status = rotator.status()

        self.assertEqual(status.active_key_id, "key_v2")
        self.assertEqual(status.previous_key_id, "key_v1")
        self.assertEqual(status.state, RotationState.SWAPPED)
        self.assertNotIn("secret_v2", repr(status))


class TestValidationGate(unittest.TestCase):
    def test_rejected_candidate_leaves_active_credential_untouched(self):
        rotator = _seeded(validate_fn=_reject)

        result = rotator.rotate("key_bad", "secret_bad")

        self.assertFalse(result.success)
        self.assertEqual(result.active_key_id, "key_v1")
        self.assertEqual(rotator.current().key_id, "key_v1")
        self.assertIsNone(rotator.previous_credential)
        self.assertFalse(result.indeterminate)

    def test_rejected_candidate_is_not_reported_as_a_rollback(self):
        """Regression: VALIDATION_FAILED and ROLLED_BACK must stay distinct.

        The previous implementation returned FAILED_ROLLBACK for a pre-swap
        rejection, so an operator paging on "we rolled back off a live key"
        could not tell it apart from "the candidate was refused and nothing
        happened".
        """
        rotator = _seeded(validate_fn=_reject)

        result = rotator.rotate("key_bad", "secret_bad")

        self.assertEqual(result.state, RotationState.VALIDATION_FAILED)
        self.assertNotEqual(result.state, RotationState.ROLLED_BACK)

    def test_probe_that_raises_is_indeterminate_and_does_not_swap(self):
        """A timed-out probe has not proven the candidate bad, only unproven."""

        def probe(_credential):
            raise TimeoutError("broker did not answer")

        rotator = _seeded(validate_fn=probe)

        result = rotator.rotate("key_v2", "secret_v2")

        self.assertFalse(result.success)
        self.assertTrue(result.indeterminate)
        self.assertEqual(rotator.current().key_id, "key_v1")

    def test_probe_that_raises_does_not_strand_the_state_machine(self):
        """Regression: the exception used to propagate, leaving VALIDATING_NEW."""

        def probe(_credential):
            raise ConnectionError("connection reset")

        rotator = _seeded(validate_fn=probe)
        rotator.rotate("key_v2", "secret_v2")

        self.assertNotEqual(rotator.state, RotationState.VALIDATING_NEW)
        self.assertEqual(rotator.state, RotationState.VALIDATION_FAILED)
        # And the rotator is still usable afterwards.
        rotator.validate_fn = _accept
        self.assertTrue(rotator.rotate("key_v3", "secret_v3").success)

    def test_validate_fn_is_mandatory(self):
        with self.assertRaises(ValueError):
            SecretsRotator()

    def test_opting_out_of_validation_is_possible_but_explicit(self):
        rotator = _seeded(validate_fn=accept_without_validation)
        with self.assertLogs("secrets_rotator", level="WARNING"):
            result = rotator.rotate("key_v2", "secret_v2")
        self.assertTrue(result.success)


class TestRevocation(unittest.TestCase):
    def test_revocation_calls_the_venue(self):
        revoked = []
        rotator = _seeded(revoke_fn=lambda cred: revoked.append(cred.key_id))
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.revoke_previous()

        self.assertTrue(result.success)
        self.assertEqual(result.state, RotationState.REVOKED_OLD)
        self.assertEqual(revoked, ["key_v1"])
        self.assertIsNone(rotator.previous_credential)

    def test_venue_refusing_revocation_is_reported_as_failure(self):
        """Regression: the old implementation always reported success, so a
        caller believed a still-live key had been killed."""

        def revoke(_credential):
            raise RuntimeError("venue returned 500")

        rotator = _seeded(revoke_fn=revoke)
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.revoke_previous()

        self.assertFalse(result.success)
        self.assertEqual(result.state, RotationState.REVOCATION_FAILED)
        # Still retained, so a fallback remains possible and the key is tracked.
        self.assertIsNotNone(rotator.previous_credential)

    def test_forgetting_locally_is_not_reported_as_revocation(self):
        """With no revoke_fn there is no way to revoke; say so."""
        rotator = _seeded()  # no revoke_fn
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.revoke_previous()

        self.assertFalse(result.success)
        self.assertEqual(result.state, RotationState.REVOCATION_FAILED)
        self.assertIn("REMAINS VALID", result.message)

    def test_revoking_with_nothing_to_revoke_is_a_no_op(self):
        rotator = _seeded(revoke_fn=lambda cred: None)

        result = rotator.revoke_previous()

        self.assertFalse(result.success)
        self.assertEqual(result.message, "No previous credential to revoke.")


class TestOverlapWindow(unittest.TestCase):
    def test_in_flight_request_blocks_revocation(self):
        rotator = _seeded(revoke_fn=lambda cred: None)

        with rotator.use() as leased:          # leased against key_v1
            self.assertEqual(leased.key_id, "key_v1")
            rotator.rotate("key_v2", "secret_v2")
            with self.assertRaises(CredentialInUse):
                rotator.revoke_previous()

        # Lease released -> revocation now permitted.
        self.assertTrue(rotator.revoke_previous().success)

    def test_lease_follows_the_credential_across_a_swap(self):
        rotator = _seeded(revoke_fn=lambda cred: None)

        with rotator.use():
            rotator.rotate("key_v2", "secret_v2")
            status = rotator.status()
            # The lease was taken on key_v1, which is now 'previous'.
            self.assertEqual(status.previous_leases, 1)
            self.assertEqual(status.active_leases, 0)

    def test_force_overrides_the_in_flight_gate(self):
        rotator = _seeded(revoke_fn=lambda cred: None)

        with rotator.use():
            rotator.rotate("key_v2", "secret_v2")
            result = rotator.revoke_previous(force=True)

        self.assertTrue(result.success)

    def test_minimum_overlap_window_is_enforced(self):
        rotator = _seeded(revoke_fn=lambda cred: None, min_overlap_seconds=30.0)
        rotator.rotate("key_v2", "secret_v2")

        with self.assertRaises(OverlapWindowOpen):
            rotator.revoke_previous()

    def test_overlap_window_uses_a_monotonic_clock(self):
        """A wall-clock jump (NTP step) must not open the window early."""
        rotator = _seeded(revoke_fn=lambda cred: None, min_overlap_seconds=30.0)
        rotator.rotate("key_v2", "secret_v2")

        real_time = time.time
        try:
            time.time = lambda: real_time() + 3600  # pretend the clock jumped
            with self.assertRaises(OverlapWindowOpen):
                rotator.revoke_previous()
        finally:
            time.time = real_time

    def test_drain_previous_returns_when_leases_clear(self):
        rotator = _seeded(revoke_fn=lambda cred: None)
        released = threading.Event()

        def worker():
            with rotator.use():
                released.wait(2.0)

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.05)
        rotator.rotate("key_v2", "secret_v2")

        self.assertFalse(rotator.drain_previous(timeout=0.1))
        released.set()
        self.assertTrue(rotator.drain_previous(timeout=2.0))
        thread.join()

    def test_drain_previous_with_nothing_outstanding(self):
        rotator = _seeded()
        self.assertTrue(rotator.drain_previous(timeout=0.0))


class TestFallback(unittest.TestCase):
    def test_fallback_restores_the_previous_credential(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.fallback_to_previous()

        self.assertTrue(result.success)
        self.assertEqual(result.active_key_id, "key_v1")
        self.assertEqual(result.state, RotationState.ROLLED_BACK)
        self.assertEqual(rotator.current().key_id, "key_v1")

    def test_fallback_after_revocation_raises_rather_than_reporting_failure(self):
        """Once revoked, the fallback is gone; the caller must not read a
        falsy result as 'still on the old key'."""
        rotator = _seeded(revoke_fn=lambda cred: None)
        rotator.rotate("key_v2", "secret_v2")
        rotator.revoke_previous()

        with self.assertRaises(NoFallbackAvailable):
            rotator.fallback_to_previous()

    def test_on_activate_runs_for_the_restored_credential(self):
        """Nonce floors and similar per-key state must be restored on rollback."""
        activated = []
        rotator = _seeded(on_activate=lambda cred: activated.append(cred.key_id))
        rotator.rotate("key_v2", "secret_v2")
        rotator.fallback_to_previous()

        self.assertEqual(activated, ["key_v2", "key_v1"])

    def test_on_activate_failure_aborts_a_rotation(self):
        def on_activate(credential):
            if credential.key_id == "key_v2":
                raise RuntimeError("cannot restore nonce floor")

        rotator = _seeded(on_activate=on_activate)
        result = rotator.rotate("key_v2", "secret_v2")

        self.assertFalse(result.success)
        self.assertEqual(rotator.current().key_id, "key_v1")

    def test_on_activate_failure_does_not_block_a_fallback(self):
        """Refusing the fallback would leave the bot with no working key."""
        calls = []

        def on_activate(credential):
            calls.append(credential.key_id)
            if credential.key_id == "key_v1" and len(calls) > 1:
                raise RuntimeError("nonce floor unavailable")

        rotator = _seeded(on_activate=on_activate)
        rotator.rotate("key_v2", "secret_v2")
        with self.assertLogs("secrets_rotator", level="ERROR"):
            result = rotator.fallback_to_previous()

        self.assertTrue(result.success)
        self.assertEqual(rotator.current().key_id, "key_v1")


class TestCredentialTracking(unittest.TestCase):
    def test_second_rotation_before_revocation_is_refused(self):
        """Regression: rotating twice used to silently orphan key_v1, leaving it
        live at the venue with nothing tracking it."""
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        with self.assertRaises(RotationInProgress):
            rotator.rotate("key_v3", "secret_v3")

        self.assertEqual(rotator.previous_credential.key_id, "key_v1")

    def test_second_rotation_can_be_forced(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.rotate("key_v3", "secret_v3", force=True)

        self.assertTrue(result.success)
        self.assertEqual(rotator.previous_credential.key_id, "key_v2")

    def test_reseeding_during_an_open_rotation_is_refused(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        with self.assertRaises(RotationInProgress):
            rotator.set_initial_credential("key_x", "secret_x")

    def test_rotating_to_the_same_key_id_is_refused(self):
        """An 'overlap' between a credential and itself is not a fallback."""
        rotator = _seeded()

        with self.assertRaises(ValueError):
            rotator.rotate("key_v1", "secret_v1_new")

    def test_rotating_without_an_active_credential_is_refused(self):
        rotator = SecretsRotator(validate_fn=_accept)

        with self.assertRaises(NoActiveCredential):
            rotator.rotate("key_v2", "secret_v2")

    def test_empty_credential_material_is_refused(self):
        rotator = _seeded()

        for key_id, secret in (("", "s"), ("   ", "s"), ("key_v2", "")):
            with self.subTest(key_id=key_id, secret=secret):
                with self.assertRaises(ValueError):
                    rotator.rotate(key_id, secret)

    def test_history_is_bounded(self):
        rotator = _seeded(validate_fn=_reject, history_limit=3)
        for i in range(10):
            rotator.rotate(f"key_bad_{i}", "secret")

        self.assertEqual(len(rotator.rotation_history), 3)


class TestSecretLeakage(unittest.TestCase):
    def test_credential_repr_redacts_the_secret(self):
        credential = Credential(key_id="key_v1", secret="hunter2-super-secret")

        for rendering in (repr(credential), str(credential), f"{credential}"):
            self.assertNotIn("hunter2-super-secret", rendering)
            self.assertIn("key_v1", rendering)
            self.assertIn("redacted", rendering)

    def test_rotator_repr_redacts_secrets(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        rendering = repr(rotator)

        self.assertNotIn("secret_v1", rendering)
        self.assertNotIn("secret_v2", rendering)
        self.assertIn("key_v2", rendering)

    def test_history_records_key_ids_but_never_secrets(self):
        rotator = _seeded()
        rotator.rotate("key_v2", "secret_v2")

        rendering = repr(list(rotator.rotation_history))

        self.assertIn("key_v2", rendering)
        self.assertNotIn("secret_v2", rendering)

    def test_secret_remains_reachable_deliberately(self):
        credential = Credential(key_id="key_v1", secret="s3cr3t")
        self.assertEqual(credential.reveal(), "s3cr3t")
        self.assertEqual(credential.secret, "s3cr3t")


class TestConcurrency(unittest.TestCase):
    def test_reader_never_observes_a_half_applied_swap(self):
        """A swap is two assignments; without a lock a reader can see the
        interleaving. Every observation must be a credential whose key_id and
        secret belong together."""
        rotator = _seeded()
        torn = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                with rotator.use() as credential:
                    suffix = credential.key_id.split("_")[-1]
                    if credential.secret != f"secret_{suffix}":
                        torn.append((credential.key_id, credential.secret))

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for thread in readers:
            thread.start()
        try:
            for i in range(2, 60):
                rotator.rotate(f"key_v{i}", f"secret_v{i}", force=True)
        finally:
            stop.set()
            for thread in readers:
                thread.join()

        self.assertEqual(torn, [])

    def test_concurrent_rotations_do_not_orphan_a_live_credential(self):
        """A scheduled rotation and a manual one firing together.

        validate_fn is a network probe, so it runs unlocked and the two calls
        overlap. Exactly one must win; the other must be refused outright, so
        the caller knows its candidate was never adopted. Silently letting both
        proceed leaves a credential valid at the venue that nothing tracks and
        nobody will revoke.
        """
        def slow_probe(_credential):
            time.sleep(0.05)
            return True

        rotator = _seeded(validate_fn=slow_probe)
        refused = []

        def rotate(key_id):
            try:
                rotator.rotate(key_id, f"secret_{key_id}")
            except RotationInProgress:
                refused.append(key_id)

        threads = [
            threading.Thread(target=rotate, args=(k,))
            for k in ("key_v2", "key_v3")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(refused), 1, "exactly one rotation must be refused")
        winner = ({"key_v2", "key_v3"} - set(refused)).pop()
        self.assertEqual(rotator.current().key_id, winner)
        self.assertEqual(rotator.previous_credential.key_id, "key_v1")

    def test_rotation_aborts_if_active_credential_moved_during_validation(self):
        """A fallback landing mid-probe makes the captured 'outgoing' stale."""
        gate = threading.Event()

        def slow_probe(credential):
            if credential.key_id == "key_v3":
                gate.wait(2.0)
            return True

        rotator = _seeded(validate_fn=slow_probe)
        rotator.rotate("key_v2", "secret_v2")   # active=key_v2, previous=key_v1

        result_box = []

        def rotate_v3():
            result_box.append(
                rotator.rotate("key_v3", "secret_v3", force=True)
            )

        thread = threading.Thread(target=rotate_v3)
        thread.start()
        time.sleep(0.05)
        rotator.fallback_to_previous()          # active moves back to key_v1
        gate.set()
        thread.join()

        self.assertFalse(result_box[0].success)
        self.assertEqual(rotator.current().key_id, "key_v1")

    def test_lease_accounting_survives_concurrent_use(self):
        rotator = _seeded()
        barrier = threading.Barrier(5)

        def worker():
            with rotator.use():
                barrier.wait(2.0)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        barrier.wait(2.0)
        for thread in threads:
            thread.join()

        self.assertEqual(rotator.status().active_leases, 0)


if __name__ == "__main__":
    unittest.main()
