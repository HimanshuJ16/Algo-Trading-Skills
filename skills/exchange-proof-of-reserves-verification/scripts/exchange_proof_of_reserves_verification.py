"""
exchange-proof-of-reserves-verification: independent verification of an exchange's
published Merkle **sum** tree Proof of Reserves (PoR).

What this module proves
-----------------------
Given one user's leaf, that user's audit path, the exchange's published Merkle
root, its declared total user liabilities and an independently established
on-chain reserve figure, it checks four things:

1. the audit path rehashes to the published root (the user's balance really is
   committed to by that root);
2. no balance on the user's branch is negative;
3. the **root sum** committed by the tree equals the liability figure the
   exchange declared -- the check that makes a Merkle *sum* tree worth more than
   a plain Merkle tree; and
4. on-chain reserves cover those liabilities at or above the configured ratio.

What this module cannot prove
-----------------------------
A single inclusion proof is evidence about **one branch**, not about the tree.
Binance's own explanation of why it moved to zk-SNARKs is explicit that a Merkle
inclusion proof cannot independently verify that all balances sum correctly or
that no negative balances exist elsewhere in the tree. This module therefore
reports on the branch it was given and on the declared totals; it does not and
cannot audit unseen leaves. Only a zero-knowledge circuit over the whole tree
(Binance zkPoR) or a full-tree dump gives that.

It also takes ``total_verified_onchain_reserves`` on trust. Establishing that
figure -- address attribution, signed control messages, unencumbered ownership --
is out of scope and is exactly where a PoR exercise usually fails. The PCAOB
Office of the Investor Advocate advisory of 2023-03-08 lists the standing
limitations: PoR engagements are not audits, cover a point in time, likely do not
address the entity's liabilities or holders' rights, and cannot show whether the
assets were borrowed to look collateralised. See ``references/standards.md``.

Determinism and precision
-------------------------
Balances are canonicalised to fixed-point ``Decimal`` at ``balance_decimals``
places (default 8, the satoshi convention most PoR files use) before hashing or
summing. Binary floats cannot represent typical stablecoin liability totals
exactly and their sums are order-dependent, so hashing a formatted float is not
reproducible across implementations. Pass balances as ``str``/``Decimal`` for
exact values; ``float`` is accepted and converted via ``str`` so ``0.1`` means
``0.1``. ``balance_decimals`` must match the precision the exchange used to build
its tree, or the recomputed root will not match.

Hash encoding
-------------
Leaf and interior preimages carry distinct domain-separation prefixes (0x00 /
0x01) and every field is length-prefixed. RFC 6962 section 2.1 requires this
separation "to give second preimage resistance": without it an attacker-chosen
``account_id`` can make a leaf preimage byte-identical to an interior-node
preimage, letting a subtree be passed off as a single small user leaf. This
encoding is self-consistent; a real exchange dictates its own encoding, so match
that exchange's format before comparing against its published root.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN
from typing import List, Tuple, Union

logger = logging.getLogger(__name__)

#: Anything acceptable as a balance. ``float`` is converted through ``str`` so
#: that the decimal literal the caller wrote is what gets hashed.
BalanceInput = Union[int, float, str, Decimal]

#: RFC 6962 section 2.1 domain separation: leaves and interior nodes must hash
#: under different prefixes or the tree is not second-preimage resistant.
LEAF_DOMAIN_PREFIX = b"\x00"
NODE_DOMAIN_PREFIX = b"\x01"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: Precision of the reported ratio. ROUND_DOWN so the published figure can never
#: overstate coverage relative to the exact ratio the verdict was taken on.
_RATIO_EXPONENT = Decimal("0.00000001")

STATUS_SOLVENT = "SOLVENT_FULL_RESERVES"
STATUS_DEFICIT = "INSOLVENT_RESERVE_DEFICIT"
STATUS_INVALID_PROOF = "INVALID_MERKLE_PROOF"
#: Root hash verified, but the sum the tree commits to is not the liability
#: figure the exchange published -- understated liabilities inflate the ratio.
STATUS_LIABILITY_MISMATCH = "INCONSISTENT_LIABILITY_TOTAL"


class ProofOfReservesError(ValueError):
    """Raised when an input or engine configuration is unusable.

    Verification must fail loudly rather than degrade. A malformed root hash, a
    non-finite balance or a non-positive liability total is a data error, and
    returning a solvency verdict computed from it would be worse than useless:
    it would carry the authority of a cryptographic check that never happened.
    """


@dataclass
class UserAccountBalance:
    """One user's balance for one asset, as committed at a leaf of the tree."""

    account_id: str
    asset_symbol: str                   # e.g. 'BTC', 'ETH', 'USDT'
    balance: BalanceInput


@dataclass
class MerkleAuditPathNode:
    """One sibling on the path from the user's leaf up to the root.

    ``is_sibling_right`` is True when the sibling is the *right* child, i.e. the
    running node is the left input to the parent hash.
    """

    sibling_hash: str
    sibling_balance: BalanceInput
    is_sibling_right: bool


@dataclass
class ProofOfReservesAuditReport:
    exchange_name: str
    asset_symbol: str
    declared_merkle_root_hash: str
    computed_merkle_root_hash: str
    computed_merkle_root_balance: Decimal   # sum the tree commits to at the root
    # True only when the branch passed every check: the path rehashed to the
    # declared root AND no balance on it was negative. Gate on this field.
    is_user_inclusion_verified: bool
    is_declared_liability_consistent: bool  # root sum == declared liabilities
    total_declared_liabilities: Decimal
    total_verified_onchain_reserves: Decimal
    reserve_ratio_percentage: Decimal       # (OnChain / Liabilities) * 100
    solvency_status: str
    audit_notes: str
    findings: List[str] = field(default_factory=list)


class ExchangeProofOfReservesEngine:
    """Verifies a single-user Merkle sum tree inclusion proof and the reserve ratio.

    Args:
        min_reserve_ratio_pct: Coverage required for a ``SOLVENT_FULL_RESERVES``
            verdict. 100.0 is the definition of full reserves, not a threshold
            set by any regulator -- no reviewed jurisdiction mandates
            cryptographic PoR at all. Raise it above 100 to require a buffer.
        balance_decimals: Fixed-point precision balances are canonicalised to
            before hashing. Must equal the precision the exchange used to build
            the tree, or the recomputed root will not match.
        enforce_root_sum_match: Require the verified root sum to equal the
            declared liabilities. Set False **only** for a plain Merkle tree that
            commits no sums (Binance's pre-zkPoR design); the report then records
            that the declared liability figure was taken on trust.
    """

    def __init__(
        self,
        min_reserve_ratio_pct: float = 100.0,
        balance_decimals: int = 8,
        enforce_root_sum_match: bool = True,
    ) -> None:
        if isinstance(balance_decimals, bool) or not isinstance(balance_decimals, int):
            raise ProofOfReservesError("balance_decimals must be an int.")
        if not 0 <= balance_decimals <= 18:
            raise ProofOfReservesError(
                f"balance_decimals must be between 0 and 18, got {balance_decimals}."
            )
        self.balance_decimals = balance_decimals
        self._balance_exponent = Decimal(1).scaleb(-balance_decimals)

        self.min_reserve_ratio_pct = self._to_decimal(
            min_reserve_ratio_pct, "min_reserve_ratio_pct"
        )
        if self.min_reserve_ratio_pct <= 0:
            raise ProofOfReservesError(
                f"min_reserve_ratio_pct must be > 0, got {min_reserve_ratio_pct!r}."
            )
        self.enforce_root_sum_match = bool(enforce_root_sum_match)

    # ------------------------------------------------------------------
    # Canonicalisation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_decimal(value: BalanceInput, field_name: str) -> Decimal:
        """Convert to Decimal, rejecting NaN/Infinity and unparseable values.

        ``float`` goes through ``str`` so that ``0.1`` becomes ``Decimal('0.1')``
        rather than its binary expansion. NaN must be rejected explicitly: every
        comparison against NaN is False, so an unchecked NaN balance would slip
        past a ``< 0`` guard and silently poison the ratio.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ProofOfReservesError(
                f"{field_name} must be int, float, str or Decimal, "
                f"got {type(value).__name__}."
            )
        try:
            dec = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ProofOfReservesError(
                f"{field_name} is not a valid number: {value!r}."
            ) from exc
        if not dec.is_finite():
            raise ProofOfReservesError(f"{field_name} must be finite, got {value!r}.")
        return dec

    def canonical_balance(self, value: BalanceInput, field_name: str = "balance") -> Decimal:
        """Quantise a balance to the engine's fixed-point precision.

        Adding ``Decimal(0)`` normalises ``-0`` to ``0`` so a negative-zero
        balance is never reported as a negative balance.
        """
        dec = self._to_decimal(value, field_name)
        try:
            quantised = dec.quantize(self._balance_exponent, rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ProofOfReservesError(
                f"{field_name} {value!r} cannot be represented at "
                f"{self.balance_decimals} decimal places."
            ) from exc
        return quantised + Decimal(0)

    def _format_balance(self, balance: Decimal) -> str:
        return f"{balance:.{self.balance_decimals}f}"

    @staticmethod
    def _format_ratio(ratio: Decimal) -> str:
        """Render a ratio without rounding it toward the threshold.

        Formatting a 99.999% deficit as "100.00%" puts a number in the audit
        note that contradicts the note's own verdict, which is precisely the
        failure mode the exact-comparison rule exists to prevent.
        """
        text = f"{ratio:f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def normalize_hash(value: str, field_name: str) -> str:
        """Accept a hex digest in any case, with or without an ``0x`` prefix.

        Exchanges publish roots in mixed conventions; a case-sensitive compare
        would report ``INVALID_MERKLE_PROOF`` for a proof that is actually
        correct, which is the more corrosive of the two error directions here --
        it teaches the operator to distrust the tool.
        """
        if not isinstance(value, str):
            raise ProofOfReservesError(
                f"{field_name} must be a hex string, got {type(value).__name__}."
            )
        cleaned = value.strip().lower()
        if cleaned.startswith("0x"):
            cleaned = cleaned[2:]
        if not _HEX64_RE.match(cleaned):
            raise ProofOfReservesError(
                f"{field_name} must be a 64-character SHA-256 hex digest, got {value!r}."
            )
        return cleaned

    @staticmethod
    def _frame(*parts: bytes) -> bytes:
        """Length-prefix each field so no field's content can forge a boundary."""
        return b"".join(len(p).to_bytes(8, "big") + p for p in parts)

    # ------------------------------------------------------------------
    # Merkle sum tree primitives
    # ------------------------------------------------------------------
    def compute_leaf_hash(
        self, account_id: str, asset_symbol: str, balance: BalanceInput
    ) -> str:
        """SHA-256 of the domain-separated, length-framed leaf preimage."""
        if not isinstance(account_id, str) or not account_id:
            raise ProofOfReservesError("account_id must be a non-empty string.")
        if not isinstance(asset_symbol, str) or not asset_symbol:
            raise ProofOfReservesError("asset_symbol must be a non-empty string.")
        bal = self.canonical_balance(balance, "balance")
        preimage = LEAF_DOMAIN_PREFIX + self._frame(
            account_id.encode("utf-8"),
            asset_symbol.encode("utf-8"),
            self._format_balance(bal).encode("utf-8"),
        )
        return hashlib.sha256(preimage).hexdigest()

    def compute_parent_hash(
        self,
        left_hash: str,
        left_bal: BalanceInput,
        right_hash: str,
        right_bal: BalanceInput,
    ) -> Tuple[str, Decimal]:
        """Combine two children into ``(parent_hash, parent_balance)``.

        The parent commits to both child hashes *and* both child balances, which
        is what makes the sum auditable: a child balance cannot be restated
        without changing every hash above it.
        """
        lh = self.normalize_hash(left_hash, "left_hash")
        rh = self.normalize_hash(right_hash, "right_hash")
        lb = self.canonical_balance(left_bal, "left_bal")
        rb = self.canonical_balance(right_bal, "right_bal")
        preimage = NODE_DOMAIN_PREFIX + self._frame(
            lh.encode("ascii"),
            self._format_balance(lb).encode("utf-8"),
            rh.encode("ascii"),
            self._format_balance(rb).encode("utf-8"),
        )
        return hashlib.sha256(preimage).hexdigest(), lb + rb

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def verify_proof_of_reserves(
        self,
        exchange_name: str,
        user_leaf: UserAccountBalance,
        audit_path: Sequence[MerkleAuditPathNode],
        declared_merkle_root: str,
        total_declared_liabilities: BalanceInput,
        total_verified_onchain_reserves: BalanceInput,
    ) -> ProofOfReservesAuditReport:
        """Verify one user's inclusion proof and the exchange's reserve ratio.

        ``total_declared_liabilities`` and ``total_verified_onchain_reserves``
        must both be denominated in ``user_leaf.asset_symbol``; the engine
        verifies one asset's tree at a time and cannot detect a mismatched pair.

        Raises:
            ProofOfReservesError: on malformed input. An invalid *proof* is
                reported as a verdict; invalid *input* raises.
        """
        if not isinstance(exchange_name, str) or not exchange_name.strip():
            raise ProofOfReservesError("exchange_name must be a non-empty string.")
        if not isinstance(user_leaf, UserAccountBalance):
            raise ProofOfReservesError("user_leaf must be a UserAccountBalance.")
        if isinstance(audit_path, (str, bytes)) or not isinstance(audit_path, Sequence):
            raise ProofOfReservesError(
                "audit_path must be a sequence of MerkleAuditPathNode."
            )

        declared_root = self.normalize_hash(declared_merkle_root, "declared_merkle_root")

        liabilities = self.canonical_balance(
            total_declared_liabilities, "total_declared_liabilities"
        )
        if liabilities <= 0:
            raise ProofOfReservesError(
                f"total_declared_liabilities must be > 0, got "
                f"{total_declared_liabilities!r}."
            )
        reserves = self.canonical_balance(
            total_verified_onchain_reserves, "total_verified_onchain_reserves"
        )
        if reserves < 0:
            raise ProofOfReservesError(
                f"total_verified_onchain_reserves must be >= 0, got "
                f"{total_verified_onchain_reserves!r}."
            )

        findings: List[str] = []

        # Rules 1 and 2: rehash the branch, auditing every balance on it. The
        # traversal always runs to completion -- an operator investigating a
        # negative node still needs to know whether the root matched.
        user_balance = self.canonical_balance(user_leaf.balance, "user_leaf.balance")
        if user_balance < 0:
            findings.append(
                f"NEGATIVE_LEAF_BALANCE: user '{user_leaf.account_id}' is committed with "
                f"{self._format_balance(user_balance)} {user_leaf.asset_symbol}. A negative "
                f"leaf shrinks the declared liability total without any user noticing."
            )

        curr_hash = self.compute_leaf_hash(
            user_leaf.account_id, user_leaf.asset_symbol, user_balance
        )
        curr_bal = user_balance

        for depth, node in enumerate(audit_path):
            if not isinstance(node, MerkleAuditPathNode):
                raise ProofOfReservesError(
                    f"audit_path[{depth}] must be a MerkleAuditPathNode, "
                    f"got {type(node).__name__}."
                )
            sibling_bal = self.canonical_balance(
                node.sibling_balance, f"audit_path[{depth}].sibling_balance"
            )
            if sibling_bal < 0:
                findings.append(
                    f"NEGATIVE_SIBLING_BALANCE: node at depth {depth} carries "
                    f"{self._format_balance(sibling_bal)} {user_leaf.asset_symbol}."
                )
            if node.is_sibling_right:
                curr_hash, curr_bal = self.compute_parent_hash(
                    curr_hash, curr_bal, node.sibling_hash, sibling_bal
                )
            else:
                curr_hash, curr_bal = self.compute_parent_hash(
                    node.sibling_hash, sibling_bal, curr_hash, curr_bal
                )

        has_negative_balance = bool(findings)
        root_matches = curr_hash == declared_root
        if not root_matches:
            findings.append(
                f"ROOT_MISMATCH: recomputed root '{curr_hash[:12]}...' does not match the "
                f"declared root '{declared_root[:12]}...'."
            )
        is_inclusion_valid = root_matches and not has_negative_balance

        # Rule 3: the root sum is the liability figure the tree actually commits
        # to. Comparing it against the declared total is the whole point of a sum
        # tree -- skip it and an exchange can publish a smaller liability number
        # than its own tree contains and report an inflated coverage ratio. Only
        # meaningful once the root hash matches; otherwise ``curr_bal`` is a sum
        # over nodes nothing has authenticated.
        if not self.enforce_root_sum_match:
            is_liability_consistent = True
            findings.append(
                "ROOT_SUM_UNENFORCED: enforce_root_sum_match is off, so the declared "
                "liability total is taken on trust and is not cryptographically verified."
            )
        elif not root_matches:
            is_liability_consistent = False
            findings.append(
                "ROOT_SUM_UNVERIFIABLE: the root hash did not match, so the recomputed "
                "sum is a sum over unverified nodes and proves nothing about liabilities."
            )
        elif curr_bal != liabilities:
            is_liability_consistent = False
            findings.append(
                f"LIABILITY_UNDERSTATEMENT: the tree commits to "
                f"{self._format_balance(curr_bal)} {user_leaf.asset_symbol} at the root but "
                f"{self._format_balance(liabilities)} was declared (delta "
                f"{self._format_balance(curr_bal - liabilities)})."
            )
        else:
            is_liability_consistent = True

        # Rule 4: the exact ratio decides the verdict; the reported figure is a
        # truncated view of it. Rounding before the comparison would classify a
        # 99.999% deficit as fully reserved.
        exact_ratio = (reserves / liabilities) * Decimal(100)
        reported_ratio = exact_ratio.quantize(_RATIO_EXPONENT, rounding=ROUND_DOWN)

        if not is_inclusion_valid:
            status = STATUS_INVALID_PROOF
            notes = (
                f"PROOF REJECTED [{exchange_name}]: inclusion proof for "
                f"'{user_leaf.account_id}' failed. No solvency conclusion can be drawn."
            )
            logger.critical("%s Findings: %s", notes, "; ".join(findings))
        elif not is_liability_consistent:
            status = STATUS_LIABILITY_MISMATCH
            notes = (
                f"LIABILITY TOTAL REJECTED [{exchange_name}]: inclusion verified, but the "
                f"declared liability total does not match the sum committed at the root. "
                f"The reserve ratio is computed from an unreliable denominator."
            )
            logger.critical("%s Findings: %s", notes, "; ".join(findings))
        elif exact_ratio < self.min_reserve_ratio_pct:
            status = STATUS_DEFICIT
            findings.append(
                f"RESERVE_DEFICIT: shortfall of "
                f"{self._format_balance(liabilities - reserves)} {user_leaf.asset_symbol}."
            )
            notes = (
                f"INSOLVENT DEFICIT DETECTED [{exchange_name}]: on-chain reserves "
                f"({reserves:,f} {user_leaf.asset_symbol}) cover only "
                f"{self._format_ratio(reported_ratio)}% of verified liabilities "
                f"({liabilities:,f} {user_leaf.asset_symbol}). "
                f"Required: {self.min_reserve_ratio_pct}%."
            )
            logger.critical(notes)
        else:
            status = STATUS_SOLVENT
            notes = (
                f"PROOF OF RESERVES VERIFIED [{exchange_name}]: user inclusion verified and "
                f"the root sum matches the declared total. On-chain reserves cover "
                f"{self._format_ratio(reported_ratio)}% of liabilities "
                f"({reserves:,f} / {liabilities:,f} "
                f"{user_leaf.asset_symbol}). Snapshot only -- the reserves are not shown to "
                f"be unencumbered or unborrowed."
            )
            logger.info(notes)

        return ProofOfReservesAuditReport(
            exchange_name=exchange_name,
            asset_symbol=user_leaf.asset_symbol,
            declared_merkle_root_hash=declared_root,
            computed_merkle_root_hash=curr_hash,
            computed_merkle_root_balance=curr_bal,
            is_user_inclusion_verified=is_inclusion_valid,
            is_declared_liability_consistent=is_liability_consistent,
            total_declared_liabilities=liabilities,
            total_verified_onchain_reserves=reserves,
            reserve_ratio_percentage=reported_ratio,
            solvency_status=status,
            audit_notes=notes,
            findings=findings,
        )
