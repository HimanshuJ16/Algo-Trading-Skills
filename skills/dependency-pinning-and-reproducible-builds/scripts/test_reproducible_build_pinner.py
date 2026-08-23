import hashlib
import unittest

from reproducible_build_pinner import (
    ReproducibleBuildPinnerEngine, ALLOWED_HASH_ALGORITHMS
)

# Real 64-hex-character SHA-256 digests, computed rather than invented, so the fixtures
# exercise the validator instead of relying on a string that merely looks like a hash.
H1 = hashlib.sha256(b"numpy-1.26.4-wheel").hexdigest()
H2 = hashlib.sha256(b"numpy-1.26.4-sdist").hexdigest()
H3 = hashlib.sha256(b"pandas-2.2.1-wheel").hexdigest()

# The SHA-256 of an empty file. The previous implementation emitted this constant as a
# fabricated hash for any package it could not resolve.
EMPTY_FILE_SHA256 = hashlib.sha256(b"").hexdigest()


class TestHashValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_real_sha256_is_accepted(self):
        report = self.engine.audit_requirements_file([f"numpy==1.26.4 --hash=sha256:{H1}"])
        self.assertEqual(report.missing_hashes_count, 0)
        self.assertTrue(report.all_requirements_pinned_and_hashed)
        self.assertEqual(report.reproducibility_score, 100.0)

    def test_weak_algorithms_are_rejected(self):
        # Regression: pip excludes md5/sha1/sha224 from hash-checking mode, but these
        # previously counted as satisfying the SHA-256 requirement and scored 100.0.
        for algo in ("md5", "sha1", "sha224"):
            with self.subTest(algo=algo):
                digest = hashlib.new(algo, b"x").hexdigest()
                report = self.engine.audit_requirements_file(
                    [f"numpy==1.26.4 --hash={algo}:{digest}"]
                )
                self.assertEqual(report.missing_hashes_count, 1)
                self.assertFalse(report.all_requirements_pinned_and_hashed)
                self.assertEqual(report.packages_with_invalid_hashes, ["numpy"])
                self.assertTrue(any("too weak" in w for w in report.warnings))

    def test_malformed_digests_are_rejected(self):
        # Regression: all three previously yielded score 100.0 and a "guaranteed" verdict.
        for bad in ("sha256:nothex", "sha256:abcd1234", "sha256:" + "z" * 64):
            with self.subTest(bad=bad):
                report = self.engine.audit_requirements_file([f"numpy==1.26.4 --hash={bad}"])
                self.assertEqual(report.missing_hashes_count, 1)
                self.assertFalse(report.all_requirements_pinned_and_hashed)

    def test_wrong_length_digest_for_algorithm_is_rejected(self):
        # A valid sha256 digest is too short to be a valid sha512 digest.
        report = self.engine.audit_requirements_file([f"numpy==1.26.4 --hash=sha512:{H1}"])
        self.assertEqual(report.missing_hashes_count, 1)

    def test_stronger_algorithms_are_accepted(self):
        for algo in ("sha384", "sha512"):
            with self.subTest(algo=algo):
                digest = hashlib.new(algo, b"x").hexdigest()
                self.assertEqual(len(digest), ALLOWED_HASH_ALGORITHMS[algo])
                report = self.engine.audit_requirements_file(
                    [f"numpy==1.26.4 --hash={algo}:{digest}"]
                )
                self.assertEqual(report.missing_hashes_count, 0)


class TestPinDetection(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_prefix_match_is_not_an_exact_pin(self):
        # Regression: '==2.2.*' is PEP 440 prefix matching and accepts any 2.2.x release,
        # but was previously reported as an exact pin -- the exact defect this tool exists
        # to catch.
        report = self.engine.audit_requirements_file([f"pandas==2.2.* --hash=sha256:{H3}"])
        self.assertEqual(report.unpinned_packages, ["pandas"])
        self.assertEqual(report.pinned_packages_count, 0)
        self.assertFalse(report.all_requirements_pinned_and_hashed)
        self.assertTrue(any("prefix matching" in w for w in report.warnings))

    def test_exact_pin_is_detected(self):
        report = self.engine.audit_requirements_file(["pandas==2.2.1"])
        self.assertEqual(report.pinned_packages_count, 1)
        self.assertEqual(report.requirements[0].pinned_version, "2.2.1")

    def test_floating_operators_are_unpinned(self):
        for spec in ("pandas>=2.0.0", "pandas", "pandas~=2.2.0", "pandas<3.0", "pandas!=2.0.0"):
            with self.subTest(spec=spec):
                report = self.engine.audit_requirements_file([spec])
                self.assertEqual(report.unpinned_packages, ["pandas"], spec)

    def test_environment_marker_does_not_corrupt_version(self):
        # Regression: previously parsed the version as '2.2.1; python_version >= "3.9"'.
        report = self.engine.audit_requirements_file(['pandas==2.2.1; python_version >= "3.9"'])
        self.assertEqual(report.requirements[0].pinned_version, "2.2.1")
        self.assertTrue(report.requirements[0].is_pinned_exact)

    def test_extras_are_stripped_from_package_name(self):
        # Regression: previously reported the name as 'requests[security]'.
        report = self.engine.audit_requirements_file(["requests[security]==2.31.0"])
        self.assertEqual(report.requirements[0].package_name, "requests")
        self.assertEqual(report.requirements[0].pinned_version, "2.31.0")

    def test_arbitrary_equality_parses_version_and_warns(self):
        # Regression: '===' previously produced the version '=2.2.1'.
        report = self.engine.audit_requirements_file(["pandas===2.2.1"])
        self.assertEqual(report.requirements[0].pinned_version, "2.2.1")
        self.assertTrue(report.requirements[0].is_pinned_exact)
        self.assertTrue(any("discouraged" in w for w in report.warnings))

    def test_direct_url_reference_counts_as_pinned_with_warning(self):
        report = self.engine.audit_requirements_file(
            ["mypkg @ https://example.invalid/mypkg-1.0-py3-none-any.whl"]
        )
        self.assertEqual(report.pinned_packages_count, 1)
        self.assertTrue(any("immutable" in w for w in report.warnings))


class TestRealWorldLockfile(unittest.TestCase):
    """The exact multi-line shape emitted by `pip-compile --generate-hashes`."""

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()
        self.lines = [
            "#",
            "# This file is autogenerated by pip-compile with Python 3.11",
            "#",
            "--index-url https://pypi.org/simple",
            "",
            "numpy==1.26.4 \\",
            f"    --hash=sha256:{H1} \\",
            f"    --hash=sha256:{H2}",
            "pandas==2.2.1 \\",
            f"    --hash=sha256:{H3}",
        ]

    def test_continuation_lines_are_joined_into_one_requirement(self):
        # Regression: the physical --hash lines were previously counted as separate
        # nameless packages, so this correctly locked file audited as 3 packages /
        # 2 unpinned / score 40.0 instead of 2 packages / 0 unpinned / score 100.0.
        report = self.engine.audit_requirements_file(self.lines)
        self.assertEqual(report.total_packages_audited, 2)
        self.assertEqual(report.unpinned_packages, [])
        self.assertEqual(report.missing_hashes_count, 0)
        self.assertEqual(report.reproducibility_score, 100.0)
        self.assertTrue(report.all_requirements_pinned_and_hashed)

    def test_multiple_hashes_are_all_retained(self):
        report = self.engine.audit_requirements_file(self.lines)
        numpy = next(r for r in report.requirements if r.package_name == "numpy")
        self.assertEqual(numpy.all_hashes, [f"sha256:{H1}", f"sha256:{H2}"])

    def test_index_url_directive_is_not_counted_as_a_package(self):
        report = self.engine.audit_requirements_file(self.lines)
        self.assertIn("--index-url https://pypi.org/simple", report.skipped_directive_lines)
        self.assertNotIn("--index-url", report.unpinned_packages)


class TestControlDirectives(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_pip_directives_are_skipped_not_scored(self):
        # Regression: each of these was previously counted as an unpinned package,
        # driving a fully compliant file's score to 0.0.
        directives = ["-r base.txt", "-c constraints.txt", "-e .",
                      "--index-url https://pypi.org/simple", "--require-hashes"]
        report = self.engine.audit_requirements_file(
            directives + [f"numpy==1.26.4 --hash=sha256:{H1}"]
        )
        self.assertEqual(report.total_packages_audited, 1)
        self.assertEqual(report.unpinned_packages, [])
        self.assertEqual(len(report.skipped_directive_lines), len(directives))
        self.assertEqual(report.reproducibility_score, 100.0)

    def test_orphan_hash_line_is_not_counted_as_a_package(self):
        # A continuation whose requirement line is missing must be reported as malformed,
        # not scored as a nameless unpinned package.
        report = self.engine.audit_requirements_file([f"--hash=sha256:{H1}"])
        self.assertEqual(report.total_packages_audited, 0)
        self.assertEqual(report.unpinned_packages, [])
        self.assertTrue(any("malformed line" in w for w in report.warnings))

    def test_comments_and_blank_lines_ignored(self):
        report = self.engine.audit_requirements_file(
            ["# a comment", "", "   ", f"numpy==1.26.4 --hash=sha256:{H1}  # inline note"]
        )
        self.assertEqual(report.total_packages_audited, 1)
        self.assertEqual(report.missing_hashes_count, 0)


class TestScoring(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_score_is_proportional_not_saturating(self):
        # Regression: absolute penalties drove 4-unpinned and 400-unpinned 500-package
        # files both to 0.0, making the score useless at realistic lockfile sizes.
        def score_for(n_unpinned: int) -> float:
            reqs = [f"unpinned{i}" for i in range(n_unpinned)]
            reqs += [f"ok{i}==1.0.0 --hash=sha256:{H1}" for i in range(500 - n_unpinned)]
            return self.engine.audit_requirements_file(reqs).reproducibility_score

        s4, s50, s400 = score_for(4), score_for(50), score_for(400)
        # 4 unpinned of 500: pinned 496/500, hashed 496/500 -> 0.5*99.2 + 0.5*99.2 = 99.2
        self.assertEqual(s4, 99.2)
        # 50 of 500 -> 450/500 = 90%
        self.assertEqual(s50, 90.0)
        # 400 of 500 -> 100/500 = 20%
        self.assertEqual(s400, 20.0)
        self.assertGreater(s4, s50)
        self.assertGreater(s50, s400)

    def test_half_pinned_none_hashed_scores_by_weights(self):
        # 2 packages, 1 pinned, 0 validly hashed -> 0.5*(1/2) + 0.5*(0/2) = 0.25 -> 25.0
        report = self.engine.audit_requirements_file(["a==1.0.0", "b>=2.0"])
        self.assertEqual(report.pinned_packages_count, 1)
        self.assertEqual(report.missing_hashes_count, 2)
        self.assertEqual(report.reproducibility_score, 25.0)

    def test_empty_input_scores_100_and_is_vacuously_compliant(self):
        report = self.engine.audit_requirements_file([])
        self.assertEqual(report.total_packages_audited, 0)
        self.assertEqual(report.reproducibility_score, 100.0)
        self.assertTrue(report.all_requirements_pinned_and_hashed)

    def test_custom_weights_shift_the_score(self):
        engine = ReproducibleBuildPinnerEngine(pin_weight=1.0, hash_weight=0.0)
        # Pinned but unhashed scores full marks when hashes carry no weight.
        report = engine.audit_requirements_file(["a==1.0.0"])
        self.assertEqual(report.reproducibility_score, 100.0)
        # ...yet the strict boolean still reports the missing hash.
        self.assertFalse(report.all_requirements_pinned_and_hashed)


class TestLockfileGeneration(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_no_hash_is_ever_fabricated(self):
        # Regression: the previous implementation emitted
        # 'pkg==1.0.0 --hash=sha256:<sha256 of an empty file>' for unresolved packages.
        report = self.engine.audit_requirements_file(["pandas>=2.0", "numpy", "scipy==1.12.0"])
        rendered = "\n".join(report.generated_lockfile_lines)
        self.assertNotIn(EMPTY_FILE_SHA256, rendered)
        self.assertNotIn("==1.0.0", rendered)

    def test_deficient_requirements_are_commented_todos(self):
        report = self.engine.audit_requirements_file(["pandas>=2.0", "scipy==1.12.0"])
        lines = report.generated_lockfile_lines
        self.assertTrue(any(l.startswith("# TODO(unpinned):") and "pandas" in l for l in lines))
        self.assertTrue(any(l.startswith("# TODO(missing-hash):") and "scipy" in l for l in lines))
        # No uncommented requirement line may be emitted for a deficient package.
        installable = [l for l in lines if not l.startswith("#")]
        self.assertEqual(installable, [])

    def test_compliant_requirements_pass_through_with_all_hashes(self):
        report = self.engine.audit_requirements_file(
            [f"numpy==1.26.4 --hash=sha256:{H1} --hash=sha256:{H2}"]
        )
        installable = [l for l in report.generated_lockfile_lines if not l.startswith("#")]
        self.assertEqual(installable, [f"numpy==1.26.4 --hash=sha256:{H1} --hash=sha256:{H2}"])


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleBuildPinnerEngine()

    def test_non_list_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_requirements_file("numpy==1.26.4")

    def test_none_and_non_string_entries_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_requirements_file([None])
        with self.assertRaises(ValueError):
            self.engine.audit_requirements_file([123])

    def test_invalid_engine_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            ReproducibleBuildPinnerEngine(target_python_version="  ")
        with self.assertRaises(ValueError):
            ReproducibleBuildPinnerEngine(pin_weight=0.7, hash_weight=0.7)
        with self.assertRaises(ValueError):
            ReproducibleBuildPinnerEngine(pin_weight=-0.1, hash_weight=1.1)


if __name__ == '__main__':
    unittest.main()
