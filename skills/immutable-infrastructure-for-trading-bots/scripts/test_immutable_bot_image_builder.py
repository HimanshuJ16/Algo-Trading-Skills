import unittest
from immutable_bot_image_builder import (
    ImmutableInfrastructureAuditEngine, ImmutableContainerSpec, ImmutableInfrastructureReport
)

class TestImmutableInfrastructureAuditEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ImmutableInfrastructureAuditEngine()
        self.valid_git_sha = "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"
        self.valid_sha256 = "sha256:11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"

    def test_immutable_spec_approval(self):
        spec = ImmutableContainerSpec(
            image_name="trading-bot-execution",
            image_tag="v1.4.2",
            image_sha256_digest=self.valid_sha256,
            git_commit_sha=self.valid_git_sha,
            read_only_rootfs=True,
            is_image_signed_cosign=True,
            tmpfs_mounts=["/tmp", "/run"]
        )
        report = self.engine.audit_container_spec(spec)

        self.assertEqual(report.status, "IMMUTABLE_SPEC_APPROVED")
        self.assertTrue(report.is_read_only_rootfs_enforced)
        self.assertTrue(report.is_cosign_signature_verified)
        self.assertTrue(report.is_tmpfs_isolated)

    def test_mutable_rootfs_rejection(self):
        # read_only_rootfs = False -> REJECTED!
        spec = ImmutableContainerSpec(
            image_name="trading-bot-execution",
            image_tag="v1.4.2",
            image_sha256_digest=self.valid_sha256,
            git_commit_sha=self.valid_git_sha,
            read_only_rootfs=False,
            is_image_signed_cosign=True,
            tmpfs_mounts=["/tmp"]
        )
        report = self.engine.audit_container_spec(spec)

        self.assertEqual(report.status, "MUTABLE_ROOTFS_REJECTED")
        self.assertFalse(report.is_read_only_rootfs_enforced)

    def test_unsigned_image_rejection(self):
        spec = ImmutableContainerSpec(
            image_name="trading-bot-execution",
            image_tag="v1.4.2",
            image_sha256_digest=self.valid_sha256,
            git_commit_sha=self.valid_git_sha,
            read_only_rootfs=True,
            is_image_signed_cosign=False,
            tmpfs_mounts=["/tmp"]
        )
        report = self.engine.audit_container_spec(spec)

        self.assertEqual(report.status, "UNSIGNED_IMAGE_REJECTED")
        self.assertFalse(report.is_cosign_signature_verified)

if __name__ == '__main__':
    unittest.main()
