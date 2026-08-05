import dataclasses
import logging
import secrets
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Large prime for finite field operations (Mersenne prime M_127 = 2^127 - 1)
PRIME_FIELD_MODULUS = (1 << 127) - 1

@dataclasses.dataclass
class Config:
    """Legacy config container for backward compatibility."""
    name: str = "shamir-secret-sharing-for-key-backup"

@dataclasses.dataclass
class SecretShare:
    index: int                          # x coordinate (1..n)
    value: int                          # y coordinate f(x)

@dataclasses.dataclass
class SSSResult:
    threshold_k: int
    total_shares_n: int
    shares: List[SecretShare]

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class ShamirSecretSharingForKeyBackupEngine:
    """
    Production-grade Shamir's Secret Sharing (SSS) engine using finite field arithmetic
    over Mersenne Prime M_127 to split private keys into (k, n) threshold shares and reconstruct via Lagrange interpolation.
    """
    def __init__(self, modulus: int = PRIME_FIELD_MODULUS):
        self.modulus = modulus

    def _eval_poly(self, poly: List[int], x: int) -> int:
        """Evaluates polynomial sum(a_i * x^i) mod P using Horner's method."""
        result = 0
        for coeff in reversed(poly):
            result = (result * x + coeff) % self.modulus
        return result

    def _mod_inverse(self, a: int) -> int:
        """Calculates modular multiplicative inverse using Extended Euclidean Algorithm."""
        return pow(a, self.modulus - 2, self.modulus)

    def split_secret(self, secret_int: int, threshold_k: int, total_shares_n: int) -> SSSResult:
        """
        Splits an integer secret into N shares with threshold K.
        Polynomial: f(x) = secret + a_1*x + a_2*x^2 + ... + a_{k-1}*x^{k-1} mod P.
        """
        if threshold_k < 1 or threshold_k > total_shares_n:
            raise ValueError(f"Threshold K ({threshold_k}) must satisfy 1 <= K <= N ({total_shares_n}).")
        if secret_int < 0 or secret_int >= self.modulus:
            raise ValueError(f"Secret value must be within [0, {self.modulus - 1}].")

        # Generate k-1 random coefficients
        poly = [secret_int] + [secrets.randbelow(self.modulus) for _ in range(threshold_k - 1)]

        shares = []
        for x in range(1, total_shares_n + 1):
            y = self._eval_poly(poly, x)
            shares.append(SecretShare(index=x, value=y))

        logger.info(f"SSS SPLIT SUCCESS: Split secret into {total_shares_n} shares with threshold {threshold_k}.")
        return SSSResult(threshold_k=threshold_k, total_shares_n=total_shares_n, shares=shares)

    def reconstruct_secret(self, shares: List[SecretShare]) -> int:
        """
        Reconstructs the secret at f(0) using Lagrange interpolation over finite field.
        f(0) = sum(y_j * prod(-x_m / (x_j - x_m))) mod P
        """
        if not shares:
            raise ValueError("No shares provided for reconstruction.")

        secret = 0
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
                num = (num * (-x_m)) % self.modulus
                den = (den * (x_j - x_m)) % self.modulus

            lagrange_basis = (num * self._mod_inverse(den)) % self.modulus
            secret = (secret + y_j * lagrange_basis) % self.modulus

        logger.info(f"SSS RECONSTRUCTION SUCCESS: Reconstructed secret using {k} shares.")
        return secret
