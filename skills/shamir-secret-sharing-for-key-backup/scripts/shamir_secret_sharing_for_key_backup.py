"""
shamir-secret-sharing-for-key-backup: (k, n) threshold split and reconstruction of
key material over a prime field, per Shamir (CACM 1979).

What this module is and is not
------------------------------
It **is** a correct, dependency-free reference implementation of Shamir's scheme
over a prime field GF(p): a degree ``k-1`` polynomial whose constant term is the
secret, evaluated at ``x = 1..n``, reconstructed at ``x = 0`` by Lagrange
interpolation using modular inverses.

It is **not**:

* **SLIP-0039 compatible.** SLIP-0039 shares are GF(256) byte-wise shares carrying
  an RS1024 checksum, a 4-bit member index/threshold, group structure and a digest
  share, encoded as mnemonic words. Shares produced here interoperate with nothing
  but this module. Do not hand these ``(index, value)`` pairs to a SLIP-0039 wallet
  and do not expect a SLIP-0039 mnemonic to be readable here.
* **A verifiable secret sharing (VSS) scheme.** There is no commitment binding a
  share to the dealer's polynomial, so a *malicious* shareholder submitting a
  forged share cannot be identified. What this module does detect is an
  *accidentally* wrong share (mistyped, transcribed from the wrong envelope,
  bit-rotted) whenever more than ``k`` shares are supplied -- see
  ``verify_shares_consistent``. With exactly ``k`` shares no integrity check of
  any kind is possible, and a wrong share yields a wrong key silently. Always
  collect ``k + 1``.
* **Memory-hygienic.** Python integers are immutable and the garbage collector
  copies them freely; this module cannot wipe the secret, the coefficients or the
  shares from RAM. Split and reconstruct on an air-gapped host, or inside an HSM
  (see ``hardware-security-module-hsm-for-signing-keys``), not on a trading host.

Field choice
------------
The modulus must exceed the secret, so the field size caps what can be shared.
The default is the 13th Mersenne prime ``M_521 = 2^521 - 1`` (also the NIST P-521
field prime), which comfortably holds a 32-byte secp256k1/Ed25519 private key and
a 64-byte BIP-39 seed. ``M_127`` is retained as a named constant because it is the
textbook example, but at 127 bits it cannot hold a 256-bit key at all, and the
engine will reject one.

Secrecy is perfect *for the value*: Shamir (1979) shows ``k-1`` shares reveal
nothing about the secret. It is not perfect for the *size*: the field is public,
so anyone holding a share learns that the secret is smaller than the modulus.
NIST SP 800-57 Part 1 Rev. 5 states this precisely -- ``k-1`` shares provide "no
information about the key other than, possibly, its length".

See ``references/standards.md`` for sourcing and for which constraints come from
an external standard versus this module's own engineering choices.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: 12th Mersenne prime, 127 bits. The textbook modulus, and too small for a
#: 256-bit key. Kept for short secrets and for reproducing published examples.
MERSENNE_M127: int = (1 << 127) - 1

#: 13th Mersenne prime, 521 bits; also the NIST P-521 field prime. Holds a
#: 32-byte private key and a 64-byte BIP-39 seed with room for the length tag.
MERSENNE_M521: int = (1 << 521) - 1

#: Default field. Chosen so that the advertised use case -- backing up a 256-bit
#: signing key or a 512-bit seed -- actually fits.
PRIME_FIELD_MODULUS: int = MERSENNE_M521

#: Moduli whose primality is established (Lucas-Lehmer) and need no runtime test.
KNOWN_PRIME_MODULI = frozenset({MERSENNE_M127, MERSENNE_M521})

#: k = 1 gives every shareholder a full copy of the key, which is the single
#: point of failure this skill exists to remove. SLIP-0039 makes the same call:
#: "If the member threshold Ti of a group is 1, then the size Ni ... SHOULD be 1".
MIN_THRESHOLD: int = 2

#: Prefix byte for the bytes API, so a key beginning with 0x00 keeps its length
#: through the integer round trip. Not an integrity check -- see the class docs.
LENGTH_TAG: int = 0x01

#: Informational only: SLIP-0039 caps members per group at 16 because its share
#: index is 4 bits. This module has no such limit; n is bounded by the field.
SLIP39_MAX_MEMBERS_PER_GROUP: int = 16

#: Miller-Rabin rounds for a caller-supplied modulus. Bases are drawn from
#: ``secrets`` so a crafted composite cannot be tuned against fixed bases.
_PRIMALITY_ROUNDS: int = 32


class ShamirSecretSharingError(ValueError):
    """Raised when a split or reconstruction cannot be performed *correctly*.

    Every path that would otherwise return a plausible-looking but wrong secret
    raises this instead. For a key backup tool a loud failure is recoverable --
    fetch another share, re-read the envelope -- while a silently wrong 256-bit
    integer is indistinguishable from a correct one until funds are already lost.
    """


@dataclass(frozen=True)
class SecretShare:
    """One point ``(index, value)`` on the dealer's polynomial.

    ``threshold_k`` and ``modulus`` are unauthenticated metadata carried alongside
    the point so that a reconstruction can tell "not enough shares" from "enough
    shares". They leak nothing about the secret. They are also not protected
    against tampering: an altered ``threshold_k`` causes a refusal or a skipped
    cross-check, never a disclosure. Leave them ``None`` only for shares built by
    hand; shares produced by
    :meth:`ShamirSecretSharingForKeyBackupEngine.split_secret` always carry both.
    """

    index: int                                  # x coordinate, 1 <= index < modulus
    value: int                                  # y coordinate, f(index) mod modulus
    threshold_k: Optional[int] = None           # k shares needed to reconstruct
    modulus: Optional[int] = None               # field this point was computed in


@dataclass(frozen=True)
class SSSResult:
    """The full share set for one split. Never persist this object as a unit."""

    threshold_k: int
    total_shares_n: int
    shares: List[SecretShare]
    modulus: int = PRIME_FIELD_MODULUS
    #: Set only by :meth:`split_secret_bytes`; length of the original byte string.
    secret_length_bytes: Optional[int] = None


def _is_probable_prime(n: int, rounds: int = _PRIMALITY_ROUNDS) -> bool:
    """Miller-Rabin primality test with cryptographically random bases."""
    if n < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n == small:
            return True
        if n % small == 0:
            return False

    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1

    for _ in range(rounds):
        a = 2 + secrets.randbelow(n - 3)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


class ShamirSecretSharingForKeyBackupEngine:
    """Splits key material into ``(k, n)`` threshold shares and reconstructs it.

    The engine is stateless apart from its field: it holds no secret between
    calls. Construct one per field, reuse it freely.

    Reconstruction refuses rather than guesses. It raises on duplicate share
    indices, on an ``x = 0`` share, on shares from a different field, on fewer
    shares than the recorded threshold, and -- when a surplus share is available
    -- on a share set that is not consistent with a single degree ``k-1``
    polynomial.
    """

    def __init__(self, modulus: int = PRIME_FIELD_MODULUS) -> None:
        if not isinstance(modulus, int) or isinstance(modulus, bool):
            raise ShamirSecretSharingError("modulus must be an int.")
        if modulus < 5:
            raise ShamirSecretSharingError("modulus must be a prime >= 5.")
        if modulus not in KNOWN_PRIME_MODULI and not _is_probable_prime(modulus):
            # A composite modulus does not degrade the scheme, it breaks it:
            # Lagrange denominators stop being invertible and reconstruction
            # returns a wrong secret with no error at all.
            raise ShamirSecretSharingError(
                f"modulus ({modulus.bit_length()}-bit) failed a Miller-Rabin "
                f"primality test; GF(p) arithmetic requires a prime field."
            )
        self.modulus = modulus

    @property
    def max_secret_int(self) -> int:
        """Largest integer secret this field can carry."""
        return self.modulus - 1

    @property
    def max_secret_bytes(self) -> int:
        """Largest byte-string secret :meth:`split_secret_bytes` accepts.

        Conservative: any ``L`` with ``2^(8L+1) - 1 < 2^(bitlen-1) <= modulus``
        is safe, giving ``L = (bitlen - 2) // 8``. For M_521 that is 64 bytes,
        which covers a 32-byte private key and a 64-byte BIP-39 seed.
        """
        return (self.modulus.bit_length() - 2) // 8

    # ---------------------------------------------------------------- internals

    def _eval_poly(self, poly: Sequence[int], x: int) -> int:
        """Evaluates sum(a_i * x^i) mod P by Horner's method."""
        result = 0
        for coeff in reversed(poly):
            result = (result * x + coeff) % self.modulus
        return result

    def _mod_inverse(self, a: int) -> int:
        """Modular multiplicative inverse via the Extended Euclidean Algorithm.

        ``pow(a, -1, p)`` is used rather than Fermat's ``pow(a, p-2, p)``: both
        are correct for a prime ``p``, but Fermat's form returns 0 for a
        non-invertible ``a`` instead of raising, which is how a duplicate share
        index used to reconstruct a plausible wrong secret.
        """
        try:
            return pow(a % self.modulus, -1, self.modulus)
        except ValueError as exc:              # pragma: no cover - guarded upstream
            raise ShamirSecretSharingError(
                "Lagrange denominator is not invertible in this field; share "
                "indices must be distinct and non-zero."
            ) from exc

    def _interpolate_at(self, shares: Sequence[SecretShare], x_target: int) -> int:
        """Lagrange interpolation of the polynomial through ``shares``, at ``x_target``.

        ``f(x_t) = sum_j y_j * prod_{m != j} (x_t - x_m) / (x_j - x_m)  (mod P)``.
        Reconstruction is this with ``x_target = 0``; the cross-check evaluates it
        at a held-out share's index.
        """
        total = 0
        k = len(shares)
        for j in range(k):
            x_j = shares[j].index
            y_j = shares[j].value

            num = 1
            den = 1
            for m in range(k):
                if m == j:
                    continue
                x_m = shares[m].index
                num = (num * (x_target - x_m)) % self.modulus
                den = (den * (x_j - x_m)) % self.modulus

            basis = (num * self._mod_inverse(den)) % self.modulus
            total = (total + y_j * basis) % self.modulus
        return total

    def _validate_shares(self, shares: Sequence[SecretShare]) -> List[SecretShare]:
        """Rejects every share set that cannot yield a trustworthy reconstruction."""
        if not shares:
            raise ShamirSecretSharingError("No shares provided for reconstruction.")

        seen_indices = set()
        for share in shares:
            if not isinstance(share, SecretShare):
                raise ShamirSecretSharingError(f"Not a SecretShare: {share!r}.")
            if not isinstance(share.index, int) or isinstance(share.index, bool):
                raise ShamirSecretSharingError("Share index must be an int.")
            if not 1 <= share.index < self.modulus:
                # x = 0 is the secret itself; accepting it would let a caller
                # "reconstruct" a secret they simply handed in.
                raise ShamirSecretSharingError(
                    f"Share index {share.index} out of range; "
                    f"expected 1 <= index < modulus."
                )
            if not isinstance(share.value, int) or isinstance(share.value, bool):
                raise ShamirSecretSharingError("Share value must be an int.")
            if not 0 <= share.value < self.modulus:
                raise ShamirSecretSharingError(
                    f"Share {share.index} value is outside the field [0, modulus)."
                )
            if share.modulus is not None and share.modulus != self.modulus:
                raise ShamirSecretSharingError(
                    f"Share {share.index} was created in a "
                    f"{share.modulus.bit_length()}-bit field but this engine uses a "
                    f"{self.modulus.bit_length()}-bit field."
                )
            if share.index in seen_indices:
                # The same point twice is not two shares. Previously this made the
                # Lagrange denominator zero and returned an unrelated integer.
                raise ShamirSecretSharingError(
                    f"Duplicate share index {share.index}; each share must be a "
                    f"distinct point."
                )
            seen_indices.add(share.index)

        return list(shares)

    @staticmethod
    def _resolve_threshold(shares: Sequence[SecretShare]) -> Optional[int]:
        """Threshold recorded by the shares, or ``None`` if they carry none."""
        declared = {s.threshold_k for s in shares if s.threshold_k is not None}
        if not declared:
            return None
        if len(declared) > 1:
            raise ShamirSecretSharingError(
                f"Shares declare conflicting thresholds {sorted(declared)}; they are "
                f"not from the same split."
            )
        return declared.pop()

    # ------------------------------------------------------------------- public

    def split_secret(
        self, secret_int: int, threshold_k: int, total_shares_n: int
    ) -> SSSResult:
        """Splits an integer secret into ``n`` shares of which any ``k`` reconstruct it.

        Polynomial: ``f(x) = secret + a_1 x + ... + a_{k-1} x^{k-1} mod P`` with
        coefficients from ``secrets.randbelow`` (CSPRNG). The leading coefficient
        is resampled if it lands on zero, which would silently drop the effective
        threshold to ``k-1``.
        """
        for name, value in (
            ("threshold_k", threshold_k),
            ("total_shares_n", total_shares_n),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ShamirSecretSharingError(f"{name} must be an int.")
        if threshold_k < MIN_THRESHOLD:
            raise ShamirSecretSharingError(
                f"Threshold K ({threshold_k}) must be >= {MIN_THRESHOLD}; K=1 makes "
                f"every share a full copy of the secret."
            )
        if threshold_k > total_shares_n:
            raise ShamirSecretSharingError(
                f"Threshold K ({threshold_k}) must satisfy K <= N ({total_shares_n}); "
                f"otherwise the secret is unrecoverable the moment it is split."
            )
        if total_shares_n >= self.modulus:
            raise ShamirSecretSharingError(
                f"N ({total_shares_n}) must be < modulus so every share index is a "
                f"distinct non-zero field element."
            )
        if not isinstance(secret_int, int) or isinstance(secret_int, bool):
            raise ShamirSecretSharingError("secret_int must be an int.")
        if not 0 <= secret_int <= self.max_secret_int:
            raise ShamirSecretSharingError(
                f"Secret ({secret_int.bit_length()} bits) must lie in "
                f"[0, modulus) for this {self.modulus.bit_length()}-bit field. Use a "
                f"larger prime (MERSENNE_M521 holds a 256-bit key; M127 does not)."
            )

        coefficients = [secret_int] + [
            secrets.randbelow(self.modulus) for _ in range(threshold_k - 1)
        ]
        while coefficients[-1] == 0:
            coefficients[-1] = secrets.randbelow(self.modulus)

        shares = [
            SecretShare(
                index=x,
                value=self._eval_poly(coefficients, x),
                threshold_k=threshold_k,
                modulus=self.modulus,
            )
            for x in range(1, total_shares_n + 1)
        ]

        logger.info(
            "SSS split: %d shares, threshold %d, %d-bit field.",
            total_shares_n,
            threshold_k,
            self.modulus.bit_length(),
        )
        return SSSResult(
            threshold_k=threshold_k,
            total_shares_n=total_shares_n,
            shares=shares,
            modulus=self.modulus,
        )

    def split_secret_bytes(
        self, secret: bytes, threshold_k: int, total_shares_n: int
    ) -> SSSResult:
        """Splits raw key material, preserving its exact byte length.

        Converting a key to an integer loses leading zero bytes -- a 32-byte key
        starting ``00`` would come back as 31 bytes and be a different key. A
        ``0x01`` tag byte is prepended before the integer conversion so the length
        survives without publishing any extra metadata alongside the shares.
        """
        if not isinstance(secret, (bytes, bytearray)):
            raise ShamirSecretSharingError("secret must be bytes.")
        if len(secret) == 0:
            raise ShamirSecretSharingError("secret must not be empty.")
        if len(secret) > self.max_secret_bytes:
            raise ShamirSecretSharingError(
                f"Secret is {len(secret)} bytes; this "
                f"{self.modulus.bit_length()}-bit field holds at most "
                f"{self.max_secret_bytes}."
            )

        tagged = int.from_bytes(bytes([LENGTH_TAG]) + bytes(secret), "big")
        result = self.split_secret(tagged, threshold_k, total_shares_n)
        return SSSResult(
            threshold_k=result.threshold_k,
            total_shares_n=result.total_shares_n,
            shares=result.shares,
            modulus=result.modulus,
            secret_length_bytes=len(secret),
        )

    def reconstruct_secret(self, shares: Sequence[SecretShare]) -> int:
        """Reconstructs ``f(0)`` from ``shares`` by Lagrange interpolation.

        Raises rather than returning a wrong secret when the share set is
        unusable: duplicated indices, an ``x = 0`` share, a foreign field, fewer
        shares than the recorded threshold, or -- with a surplus share present --
        points that do not lie on one degree ``k-1`` polynomial.

        With exactly ``k`` shares no integrity check exists; a corrupt share
        returns a wrong secret and only a WARNING is logged. Collect ``k + 1``.
        """
        validated = self._validate_shares(shares)
        threshold_k = self._resolve_threshold(validated)

        if threshold_k is None:
            logger.warning(
                "SSS reconstruct: shares carry no threshold metadata; neither "
                "sufficiency nor consistency can be checked."
            )
            return self._interpolate_at(validated, 0)

        if len(validated) < threshold_k:
            raise ShamirSecretSharingError(
                f"{len(validated)} share(s) supplied but threshold is {threshold_k}. "
                f"Interpolating anyway would return an unrelated integer, not the "
                f"secret."
            )

        if len(validated) == threshold_k:
            logger.warning(
                "SSS reconstruct: exactly %d share(s) supplied; no integrity "
                "cross-check is possible. Supply k+1 to detect a corrupt share.",
                threshold_k,
            )
        elif not self.verify_shares_consistent(validated, threshold_k):
            raise ShamirSecretSharingError(
                "Shares are mutually inconsistent: they do not lie on a single "
                f"degree-{threshold_k - 1} polynomial. At least one is corrupt or "
                f"belongs to a different split."
            )

        secret = self._interpolate_at(validated[:threshold_k], 0)
        logger.info(
            "SSS reconstruct: %d share(s), threshold %d.", len(validated), threshold_k
        )
        return secret

    def reconstruct_secret_bytes(self, shares: Sequence[SecretShare]) -> bytes:
        """Reconstructs key material split by :meth:`split_secret_bytes`.

        The ``0x01`` tag is a shape check, not an integrity check: it catches the
        common mistake of feeding integer-API shares to the bytes API, but a
        corrupt share passes it roughly 1 time in 256. Real detection comes from
        supplying a surplus share.
        """
        value = self.reconstruct_secret(shares)
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if not raw or raw[0] != LENGTH_TAG:
            raise ShamirSecretSharingError(
                "Reconstructed value has no 0x01 length tag: these shares were not "
                "produced by split_secret_bytes, or the share set is corrupt."
            )
        return raw[1:]

    def verify_shares_consistent(
        self, shares: Sequence[SecretShare], threshold_k: Optional[int] = None
    ) -> bool:
        """Cross-checks a share set against itself using at least one surplus share.

        Interpolates the polynomial through the first ``k`` shares and evaluates it
        at each remaining share's index. A mismatch proves the *set* is corrupt; it
        does not identify which share is at fault, and it cannot detect a forgery
        by a shareholder who knows the polynomial (that needs a VSS scheme).

        Raises if fewer than ``k + 1`` shares are supplied -- "cannot verify" must
        never be reported as "verified".
        """
        validated = self._validate_shares(shares)
        k = threshold_k if threshold_k is not None else self._resolve_threshold(validated)
        if k is None:
            raise ShamirSecretSharingError(
                "Threshold unknown: pass threshold_k, or use shares carrying it."
            )
        if not isinstance(k, int) or isinstance(k, bool) or k < MIN_THRESHOLD:
            raise ShamirSecretSharingError(
                f"threshold_k must be an int >= {MIN_THRESHOLD}."
            )
        if len(validated) <= k:
            raise ShamirSecretSharingError(
                f"Consistency needs at least k+1 = {k + 1} shares; "
                f"{len(validated)} supplied."
            )

        basis = validated[:k]
        for extra in validated[k:]:
            if self._interpolate_at(basis, extra.index) != extra.value:
                logger.warning(
                    "SSS consistency check failed: held-out share %d does not lie "
                    "on the polynomial through the first %d shares. The corrupt "
                    "share may be any of them, not necessarily share %d.",
                    extra.index, k, extra.index,
                )
                return False
        return True
