"""Unit tests for backtest-audit-trail-for-regulatory-review."""
import hashlib
import json
import logging
import os
import tempfile
import unittest

from regulatory_audit_trail import (
    AuditManifestError,
    ManifestVerificationError,
    RegulatoryAuditTrailEngine,
)

KEY_A = b"\x01" * 32
KEY_B = b"\x02" * 32

# Engine logs an ERROR on detected forgery by design; keep test output readable.
logging.getLogger("regulatory_audit_trail").setLevel(logging.CRITICAL)


def _engine(key=KEY_A, org="AlphaHedge LLC", key_id="default"):
    return RegulatoryAuditTrailEngine(signing_key=key, organization=org, signing_key_id=key_id)


def _manifest(engine, **overrides):
    kwargs = dict(
        strategy_id="stat_arb_v3",
        git_commit_sha="41c1d05a8b792e01",
        data_checksums={"data/train.parquet": "a5" * 32},
        parameters={"lookback": 20, "z_entry": 2.0},
        metrics_summary={"sharpe_ratio": 2.15, "max_drawdown_pct": 8.4},
        data_sources=["NYSE", "NASDAQ"],
        notes="Initial backtest run",
    )
    kwargs.update(overrides)
    return engine.build_manifest(**kwargs)


class TestBuildAndVerify(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_build_and_verify(self):
        m = _manifest(self.engine)
        self.assertEqual(len(m.content_digest_sha256), 64)
        self.assertEqual(len(m.manifest_hmac_sha256), 64)
        self.assertNotEqual(m.content_digest_sha256, m.manifest_hmac_sha256)
        self.assertTrue(self.engine.verify_manifest(m))
        self.assertEqual(m.organization, "AlphaHedge LLC")
        self.assertEqual(m.data_sources, ["NYSE", "NASDAQ"])

    def test_timestamp_is_integer_nanoseconds(self):
        m = _manifest(self.engine)
        self.assertIsInstance(m.execution_timestamp_ns, int)

    def test_iso_timestamp_has_microsecond_granularity(self):
        m = _manifest(self.engine)
        iso = m.execution_timestamp_iso
        self.assertTrue(iso.endswith("Z"), iso)
        fractional = iso.split(".")[1].rstrip("Z")
        self.assertEqual(len(fractional), 6, f"expected 6 fractional digits, got {iso!r}")

    def test_python_version_is_recorded(self):
        # The workflow claims environment capture; assert it actually happens.
        m = _manifest(self.engine)
        self.assertRegex(m.python_version, r"^\d+\.\d+\.\d+")


class TestForgeryResistance(unittest.TestCase):
    """The threat this skill exists to address: a party who edits results and re-hashes.

    These fail against any unkeyed-digest implementation, where recomputing the
    hash is sufficient to make a forged manifest verify.
    """

    def setUp(self):
        self.engine = _engine()

    def _recompute_unkeyed_digest(self, manifest):
        """What an attacker without the key can do: recompute the public digest."""
        payload = self.engine._canonical_payload(manifest)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        manifest.content_digest_sha256 = hashlib.sha256(canonical).hexdigest()

    def test_inflated_metrics_with_recomputed_digest_is_rejected(self):
        m = _manifest(self.engine, metrics_summary={"sharpe_ratio": 1.05, "max_drawdown_pct": 22.0})
        self.assertTrue(self.engine.verify_manifest(m))

        m.metrics_summary["sharpe_ratio"] = 4.50
        m.metrics_summary["max_drawdown_pct"] = 3.0
        self._recompute_unkeyed_digest(m)

        # Digest is now internally consistent; only the HMAC exposes the forgery.
        self.assertEqual(m.content_digest_sha256, self.engine._digests(m)[0])
        self.assertFalse(self.engine.verify_manifest(m))

    def test_forged_export_is_rejected_on_load(self):
        m = _manifest(self.engine, metrics_summary={"sharpe_ratio": 1.05})
        data = json.loads(self.engine.export_manifest_json(m))
        data["metrics"]["sharpe_ratio"] = 9.99

        payload = {k: v for k, v in data.items()
                   if k not in ("content_digest_sha256", "manifest_hmac_sha256")}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        data["content_digest_sha256"] = hashlib.sha256(canonical).hexdigest()

        with self.assertRaises(ManifestVerificationError):
            self.engine.load_manifest_from_json(json.dumps(data))

    def test_manifest_from_different_key_is_rejected(self):
        m = _manifest(_engine(key=KEY_B))
        self.assertFalse(_engine(key=KEY_A).verify_manifest(m))

    def test_key_id_mismatch_is_rejected(self):
        m = _manifest(_engine(key=KEY_A, key_id="2024-q1"))
        self.assertFalse(_engine(key=KEY_A, key_id="2024-q2").verify_manifest(m))

    def test_key_id_is_covered_by_the_signature(self):
        m = _manifest(self.engine)
        m.signing_key_id = "attacker-supplied"
        self.assertFalse(self.engine.verify_manifest(m))


class TestTamperDetectionWithoutResigning(unittest.TestCase):
    """Accidental corruption / naive edits, caught by the content digest alone."""

    def setUp(self):
        self.engine = _engine()

    def test_each_signed_field_is_covered(self):
        cases = {
            "metrics": lambda m: m.metrics_summary.__setitem__("sharpe_ratio", 4.5),
            "git_sha": lambda m: setattr(m, "git_commit_sha", "f" * 40),
            "checksum": lambda m: m.data_checksums_sha256.__setitem__("data/train.parquet", "0" * 64),
            "organization": lambda m: setattr(m, "organization", "Different Org"),
            "strategy_id": lambda m: setattr(m, "strategy_id", "other_strategy"),
            "parameters": lambda m: m.parameters.__setitem__("lookback", 999),
            "data_sources": lambda m: m.data_sources.append("LSE"),
            "notes": lambda m: setattr(m, "notes", "rewritten"),
            "timestamp": lambda m: setattr(m, "execution_timestamp_ns", 1),
            "platform": lambda m: setattr(m, "platform_info", "Other OS"),
            "python_version": lambda m: setattr(m, "python_version", "0.0.0"),
        }
        for name, mutate in cases.items():
            with self.subTest(field=name):
                m = _manifest(self.engine)
                self.assertTrue(self.engine.verify_manifest(m))
                mutate(m)
                self.assertFalse(self.engine.verify_manifest(m))


class TestManifestIsolation(unittest.TestCase):
    """A signed manifest must not alias caller-owned containers."""

    def setUp(self):
        self.engine = _engine()

    def test_mutating_caller_dicts_after_build_does_not_affect_manifest(self):
        params = {"lookback": 20}
        metrics = {"sharpe_ratio": 2.0}
        checksums = {"data/a.csv": "ab" * 32}
        sources = ["NYSE"]

        m = self.engine.build_manifest(
            strategy_id="s", git_commit_sha="abc123",
            data_checksums=checksums, parameters=params,
            metrics_summary=metrics, data_sources=sources,
        )
        params["lookback"] = 999
        metrics["sharpe_ratio"] = 99.0
        checksums["data/a.csv"] = "00" * 32
        sources.append("LSE")

        self.assertEqual(m.parameters["lookback"], 20)
        self.assertEqual(m.metrics_summary["sharpe_ratio"], 2.0)
        self.assertEqual(m.data_checksums_sha256["data/a.csv"], "ab" * 32)
        self.assertEqual(m.data_sources, ["NYSE"])
        self.assertTrue(self.engine.verify_manifest(m))

    def test_nested_parameter_structures_are_copied(self):
        params = {"grid": {"window": [10, 20]}}
        m = self.engine.build_manifest(
            strategy_id="s", git_commit_sha="abc123", data_checksums={},
            parameters=params, metrics_summary={"sharpe": 1.0},
        )
        params["grid"]["window"].append(30)
        self.assertEqual(m.parameters["grid"]["window"], [10, 20])
        self.assertTrue(self.engine.verify_manifest(m))


class TestCanonicalisation(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_non_finite_metrics_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(AuditManifestError):
                    _manifest(self.engine, metrics_summary={"sharpe_ratio": bad})

    def test_export_is_valid_strict_json(self):
        m = _manifest(self.engine)
        exported = self.engine.export_manifest_json(m)
        # Strict parsers reject NaN/Infinity; ensure a strict reload succeeds.
        json.loads(exported, parse_constant=lambda c: (_ for _ in ()).throw(
            AssertionError(f"non-JSON constant {c!r} in export")))

    def test_export_is_byte_stable(self):
        m = _manifest(self.engine)
        self.assertEqual(self.engine.export_manifest_json(m),
                         self.engine.export_manifest_json(m))

    def test_export_payload_matches_signed_payload(self):
        # Guards the historical hazard of export and signing formats drifting apart.
        m = _manifest(self.engine)
        exported = json.loads(self.engine.export_manifest_json(m))
        signed = self.engine._canonical_payload(m)
        for key, value in signed.items():
            self.assertIn(key, exported)
            self.assertEqual(exported[key], value, f"field {key!r} differs")

    def test_unicode_notes_round_trip(self):
        m = _manifest(self.engine, notes="rebalance — München ✓")
        loaded = self.engine.load_manifest_from_json(self.engine.export_manifest_json(m))
        self.assertEqual(loaded.notes, "rebalance — München ✓")


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_str_signing_key_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            RegulatoryAuditTrailEngine(signing_key="hunter2hunter2hunter2hunter2hunter2")

    def test_short_signing_key_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            RegulatoryAuditTrailEngine(signing_key=b"tooshort")

    def test_empty_strategy_id_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            _manifest(self.engine, strategy_id="  ")

    def test_empty_git_sha_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            _manifest(self.engine, git_commit_sha="")

    def test_malformed_data_checksum_is_rejected(self):
        for bad in ({"f.csv": "abc"}, {"f.csv": "z" * 64}, {"f.csv": 12345}):
            with self.subTest(bad=bad):
                with self.assertRaises(AuditManifestError):
                    _manifest(self.engine, data_checksums=bad)

    def test_boolean_metric_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            _manifest(self.engine, metrics_summary={"sharpe_ratio": True})

    def test_missing_file_raises_audit_error(self):
        with self.assertRaises(AuditManifestError):
            self.engine.compute_file_checksum("does/not/exist.parquet")

    def test_duplicate_input_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f = os.path.join(tmpdir, "a.csv")
            with open(f, "w") as fh:
                fh.write("x")
            with self.assertRaises(AuditManifestError):
                self.engine.compute_data_checksums([f, f])

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(AuditManifestError):
            self.engine.load_manifest_from_json("{not json")

    def test_missing_fields_are_reported(self):
        with self.assertRaises(AuditManifestError) as ctx:
            self.engine.load_manifest_from_json(json.dumps({"strategy_id": "s"}))
        self.assertIn("missing required field", str(ctx.exception))

    def test_non_integer_timestamp_is_rejected(self):
        m = _manifest(self.engine)
        data = json.loads(self.engine.export_manifest_json(m))
        data["timestamp_ns"] = 1.5
        with self.assertRaises(AuditManifestError):
            self.engine.load_manifest_from_json(json.dumps(data))


class TestChecksumsAndRoundTrip(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_multiple_data_files_checksum(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "data1.csv")
            file2 = os.path.join(tmpdir, "data2.parquet")
            with open(file1, "w") as f:
                f.write("date,open,high,low,close,volume\n2024-01-01,100,101,99,100.5,1000\n")
            with open(file2, "wb") as f:
                f.write(b"PARQUET_DUMMY_DATA")

            checksums = self.engine.compute_data_checksums([file1, file2])
            self.assertEqual(len(checksums), 2)
            self.assertEqual(len(checksums[file1]), 64)

            m = _manifest(self.engine, data_checksums=checksums)
            self.assertTrue(self.engine.verify_manifest(m))

    def test_checksum_matches_independent_hashlib_computation(self):
        # Independently derived expected value, not a repeat of the implementation.
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "d.bin")
            content = b"abc" * 100_000  # exceeds one chunk, exercises streaming
            with open(path, "wb") as f:
                f.write(content)
            self.assertEqual(
                self.engine.compute_file_checksum(path),
                hashlib.sha256(content).hexdigest(),
            )

    def test_empty_file_checksum_is_the_known_sha256_of_empty_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "empty.csv")
            open(path, "wb").close()
            self.assertEqual(
                self.engine.compute_file_checksum(path),
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )

    def test_export_and_load_round_trip(self):
        m = _manifest(self.engine)
        loaded = self.engine.load_manifest_from_json(self.engine.export_manifest_json(m))
        self.assertEqual(loaded.strategy_id, m.strategy_id)
        self.assertEqual(loaded.git_commit_sha, m.git_commit_sha)
        self.assertEqual(loaded.data_checksums_sha256, m.data_checksums_sha256)
        self.assertEqual(loaded.content_digest_sha256, m.content_digest_sha256)
        self.assertEqual(loaded.manifest_hmac_sha256, m.manifest_hmac_sha256)
        self.assertEqual(loaded.execution_timestamp_ns, m.execution_timestamp_ns)
        self.assertTrue(self.engine.verify_manifest(loaded))


if __name__ == "__main__":
    unittest.main()
