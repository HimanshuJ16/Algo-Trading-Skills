"""
Unit tests for secrets-rotation-without-bot-downtime skill.
"""
import unittest
from secrets_rotator import RotationState, SecretsRotator


class TestSecretsRotator(unittest.TestCase):

    def test_successful_rotation(self):
        """New valid credentials should be swapped in without downtime."""
        rotator = SecretsRotator(validate_fn=lambda cred: True)
        rotator.set_initial_credential("key_v1", "secret_v1")

        result = rotator.rotate("key_v2", "secret_v2")

        self.assertTrue(result.success)
        self.assertEqual(result.active_key_id, "key_v2")
        self.assertEqual(result.state, RotationState.SWAPPED)
        self.assertIsNotNone(rotator.previous_credential)

    def test_failed_validation_keeps_old_credential(self):
        """Failed validation should keep old credentials active."""
        rotator = SecretsRotator(validate_fn=lambda cred: False)
        rotator.set_initial_credential("key_v1", "secret_v1")

        result = rotator.rotate("key_bad", "secret_bad")

        self.assertFalse(result.success)
        self.assertEqual(result.active_key_id, "key_v1")
        self.assertEqual(result.state, RotationState.FAILED_ROLLBACK)

    def test_fallback_to_previous(self):
        """Emergency fallback should restore previous credential."""
        rotator = SecretsRotator(validate_fn=lambda cred: True)
        rotator.set_initial_credential("key_v1", "secret_v1")
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.fallback_to_previous()

        self.assertTrue(result.success)
        self.assertEqual(result.active_key_id, "key_v1")
        self.assertEqual(result.state, RotationState.FAILED_ROLLBACK)

    def test_revoke_previous(self):
        """After confirmed rotation, previous credential should be revokable."""
        rotator = SecretsRotator(validate_fn=lambda cred: True)
        rotator.set_initial_credential("key_v1", "secret_v1")
        rotator.rotate("key_v2", "secret_v2")

        result = rotator.revoke_previous()

        self.assertTrue(result.success)
        self.assertEqual(result.state, RotationState.REVOKED_OLD)
        self.assertIsNone(rotator.previous_credential)


if __name__ == "__main__":
    unittest.main()
