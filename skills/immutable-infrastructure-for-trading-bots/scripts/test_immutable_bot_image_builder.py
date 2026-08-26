import unittest

from immutable_bot_image_builder import (
    ImmutableContainerSpec,
    ImmutableInfrastructureAuditEngine,
    ImmutableSpecError,
    STATUS_APPROVED,
)

VALID_GIT_SHA = "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"
VALID_SHA256 = (
    "sha256:11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
)
# 64 hex characters: a Git object id under the SHA-256 object format.
VALID_GIT_SHA256 = (
    "9f2b8c1d4e6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e"
)


def make_spec(**overrides):
    """A fully compliant spec, with named fields overridden per test."""
    kwargs = dict(
        image_name="trading-bot-execution",
        image_tag="v1.4.2",
        image_sha256_digest=VALID_SHA256,
        git_commit_sha=VALID_GIT_SHA,
        read_only_rootfs=True,
        is_image_signed_cosign=True,
        tmpfs_mounts=["/tmp", "/run"],
        tmpfs_options={
            "/tmp": ["rw", "noexec", "nosuid", "size=64m"],
            "/run": ["rw", "noexec", "nosuid", "size=16m"],
        },
        source_revision_annotation=VALID_GIT_SHA,
    )
    kwargs.update(overrides)
    return ImmutableContainerSpec(**kwargs)


class TestSpecValidation(unittest.TestCase):
    """Structurally unusable specs must raise, never silently audit as clean."""

    def test_non_bool_read_only_rootfs_raises(self):
        # Regression: YAML `read_only_rootfs: "false"` is a truthy str. Accepting
        # it would report a mutable container as read-only.
        with self.assertRaises(ImmutableSpecError):
            make_spec(read_only_rootfs="false")

    def test_non_bool_signature_flag_raises(self):
        with self.assertRaises(ImmutableSpecError):
            make_spec(is_image_signed_cosign="yes")

    def test_none_git_sha_raises_typed_error(self):
        # Regression: previously raised AttributeError from .strip() deep inside
        # the audit, which a deploy gate could not classify.
        with self.assertRaises(ImmutableSpecError):
            make_spec(git_commit_sha=None)

    def test_empty_image_name_raises(self):
        with self.assertRaises(ImmutableSpecError):
            make_spec(image_name="   ")

    def test_string_tmpfs_mounts_raises_rather_than_iterating_chars(self):
        with self.assertRaises(ImmutableSpecError):
            make_spec(tmpfs_mounts="/tmp")

    def test_non_spec_argument_raises(self):
        engine = ImmutableInfrastructureAuditEngine()
        with self.assertRaises(ImmutableSpecError):
            engine.audit_container_spec({"read_only_rootfs": True})


class TestFormatValidators(unittest.TestCase):

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()

    def test_git_sha_accepts_sha1_and_sha256_object_ids(self):
        self.assertTrue(self.engine.validate_git_sha(VALID_GIT_SHA))
        self.assertTrue(self.engine.validate_git_sha(VALID_GIT_SHA256))

    def test_git_sha_rejects_abbreviated_and_overlong(self):
        self.assertFalse(self.engine.validate_git_sha(VALID_GIT_SHA[:7]))
        self.assertFalse(self.engine.validate_git_sha(VALID_GIT_SHA + "ab"))
        self.assertFalse(self.engine.validate_git_sha("z" * 40))
        self.assertFalse(self.engine.validate_git_sha(""))

    def test_git_sha_rejects_embedded_newline(self):
        # re.match with '$' would accept a trailing newline; fullmatch does not,
        # and an injected newline must never pass a provenance check.
        self.assertFalse(self.engine.validate_git_sha(VALID_GIT_SHA + "\nrm -rf /"))

    def test_digest_requires_algorithm_prefix_and_lowercase_hex(self):
        self.assertTrue(self.engine.validate_image_digest(VALID_SHA256))
        # OCI: the sha256 encoded portion MUST match /[a-f0-9]{64}/.
        self.assertFalse(self.engine.validate_image_digest(VALID_SHA256.upper()))
        self.assertFalse(self.engine.validate_image_digest(VALID_SHA256[7:]))
        self.assertFalse(self.engine.validate_image_digest("sha256:" + "a" * 63))
        self.assertFalse(self.engine.validate_image_digest("latest"))
        self.assertFalse(self.engine.validate_image_digest(""))

    def test_digest_accepts_sha512(self):
        self.assertTrue(
            self.engine.validate_image_digest("sha512:" + "0" * 128))


class TestApproval(unittest.TestCase):

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()

    def test_fully_compliant_spec_is_approved_with_no_warnings(self):
        report = self.engine.audit_container_spec(make_spec())

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.approved)
        self.assertEqual(report.violations, [])
        self.assertEqual(report.warnings, [])
        self.assertTrue(report.is_digest_pinned)
        self.assertTrue(report.is_read_only_rootfs_enforced)
        self.assertTrue(report.is_signature_attested)
        self.assertTrue(report.is_source_revision_bound)
        self.assertTrue(report.is_tmpfs_isolated)
        self.assertTrue(report.is_security_hardened)

    def test_image_uri_is_digest_qualified(self):
        report = self.engine.audit_container_spec(make_spec())
        self.assertEqual(
            report.image_uri,
            f"trading-bot-execution:v1.4.2@{VALID_SHA256}",
        )


class TestIndividualViolations(unittest.TestCase):

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()

    def assert_single_violation(self, spec, code):
        report = self.engine.audit_container_spec(spec)
        self.assertEqual(report.violation_codes, [code])
        self.assertEqual(report.status, code)
        self.assertFalse(report.approved)
        return report

    def test_mutable_rootfs_rejection(self):
        report = self.assert_single_violation(
            make_spec(read_only_rootfs=False), "MUTABLE_ROOTFS_REJECTED")
        self.assertFalse(report.is_read_only_rootfs_enforced)

    def test_unsigned_image_rejection(self):
        report = self.assert_single_violation(
            make_spec(is_image_signed_cosign=False), "UNSIGNED_IMAGE_REJECTED")
        self.assertFalse(report.is_signature_attested)

    def test_invalid_git_sha_rejection(self):
        report = self.assert_single_violation(
            make_spec(git_commit_sha="HEAD~1", source_revision_annotation=None),
            "INVALID_GIT_SHA_REJECTED")
        self.assertFalse(report.is_source_revision_bound)

    def test_unpinned_digest_rejection(self):
        # Regression: an unvalidated digest previously produced an approved spec
        # whose image_uri ended in a bare '@'.
        report = self.assert_single_violation(
            make_spec(image_sha256_digest="sha256:not-a-digest"),
            "UNPINNED_DIGEST_REJECTED")
        self.assertFalse(report.is_digest_pinned)

    def test_floating_tag_rejection(self):
        self.assert_single_violation(
            make_spec(image_tag="latest"), "MUTABLE_TAG_REJECTED")

    def test_floating_tag_check_is_case_insensitive(self):
        self.assert_single_violation(
            make_spec(image_tag="LATEST"), "MUTABLE_TAG_REJECTED")

    def test_semver_tag_is_not_treated_as_floating(self):
        # Boundary: 'stable' floats, 'v2.0.0-stable' does not.
        report = self.engine.audit_container_spec(make_spec(image_tag="v2.0.0-stable"))
        self.assertNotIn("MUTABLE_TAG_REJECTED", report.violation_codes)

    def test_writable_host_mount_rejection(self):
        # The hot-patching hole a read-only rootfs alone does not close.
        report = self.assert_single_violation(
            make_spec(writable_volumes=["/app"]), "WRITABLE_HOST_MOUNT_REJECTED")
        self.assertTrue(report.is_read_only_rootfs_enforced)

    def test_source_revision_mismatch_rejection(self):
        other_sha = "0" * 39 + "1"
        self.assert_single_violation(
            make_spec(source_revision_annotation=other_sha),
            "SOURCE_REVISION_MISMATCH_REJECTED")

    def test_source_revision_comparison_is_case_insensitive(self):
        report = self.engine.audit_container_spec(
            make_spec(source_revision_annotation=VALID_GIT_SHA.upper()))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_source_revision_bound)

    def test_root_user_rejection(self):
        report = self.assert_single_violation(
            make_spec(run_as_non_root_user=False), "PRIVILEGE_HARDENING_REJECTED")
        self.assertFalse(report.is_security_hardened)

    def test_privilege_escalation_rejection(self):
        self.assert_single_violation(
            make_spec(no_new_privileges=False), "PRIVILEGE_HARDENING_REJECTED")

    def test_both_hardening_failures_produce_one_violation_naming_both(self):
        report = self.assert_single_violation(
            make_spec(no_new_privileges=False, run_as_non_root_user=False),
            "PRIVILEGE_HARDENING_REJECTED")
        detail = report.violations[0].detail
        self.assertIn("no_new_privileges", detail)
        self.assertIn("run_as_non_root_user", detail)

    def test_executable_tmpfs_rejection(self):
        report = self.assert_single_violation(
            make_spec(
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmp": ["rw", "size=64m"]},
            ),
            "EXECUTABLE_TMPFS_REJECTED")
        self.assertIn("noexec", report.violations[0].remediation)

    def test_tmpfs_size_option_does_not_mask_missing_noexec(self):
        # Options are compared by name, so 'size=64m' must not be read as a flag.
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmp": ["rw", "nosuid", "size=64m"]},
            ))
        self.assertEqual(report.violation_codes, ["EXECUTABLE_TMPFS_REJECTED"])

    def test_tmpfs_options_are_matched_case_insensitively(self):
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmp": ["RW", "NOEXEC", "NOSUID"]},
            ))
        self.assertEqual(report.status, STATUS_APPROVED)


class TestWarningsVersusViolations(unittest.TestCase):
    """A warning must never block a deployment, and never silently vanish."""

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()

    def test_missing_tmp_mount_warns_but_approves(self):
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/run"],
                tmpfs_options={"/run": ["rw", "noexec", "nosuid"]},
            ))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.is_tmpfs_isolated)
        self.assertTrue(any("/tmp" in w for w in report.warnings))

    def test_unrelated_tmpfs_path_does_not_satisfy_the_tmp_requirement(self):
        # Regression: the old check was `"/tmp" in mounts or len(mounts) > 0`,
        # whose second clause made the first unreachable, so any non-empty list
        # reported /tmp as isolated.
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/var/cache"],
                tmpfs_options={"/var/cache": ["rw", "noexec", "nosuid"]},
            ))
        self.assertFalse(report.is_tmpfs_isolated)

    def test_empty_tmpfs_list_warns_but_approves(self):
        report = self.engine.audit_container_spec(
            make_spec(tmpfs_mounts=[], tmpfs_options={}))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.is_tmpfs_isolated)

    def test_undeclared_tmpfs_options_warn_rather_than_reject(self):
        report = self.engine.audit_container_spec(
            make_spec(tmpfs_mounts=["/tmp"], tmpfs_options={}))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(any("no declared options" in w for w in report.warnings))

    def test_missing_source_revision_annotation_warns_by_default(self):
        report = self.engine.audit_container_spec(
            make_spec(source_revision_annotation=None))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.is_source_revision_bound)
        self.assertTrue(any("revision" in w for w in report.warnings))

    def test_missing_source_revision_annotation_rejects_when_required(self):
        strict = ImmutableInfrastructureAuditEngine(
            require_source_revision_annotation=True)
        report = strict.audit_container_spec(
            make_spec(source_revision_annotation=None))
        self.assertEqual(report.violation_codes, ["SOURCE_REVISION_MISSING_REJECTED"])


class TestAdversarialEdgeCases(unittest.TestCase):

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()

    def test_abbreviated_annotation_is_rejected_with_a_targeted_remediation(self):
        # `git rev-parse --short HEAD` is a plausible builder mistake; it must not
        # pass, and the fix differs from "you built the wrong commit".
        report = self.engine.audit_container_spec(
            make_spec(source_revision_annotation=VALID_GIT_SHA[:7]))
        self.assertEqual(
            report.violation_codes, ["SOURCE_REVISION_MISMATCH_REJECTED"])
        self.assertIn("abbreviated", report.violations[0].remediation)

    def test_genuinely_different_commit_gets_the_rebuild_remediation(self):
        report = self.engine.audit_container_spec(
            make_spec(source_revision_annotation="f" * 40))
        self.assertNotIn("abbreviated", report.violations[0].remediation)
        self.assertIn("Rebuild", report.violations[0].remediation)

    def test_orphaned_tmpfs_options_key_is_surfaced(self):
        # Options declared for '/tmpp' harden nothing, and '/tmp' silently falls
        # back to the undeclared-options path.
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmpp": ["rw", "noexec", "nosuid"]},
            ))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(any("/tmpp" in w for w in report.warnings))

    def test_duplicate_tmpfs_path_reports_each_finding_once(self):
        report = self.engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/tmp", "/tmp"],
                tmpfs_options={"/tmp": ["rw"]},
            ))
        self.assertEqual(report.violation_codes, ["EXECUTABLE_TMPFS_REJECTED"])

    def test_whitespace_padded_floating_tag_is_still_caught(self):
        report = self.engine.audit_container_spec(make_spec(image_tag="  latest  "))
        self.assertIn("MUTABLE_TAG_REJECTED", report.violation_codes)

    def test_empty_required_options_disables_both_the_check_and_its_warning(self):
        engine = ImmutableInfrastructureAuditEngine(required_tmpfs_options=())
        report = engine.audit_container_spec(
            make_spec(tmpfs_mounts=["/tmp"], tmpfs_options={}))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.warnings, [])


class TestAggregateReporting(unittest.TestCase):
    """One audit must surface every breach, not one rebuild cycle at a time."""

    def test_mutable_and_unsigned_spec_reports_both_codes(self):
        # The behaviour SKILL.md's Verification section describes. The previous
        # implementation returned on the first failure, so only one code existed.
        engine = ImmutableInfrastructureAuditEngine()
        report = engine.audit_container_spec(
            make_spec(read_only_rootfs=False, is_image_signed_cosign=False))

        self.assertIn("MUTABLE_ROOTFS_REJECTED", report.violation_codes)
        self.assertIn("UNSIGNED_IMAGE_REJECTED", report.violation_codes)
        self.assertEqual(report.status, "MUTABLE_ROOTFS_REJECTED")

    def test_worst_case_spec_reports_every_control(self):
        engine = ImmutableInfrastructureAuditEngine()
        report = engine.audit_container_spec(
            make_spec(
                image_tag="latest",
                image_sha256_digest="sha256:deadbeef",
                git_commit_sha="not-a-sha",
                read_only_rootfs=False,
                is_image_signed_cosign=False,
                no_new_privileges=False,
                run_as_non_root_user=False,
                writable_volumes=["/app", "/etc/bot"],
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmp": ["rw"]},
                source_revision_annotation=None,
            ))

        self.assertEqual(
            set(report.violation_codes),
            {
                "INVALID_GIT_SHA_REJECTED",
                "UNPINNED_DIGEST_REJECTED",
                "MUTABLE_TAG_REJECTED",
                "MUTABLE_ROOTFS_REJECTED",
                "WRITABLE_HOST_MOUNT_REJECTED",
                "UNSIGNED_IMAGE_REJECTED",
                "PRIVILEGE_HARDENING_REJECTED",
                "EXECUTABLE_TMPFS_REJECTED",
            },
        )
        self.assertFalse(report.approved)

    def test_status_reports_the_most_severe_violation_not_the_first_evaluated(self):
        # An invalid git sha (HIGH) is evaluated before the writable host mount
        # (CRITICAL); an alert keyed on status must surface the CRITICAL one.
        engine = ImmutableInfrastructureAuditEngine()
        report = engine.audit_container_spec(
            make_spec(git_commit_sha="HEAD", source_revision_annotation=None,
                      writable_volumes=["/app"]))

        self.assertEqual(report.status, "WRITABLE_HOST_MOUNT_REJECTED")
        self.assertEqual(report.violations[0].severity, "CRITICAL")
        self.assertIn("INVALID_GIT_SHA_REJECTED", report.violation_codes)

    def test_equal_severity_violations_keep_evaluation_order(self):
        engine = ImmutableInfrastructureAuditEngine()
        report = engine.audit_container_spec(
            make_spec(read_only_rootfs=False, is_image_signed_cosign=False))
        self.assertEqual(
            report.violation_codes,
            ["MUTABLE_ROOTFS_REJECTED", "UNSIGNED_IMAGE_REJECTED"])

    def test_status_is_deterministic_across_repeated_audits(self):
        engine = ImmutableInfrastructureAuditEngine()
        spec_kwargs = dict(read_only_rootfs=False, is_image_signed_cosign=False)
        statuses = {
            engine.audit_container_spec(make_spec(**spec_kwargs)).status
            for _ in range(5)
        }
        self.assertEqual(statuses, {"MUTABLE_ROOTFS_REJECTED"})

    def test_every_violation_carries_severity_and_remediation(self):
        engine = ImmutableInfrastructureAuditEngine()
        report = engine.audit_container_spec(
            make_spec(read_only_rootfs=False, writable_volumes=["/app"]))
        for violation in report.violations:
            self.assertIn(violation.severity, ("CRITICAL", "HIGH"))
            self.assertTrue(violation.remediation.strip())


class TestEnginePolicyOverrides(unittest.TestCase):

    def test_custom_required_tmpfs_paths(self):
        engine = ImmutableInfrastructureAuditEngine(
            required_tmpfs_paths=("/tmp", "/run"))
        report = engine.audit_container_spec(
            make_spec(
                tmpfs_mounts=["/tmp"],
                tmpfs_options={"/tmp": ["rw", "noexec", "nosuid"]},
            ))
        self.assertFalse(report.is_tmpfs_isolated)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_custom_mutable_tag_list_replaces_the_default(self):
        engine = ImmutableInfrastructureAuditEngine(mutable_tag_names=("rolling",))
        self.assertNotIn(
            "MUTABLE_TAG_REJECTED",
            engine.audit_container_spec(make_spec(image_tag="latest")).violation_codes)
        self.assertIn(
            "MUTABLE_TAG_REJECTED",
            engine.audit_container_spec(make_spec(image_tag="rolling")).violation_codes)


if __name__ == "__main__":
    unittest.main()
