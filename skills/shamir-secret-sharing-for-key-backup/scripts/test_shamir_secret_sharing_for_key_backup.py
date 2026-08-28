import logging
import unittest

from shamir_secret_sharing_for_key_backup import (
    LENGTH_TAG,
    MERSENNE_M127,
    MERSENNE_M521,
    MIN_THRESHOLD,
    PRIME_FIELD_MODULUS,
    SecretShare,
    ShamirSecretSharingError,
    ShamirSecretSharingForKeyBackupEngine,
)

#: Silence the incidental WARNING lines the engine emits (they are deliberate in
#: production). A NullHandler is used rather than ``logging.disable`` so the three
#: tests that assert on those warnings via ``assertLogs`` still see them.
logging.getLogger("shamir_secret_sharing_for_key_backup").addHandler(
    logging.NullHandler()
)

#: A real 32-byte secp256k1-range private key, not a decimal literal that merely
#: looks long. 0xC1... is 256 bits, which the old 127-bit field could not hold.
KEY_256_BIT = bytes.fromhex("c1" * 32)

#: Hand-computed polynomial f(x) = 7 + 3x + 5x^2 over M_127. Every y below was
#: derived by substitution, independently of the implementation's own arithmetic:
#: f(1)=7+3+5=15, f(2)=7+6+20=33, f(3)=7+9+45=61, f(4)=7+12+80=99.
KNOWN_POLY_SECRET = 7
KNOWN_POLY_POINTS = ((1, 15), (2, 33), (3, 61), (4, 99))


def known_shares(k=3, modulus=MERSENNE_M127, points=KNOWN_POLY_POINTS):
    return [
        SecretShare(index=x, value=y, threshold_k=k, modulus=modulus)
        for x, y in points
    ]


class TestFieldSelection(unittest.TestCase):
    """The field must actually hold the key material the skill claims to back up."""

    def test_default_field_is_m521(self):
        self.assertEqual(PRIME_FIELD_MODULUS, MERSENNE_M521)
        self.assertEqual(PRIME_FIELD_MODULUS.bit_length(), 521)

    def test_default_field_accepts_a_real_256_bit_key(self):
        engine = ShamirSecretSharingForKeyBackupEngine()
        secret = int.from_bytes(KEY_256_BIT, "big")
        self.assertEqual(secret.bit_length(), 256)
        result = engine.split_secret(secret, threshold_k=3, total_shares_n=5)
        self.assertEqual(
            engine.reconstruct_secret(result.shares[:4]), secret
        )

    def test_m127_field_rejects_a_256_bit_key_instead_of_truncating(self):
        engine = ShamirSecretSharingForKeyBackupEngine(modulus=MERSENNE_M127)
        secret = int.from_bytes(KEY_256_BIT, "big")
        with self.assertRaises(ShamirSecretSharingError):
            engine.split_secret(secret, threshold_k=3, total_shares_n=5)

    def test_composite_modulus_is_rejected_at_construction(self):
        # 2**127 is composite; under Fermat-based inversion it silently returned a
        # wrong secret from a correct share set.
        with self.assertRaises(ShamirSecretSharingError):
            ShamirSecretSharingForKeyBackupEngine(modulus=(1 << 127))

    def test_tiny_and_non_int_moduli_are_rejected(self):
        for bad in (0, 1, 4, -7):
            with self.assertRaises(ShamirSecretSharingError):
                ShamirSecretSharingForKeyBackupEngine(modulus=bad)
        with self.assertRaises(ShamirSecretSharingError):
            ShamirSecretSharingForKeyBackupEngine(modulus="M521")

    def test_max_secret_bytes_boundary(self):
        engine = ShamirSecretSharingForKeyBackupEngine()
        self.assertEqual(engine.max_secret_bytes, 64)
        engine.split_secret_bytes(b"\xff" * 64, threshold_k=2, total_shares_n=3)
        with self.assertRaises(ShamirSecretSharingError):
            engine.split_secret_bytes(b"\xff" * 65, threshold_k=2, total_shares_n=3)
        self.assertEqual(
            ShamirSecretSharingForKeyBackupEngine(modulus=MERSENNE_M127).max_secret_bytes,
            15,
        )


class TestInterpolationCorrectness(unittest.TestCase):
    """Lagrange reconstruction against independently derived expected values."""

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine(modulus=MERSENNE_M127)

    def test_reconstructs_hand_computed_polynomial_constant_term(self):
        for subset in ((0, 1, 2), (0, 2, 3), (1, 2, 3)):
            shares = [known_shares()[i] for i in subset]
            self.assertEqual(
                self.engine.reconstruct_secret(shares), KNOWN_POLY_SECRET
            )

    def test_interpolates_a_held_out_point_of_the_known_polynomial(self):
        # f(4) = 99 must fall out of the three points (1,15), (2,33), (3,61).
        self.assertEqual(
            self.engine._interpolate_at(known_shares()[:3], 4), 99
        )

    def test_share_values_match_the_dealt_polynomial(self):
        engine = ShamirSecretSharingForKeyBackupEngine()
        result = engine.split_secret(2**200 + 1, threshold_k=3, total_shares_n=6)
        # Any 3 shares must predict every other share's value.
        self.assertTrue(engine.verify_shares_consistent(result.shares))


class TestSplitAndReconstruct(unittest.TestCase):

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine()

    def test_split_and_reconstruct_at_exact_threshold(self):
        secret = 123456789012345678901234567890
        result = self.engine.split_secret(secret, threshold_k=3, total_shares_n=5)

        self.assertEqual(len(result.shares), 5)
        self.assertEqual([s.index for s in result.shares], [1, 2, 3, 4, 5])
        self.assertTrue(all(s.threshold_k == 3 for s in result.shares))
        self.assertTrue(all(s.modulus == MERSENNE_M521 for s in result.shares))

        subset = [result.shares[0], result.shares[2], result.shares[4]]
        self.assertEqual(self.engine.reconstruct_secret(subset), secret)

    def test_reconstruct_with_surplus_shares_matches(self):
        secret = 42
        result = self.engine.split_secret(secret, threshold_k=2, total_shares_n=5)
        self.assertEqual(self.engine.reconstruct_secret(result.shares), secret)

    def test_boundary_secrets_round_trip(self):
        for secret in (0, 1, self.engine.max_secret_int):
            result = self.engine.split_secret(secret, threshold_k=2, total_shares_n=3)
            self.assertEqual(self.engine.reconstruct_secret(result.shares[:2]), secret)

    def test_shares_alone_do_not_equal_the_secret(self):
        secret = 2**255 + 12345
        result = self.engine.split_secret(secret, threshold_k=3, total_shares_n=5)
        for share in result.shares:
            self.assertNotEqual(share.value, secret)

    def test_split_is_randomised_across_calls(self):
        secret = 999_999_937
        first = self.engine.split_secret(secret, threshold_k=3, total_shares_n=5)
        second = self.engine.split_secret(secret, threshold_k=3, total_shares_n=5)
        self.assertNotEqual(
            [s.value for s in first.shares], [s.value for s in second.shares]
        )


class TestByteOrientedApi(unittest.TestCase):
    """Key material is bytes; an int round trip must not eat leading zeros."""

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine()

    def test_private_key_round_trip(self):
        result = self.engine.split_secret_bytes(
            KEY_256_BIT, threshold_k=3, total_shares_n=5
        )
        self.assertEqual(result.secret_length_bytes, 32)
        recovered = self.engine.reconstruct_secret_bytes(result.shares[:4])
        self.assertEqual(recovered, KEY_256_BIT)

    def test_leading_zero_bytes_are_preserved(self):
        secret = bytes(4) + b"\xab" * 28          # 32 bytes, first four are 0x00
        result = self.engine.split_secret_bytes(secret, threshold_k=2, total_shares_n=3)
        recovered = self.engine.reconstruct_secret_bytes(result.shares[:3])
        self.assertEqual(recovered, secret)
        self.assertEqual(len(recovered), 32)

    def test_bip39_length_seed_round_trip(self):
        seed = bytes(range(64))                   # 512-bit BIP-39 seed length
        result = self.engine.split_secret_bytes(seed, threshold_k=4, total_shares_n=7)
        self.assertEqual(self.engine.reconstruct_secret_bytes(result.shares[:5]), seed)

    def test_empty_and_non_bytes_secrets_rejected(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret_bytes(b"", threshold_k=2, total_shares_n=3)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret_bytes("hex-string", threshold_k=2, total_shares_n=3)

    def test_integer_api_shares_rejected_by_the_bytes_api(self):
        # An untagged secret has no 0x01 prefix, so this must raise rather than
        # hand back a truncated byte string.
        result = self.engine.split_secret(2**300, threshold_k=2, total_shares_n=3)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret_bytes(result.shares[:2])


class TestSplitValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine()

    def test_threshold_greater_than_total_rejected(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret(100, threshold_k=5, total_shares_n=3)

    def test_threshold_of_one_rejected(self):
        # k=1 makes every share a verbatim copy of the secret, which is exactly
        # the single point of failure this skill exists to remove.
        self.assertEqual(MIN_THRESHOLD, 2)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret(100, threshold_k=1, total_shares_n=3)

    def test_zero_and_negative_thresholds_rejected(self):
        for k in (0, -1):
            with self.assertRaises(ShamirSecretSharingError):
                self.engine.split_secret(100, threshold_k=k, total_shares_n=3)

    def test_secret_outside_the_field_rejected(self):
        for secret in (-1, PRIME_FIELD_MODULUS, PRIME_FIELD_MODULUS + 1):
            with self.assertRaises(ShamirSecretSharingError):
                self.engine.split_secret(secret, threshold_k=2, total_shares_n=3)

    def test_non_integer_arguments_rejected(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret(1.5, threshold_k=2, total_shares_n=3)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.split_secret(100, threshold_k="3", total_shares_n=5)


class TestReconstructionRefusals(unittest.TestCase):
    """Every path that used to return a plausible wrong secret must now raise."""

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine()
        self.secret = 987654321987654321
        self.result = self.engine.split_secret(
            self.secret, threshold_k=3, total_shares_n=5
        )

    def test_sub_threshold_share_set_raises(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(self.result.shares[:2])

    def test_duplicate_share_index_raises(self):
        # Regression: duplicating a share to reach the count made the Lagrange
        # denominator zero and returned an unrelated 127-bit integer.
        dup = [self.result.shares[0], self.result.shares[0], self.result.shares[1]]
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(dup)

    def test_share_at_index_zero_raises(self):
        # Regression: SecretShare(0, v) was accepted and echoed v straight back.
        forged = SecretShare(index=0, value=777, threshold_k=3, modulus=MERSENNE_M521)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret([forged] + self.result.shares[:2])

    def test_share_from_a_different_field_raises(self):
        foreign = SecretShare(index=9, value=5, threshold_k=3, modulus=MERSENNE_M127)
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(self.result.shares[:2] + [foreign])

    def test_share_value_outside_the_field_raises(self):
        bad = SecretShare(
            index=9, value=MERSENNE_M521, threshold_k=3, modulus=MERSENNE_M521
        )
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(self.result.shares[:2] + [bad])

    def test_conflicting_declared_thresholds_raise(self):
        mismatched = SecretShare(
            index=self.result.shares[3].index,
            value=self.result.shares[3].value,
            threshold_k=4,
            modulus=MERSENNE_M521,
        )
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(self.result.shares[:3] + [mismatched])

    def test_empty_share_set_raises(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret([])

    def test_non_share_object_raises(self):
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret([(1, 2), (3, 4)])

    def test_corrupt_share_detected_when_a_surplus_share_is_present(self):
        corrupt = SecretShare(
            index=self.result.shares[2].index,
            value=self.result.shares[2].value ^ 1,       # one flipped bit
            threshold_k=3,
            modulus=MERSENNE_M521,
        )
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.reconstruct_secret(
                self.result.shares[:2] + [corrupt, self.result.shares[3]]
            )

    def test_corrupt_share_is_undetectable_at_exactly_k_and_is_warned_about(self):
        # Documented limitation, asserted so it cannot be quietly forgotten: with
        # exactly k shares the result is wrong and only a warning marks it.
        corrupt = SecretShare(
            index=self.result.shares[2].index,
            value=self.result.shares[2].value ^ 1,
            threshold_k=3,
            modulus=MERSENNE_M521,
        )
        with self.assertLogs(
            "shamir_secret_sharing_for_key_backup", level=logging.WARNING
        ) as captured:
            wrong = self.engine.reconstruct_secret(self.result.shares[:2] + [corrupt])
        self.assertNotEqual(wrong, self.secret)
        self.assertTrue(any("cross-check" in line for line in captured.output))

    def test_metadata_free_shares_reconstruct_but_warn(self):
        bare = [
            SecretShare(index=s.index, value=s.value) for s in self.result.shares[:3]
        ]
        with self.assertLogs(
            "shamir_secret_sharing_for_key_backup", level=logging.WARNING
        ):
            self.assertEqual(self.engine.reconstruct_secret(bare), self.secret)

    def test_sub_threshold_bare_shares_reveal_nothing(self):
        # Without metadata the engine cannot refuse, so assert the scheme's own
        # property: 2 of a 3-of-5 split interpolate to an unrelated field element.
        bare = [
            SecretShare(index=s.index, value=s.value) for s in self.result.shares[:2]
        ]
        with self.assertLogs(
            "shamir_secret_sharing_for_key_backup", level=logging.WARNING
        ):
            self.assertNotEqual(self.engine.reconstruct_secret(bare), self.secret)


class TestVerifyShares(unittest.TestCase):

    def setUp(self):
        self.engine = ShamirSecretSharingForKeyBackupEngine()
        self.result = self.engine.split_secret(555, threshold_k=3, total_shares_n=5)

    def test_intact_share_set_is_consistent(self):
        self.assertTrue(self.engine.verify_shares_consistent(self.result.shares))

    def test_corrupt_share_set_is_inconsistent(self):
        corrupt = SecretShare(
            index=5, value=self.result.shares[4].value + 1, threshold_k=3,
            modulus=MERSENNE_M521,
        )
        self.assertFalse(
            self.engine.verify_shares_consistent(self.result.shares[:4] + [corrupt])
        )

    def test_cannot_verify_without_a_surplus_share(self):
        # "cannot verify" must never be reported as "verified".
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.verify_shares_consistent(self.result.shares[:3])

    def test_cannot_verify_without_a_known_threshold(self):
        bare = [
            SecretShare(index=s.index, value=s.value) for s in self.result.shares
        ]
        with self.assertRaises(ShamirSecretSharingError):
            self.engine.verify_shares_consistent(bare)
        self.assertTrue(self.engine.verify_shares_consistent(bare, threshold_k=3))


class TestConstants(unittest.TestCase):

    def test_length_tag_and_mersenne_exponents(self):
        self.assertEqual(LENGTH_TAG, 0x01)
        self.assertEqual(MERSENNE_M127, 2**127 - 1)
        self.assertEqual(MERSENNE_M521, 2**521 - 1)

    def test_documented_moduli_are_prime(self):
        # Lucas-Lehmer: M_p is prime iff s_{p-2} == 0 mod M_p, s_0 = 4. This is an
        # independent check of the two constants the module trusts without testing.
        def lucas_lehmer(p):
            m, s = (1 << p) - 1, 4
            for _ in range(p - 2):
                s = (s * s - 2) % m
            return s == 0

        self.assertTrue(lucas_lehmer(127))
        self.assertTrue(lucas_lehmer(521))


if __name__ == "__main__":
    unittest.main()
