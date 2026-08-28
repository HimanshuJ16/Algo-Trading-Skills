"""On-chain transaction anomaly monitoring for EVM custody and trading wallets.

Screens a single EVM transaction payload against five risk vectors -- sanctions
list interaction, sanctions-list staleness, high-value transfer, abnormal gas
price, and unapproved contract method -- and returns a composite score with a
deterministic execution verdict (``TRANSACTION_SAFE`` / ``ANOMALY_SUSPECTED`` /
``HIGH_RISK_BLOCK``).

Two conditions block categorically rather than additively: a sanctions
listed-address match, and a call to a method in ``blocking_methods``.

Evidence and limitations (deliberate, documented):

- ``is_blocked=True`` is an *execution* decision: do not broadcast / do not
  co-sign. It is NOT an OFAC "blocking" of property. Blocking the property of a
  designated person, and filing the initial blocking report with OFAC within
  10 business days (31 CFR 501.603(b)(1)), are separate downstream obligations
  this module does not perform. ``matched_sanctioned_addresses`` is emitted so
  that handoff has evidence to carry.
- Address-list matching is necessary but not sufficient. OFAC's published
  digital currency address listings "are not likely to be exhaustive" (OFAC FAQ
  646); property of a blocked person is blocked whether or not the address
  appears on the SDN List. Treat a clean result as "no listed-address hit", not
  as "sanctions-clear".
- The screening list is a *snapshot*, and OFAC listings move in both
  directions: over 100 Tornado Cash addresses were removed from the SDN List on
  21 Mar 2025 (Treasury press release sb0057). A stale snapshot therefore both
  misses new designations and blocks transactions that are no longer
  prohibited. Hence ``sanctions_list_updated_at`` is mandatory and staleness is
  a scored vector rather than a silent condition.
- Verdicts are computed against the list snapshot supplied, i.e. against
  *current* designations. This is not a point-in-time historical compliance
  tool: replaying a 2023 Tornado Cash transfer against a 2026 snapshot produces
  no hit even though the transfer was prohibited when it occurred.
- Addresses are compared case-insensitively because EIP-55 checksummed and
  all-lowercase hex spellings denote the same EVM account. That normalization
  is EVM-only -- Base58 (BTC, TRON) and Bech32 encodings are case-sensitive and
  must not be routed through this module.
- Method signatures are compared case-*sensitively* and byte-exactly: the
  4-byte selector is keccak-256 of the canonical signature, so
  ``transferFrom(address,address,uint256)`` and its lowercased spelling are
  different functions.
- ``gas_price_gwei`` must be the *effective* gas price the transaction will
  pay. For an EIP-1559 (type-2) transaction that is
  ``baseFeePerGas + min(maxPriorityFeePerGas, maxFeePerGas - baseFeePerGas)``.
  Feeding ``maxFeePerGas`` -- which wallets routinely set to several times the
  base fee as headroom -- against a fixed ceiling generates false positives.
- The additive weights do not block an approval-granting call on their own: a
  drainer's ``setApprovalForAll`` moves no value, so the high-value vector
  cannot see it, and the unapproved-method penalty (30) sits below the block
  threshold. ``blocking_methods`` is empty by default because which calls are
  categorically prohibited is a custody policy decision; set it if this engine
  is relied on for drainer protection.
- Every numeric default here (50,000 USD, 200 Gwei, 5x baseline, 24h list age,
  the 80/40/30/30/20 penalties and the 70/30 thresholds) is an engineering
  default, not a regulatory or protocol constant. The 200 Gwei ceiling in
  particular is Ethereum-mainnet-shaped and is meaningless on chains whose
  normal gas prices sit orders of magnitude away. Calibrate per chain before
  automating.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

# --- Method-signature sentinels -------------------------------------------
# Angle brackets cannot occur in a canonical Solidity signature, so these can
# never collide with a real function.

# Value transfer with empty calldata: there is no function call to whitelist.
NATIVE_TRANSFER_SIGNATURE = "<native-transfer>"
# Calldata was present but could not be decoded (unknown selector, unverified
# contract, proxy). Always treated as unapproved -- never whitelistable.
UNKNOWN_METHOD_SIGNATURE = "<unknown>"

# --- Engineering defaults (tunable; none of these are regulatory constants) ---
DEFAULT_MAX_TRANSFER_USD = 50_000.0
DEFAULT_MAX_GAS_GWEI = 200.0
DEFAULT_GAS_BASELINE_MULTIPLE = 5.0
DEFAULT_MAX_SANCTIONS_LIST_AGE_SECONDS = 86_400.0

PENALTY_BLACKLIST_INTERACTION = 80
PENALTY_HIGH_VALUE_SPIKE = 40
PENALTY_UNAPPROVED_METHOD_CALL = 30
PENALTY_SANCTIONS_LIST_STALE = 30
PENALTY_GAS_SPIKE_MEV = 20
# A categorically prohibited method saturates the score, so the verdict and the
# score never disagree.
PENALTY_BLOCKING_METHOD_CALL = 100

BLOCK_SCORE_THRESHOLD = 70
ANOMALY_SCORE_THRESHOLD = 30
MAX_RISK_SCORE = 100

DEFAULT_WHITELISTED_METHODS = frozenset({
    "transfer(address,uint256)",
    "transferFrom(address,address,uint256)",
    "swap(uint256,uint256,address,bytes)",
})


class OnChainMonitoringError(ValueError):
    """Raised on an invalid transaction payload or risk policy configuration."""


def _require_finite(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OnChainMonitoringError(f"{name} must be a real number, got {value!r}.") from exc
    if not math.isfinite(number):
        # NaN silently defeats every '>' threshold comparison below, so a
        # corrupted feed would otherwise be scored TRANSACTION_SAFE.
        raise OnChainMonitoringError(f"{name} must be finite, got {value!r}.")
    return number


def _require_non_negative(value: float, name: str) -> float:
    number = _require_finite(value, name)
    if number < 0.0:
        raise OnChainMonitoringError(f"{name} must be >= 0, got {number}.")
    return number


def _require_non_empty_str(value: str, name: str) -> str:
    """Validates an identifier token: non-empty, and no interior whitespace.

    Interior whitespace is rejected rather than stripped. No legitimate EVM
    transaction hash or address contains it, and a newline inside a hash would
    forge line breaks into the audit log this engine's findings are evidenced
    from.
    """
    if not isinstance(value, str) or not value.strip():
        raise OnChainMonitoringError(f"{name} must be a non-empty string, got {value!r}.")
    token = value.strip()
    if any(ch.isspace() for ch in token):
        raise OnChainMonitoringError(
            f"{name} must not contain interior whitespace, got {value!r}."
        )
    return token


def normalize_evm_address(address: str) -> str:
    """Lower-cases and trims an EVM address for comparison.

    EIP-55 checksummed and all-lowercase spellings of the same account differ
    only in case. EVM only: Base58/Bech32 addresses are case-sensitive.
    """
    return _require_non_empty_str(address, "address").lower()


def _validate_method_signatures(
    methods: Iterable[str], field_name: str, allow_unknown_sentinel: bool = False
) -> List[str]:
    validated: List[str] = []
    for method in methods:
        if not isinstance(method, str) or not method.strip():
            raise OnChainMonitoringError(
                f"{field_name} entries must be non-empty strings, got {method!r}."
            )
        if any(ch.isspace() for ch in method):
            # 'transfer(address, uint256)' hashes to a different selector than
            # the canonical form and would silently never match a real payload.
            raise OnChainMonitoringError(
                f"{field_name} entry {method!r} contains whitespace; use the canonical "
                "signature form, e.g. 'transfer(address,uint256)'."
            )
        if method == UNKNOWN_METHOD_SIGNATURE and not allow_unknown_sentinel:
            raise OnChainMonitoringError(
                f"{UNKNOWN_METHOD_SIGNATURE!r} must never be whitelisted: undecodable calldata "
                "is precisely the case this vector exists to flag."
            )
        validated.append(method)
    return validated


@dataclass
class OnChainTxPayload:
    """A single transaction (pending or mined) to be screened.

    ``method_signature`` must be the canonical Solidity signature (no spaces),
    or ``NATIVE_TRANSFER_SIGNATURE`` when calldata is empty, or
    ``UNKNOWN_METHOD_SIGNATURE`` when calldata could not be decoded. It must
    never be left blank -- a blank value previously skipped the unapproved-method
    vector entirely, turning the least-understood transactions into the
    lowest-scoring ones.

    ``gas_price_gwei`` must be the effective gas price
    (``baseFee + min(priorityFee, maxFee - baseFee)`` for EIP-1559), not
    ``maxFeePerGas``.

    ``timestamp_utc`` is Unix seconds UTC and is the only clock the engine
    reads, so audits are reproducible on replay.
    """

    tx_hash: str
    from_address: str
    to_address: str
    value_usd: float
    gas_price_gwei: float
    method_signature: str
    block_number: int
    timestamp_utc: float

    def __post_init__(self) -> None:
        self.tx_hash = _require_non_empty_str(self.tx_hash, "tx_hash")
        self.from_address = _require_non_empty_str(self.from_address, "from_address")
        self.to_address = _require_non_empty_str(self.to_address, "to_address")
        self.value_usd = _require_non_negative(self.value_usd, "value_usd")
        self.gas_price_gwei = _require_non_negative(self.gas_price_gwei, "gas_price_gwei")
        self.timestamp_utc = _require_finite(self.timestamp_utc, "timestamp_utc")

        if not isinstance(self.block_number, int) or isinstance(self.block_number, bool):
            raise OnChainMonitoringError(
                f"block_number must be an int, got {self.block_number!r}."
            )
        if self.block_number < 0:
            raise OnChainMonitoringError(f"block_number must be >= 0, got {self.block_number}.")

        if not isinstance(self.method_signature, str) or not self.method_signature.strip():
            raise OnChainMonitoringError(
                "method_signature must be a canonical signature, "
                f"{NATIVE_TRANSFER_SIGNATURE!r} for an empty-calldata transfer, or "
                f"{UNKNOWN_METHOD_SIGNATURE!r} for undecodable calldata; got "
                f"{self.method_signature!r}."
            )
        self.method_signature = self.method_signature.strip()


@dataclass
class OnChainRiskPolicy:
    """Screening thresholds and the sanctions-list snapshot to screen against.

    The address and method collections are frozen at construction: mutating a
    live policy would leave the normalized screening set stale, and a refreshed
    list must in any case carry a new ``sanctions_list_updated_at``. Build a new
    policy instead.

    Set ``sanctions_screening_enabled=False`` only to run the non-sanctions
    vectors deliberately (e.g. a testnet rig). Every report then carries
    ``sanctions_screening_performed=False`` so a clean verdict is never mistaken
    for a sanctions clearance.
    """

    max_transfer_usd: float = DEFAULT_MAX_TRANSFER_USD
    max_gas_gwei: float = DEFAULT_MAX_GAS_GWEI
    sanctioned_addresses: Set[str] = field(default_factory=set)
    whitelisted_methods: Set[str] = field(
        default_factory=lambda: set(DEFAULT_WHITELISTED_METHODS)
    )
    sanctions_screening_enabled: bool = True
    # Unix seconds UTC at which the sanctions snapshot was pulled. Required
    # whenever screening is enabled: an unattributed list cannot be aged.
    sanctions_list_updated_at: Optional[float] = None
    # None disables the staleness vector (use only when list currency is
    # enforced out-of-band and evidenced elsewhere).
    max_sanctions_list_age_seconds: Optional[float] = DEFAULT_MAX_SANCTIONS_LIST_AGE_SECONDS
    # Methods that block outright, like a sanctions hit, instead of adding to
    # the score. Empty by default: which calls are categorically prohibited is
    # a custody policy decision, not something this module can presume. An
    # approval-granting call is the motivating case -- it moves no value, so
    # the high-value vector cannot see it, and the unapproved-method penalty
    # alone (30) does not reach the block threshold.
    blocking_methods: Set[str] = field(default_factory=set)
    # Optional regime-relative gas check, applied in addition to the fixed
    # ceiling. A fixed Gwei ceiling alone is chain- and regime-specific.
    gas_baseline_gwei: Optional[float] = None
    gas_baseline_multiple: float = DEFAULT_GAS_BASELINE_MULTIPLE
    # Normalized screening set, derived once at construction rather than
    # rebuilt per transaction.
    normalized_sanctioned_addresses: FrozenSet[str] = field(
        init=False, repr=False, default_factory=frozenset
    )

    def __post_init__(self) -> None:
        self.max_transfer_usd = _require_non_negative(self.max_transfer_usd, "max_transfer_usd")
        self.max_gas_gwei = _require_non_negative(self.max_gas_gwei, "max_gas_gwei")

        self.whitelisted_methods = frozenset(
            _validate_method_signatures(self.whitelisted_methods, "whitelisted_methods")
        )
        self.blocking_methods = frozenset(
            _validate_method_signatures(
                self.blocking_methods, "blocking_methods", allow_unknown_sentinel=True
            )
        )
        overlap = self.whitelisted_methods & self.blocking_methods
        if overlap:
            raise OnChainMonitoringError(
                f"methods cannot be both whitelisted and blocking: {sorted(overlap)}."
            )
        self.sanctioned_addresses = frozenset(self.sanctioned_addresses)
        self.normalized_sanctioned_addresses = frozenset(
            normalize_evm_address(a) for a in self.sanctioned_addresses
        )

        if self.sanctions_screening_enabled and not self.normalized_sanctioned_addresses:
            raise OnChainMonitoringError(
                "sanctions_screening_enabled is True but sanctioned_addresses is empty: the "
                "engine would report TRANSACTION_SAFE having screened against nothing. Load a "
                "sanctions snapshot, or set sanctions_screening_enabled=False deliberately."
            )

        if self.max_sanctions_list_age_seconds is not None:
            self.max_sanctions_list_age_seconds = _require_non_negative(
                self.max_sanctions_list_age_seconds, "max_sanctions_list_age_seconds"
            )

        if self.sanctions_list_updated_at is not None:
            self.sanctions_list_updated_at = _require_finite(
                self.sanctions_list_updated_at, "sanctions_list_updated_at"
            )
        elif self.sanctions_screening_enabled and self.max_sanctions_list_age_seconds is not None:
            raise OnChainMonitoringError(
                "sanctions_list_updated_at is required when sanctions screening is enabled and "
                "max_sanctions_list_age_seconds is set: an undated list cannot be aged, and "
                "listings change in both directions (e.g. the Tornado Cash delisting of "
                "21 Mar 2025)."
            )

        if self.gas_baseline_gwei is not None:
            self.gas_baseline_gwei = _require_non_negative(
                self.gas_baseline_gwei, "gas_baseline_gwei"
            )
            self.gas_baseline_multiple = _require_finite(
                self.gas_baseline_multiple, "gas_baseline_multiple"
            )
            if self.gas_baseline_multiple <= 0.0:
                raise OnChainMonitoringError(
                    f"gas_baseline_multiple must be > 0, got {self.gas_baseline_multiple}."
                )


@dataclass
class RiskVectorFlag:
    vector_name: str                     # 'BLACKLIST_INTERACTION', 'SANCTIONS_LIST_STALE',
                                         # 'HIGH_VALUE_SPIKE', 'GAS_SPIKE_MEV',
                                         # 'UNAPPROVED_METHOD_CALL'
    score_penalty: int
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM'
    details: str


@dataclass
class OnChainMonitoringReport:
    """Screening verdict plus the evidence a compliance handoff needs.

    ``is_blocked`` means "do not broadcast / do not co-sign". It is not an OFAC
    blocking of property, and it does not discharge the 31 CFR 501.603(b)(1)
    initial blocking report.
    """

    tx_hash: str
    risk_score: int                      # 0 to 100
    risk_flags: List[RiskVectorFlag]
    is_blocked: bool
    status: str                          # 'TRANSACTION_SAFE', 'ANOMALY_SUSPECTED', 'HIGH_RISK_BLOCK'
    audit_notes: str
    # Evidence for the downstream blocking report / investigation.
    matched_sanctioned_addresses: List[str] = field(default_factory=list)
    sanctions_screening_performed: bool = True
    sanctions_list_updated_at: Optional[float] = None
    sanctions_list_age_seconds: Optional[float] = None


class OnChainAnomalyMonitorEngine:
    """
    On-chain transaction anomaly monitor evaluating sanctions/OFAC blacklists, high-value transfer
    spikes, abnormal gas/priority fee spikes, and unapproved contract interactions for crypto
    custody security.

    The engine is stateless and deterministic: the only clock it reads is
    ``OnChainTxPayload.timestamp_utc``, so replaying a payload reproduces the
    verdict exactly.
    """

    def __init__(self, policy: OnChainRiskPolicy):
        if not isinstance(policy, OnChainRiskPolicy):
            raise OnChainMonitoringError(
                "policy is required and must be an OnChainRiskPolicy; there is no safe default "
                f"sanctions list to fall back on. Got {type(policy).__name__}."
            )
        self.policy = policy

    def audit_transaction(self, tx: OnChainTxPayload) -> OnChainMonitoringReport:
        """
        Audits a single on-chain transaction payload against the risk vectors and computes a
        composite risk score in [0, 100].

        A sanctions-list hit forces ``HIGH_RISK_BLOCK`` regardless of the score
        arithmetic: the prohibition does not scale with transaction value.
        """
        if not isinstance(tx, OnChainTxPayload):
            raise OnChainMonitoringError(
                f"audit_transaction expects an OnChainTxPayload, got {type(tx).__name__}."
            )

        policy = self.policy
        flags: List[RiskVectorFlag] = []
        score = 0

        # Vector 1: Sanctions / OFAC Blacklist Check (+80 points)
        matched: List[str] = []
        if policy.sanctions_screening_enabled:
            from_addr = normalize_evm_address(tx.from_address)
            to_addr = normalize_evm_address(tx.to_address)
            sanctioned = policy.normalized_sanctioned_addresses
            matched_sides: List[str] = []
            if from_addr in sanctioned:
                matched.append(from_addr)
                matched_sides.append(f"from='{tx.from_address}'")
            if to_addr in sanctioned:
                if to_addr != from_addr:
                    matched.append(to_addr)
                matched_sides.append(f"to='{tx.to_address}'")
            if matched:
                flags.append(RiskVectorFlag(
                    vector_name="BLACKLIST_INTERACTION",
                    score_penalty=PENALTY_BLACKLIST_INTERACTION,
                    severity="CRITICAL",
                    details=(
                        "Transaction interacts with a listed sanctioned address on the "
                        f"{', '.join(matched_sides)} side. Matched: {', '.join(matched)}. "
                        "Escalate for property-blocking and reporting review; a listed-address "
                        "match is not value-dependent."
                    ),
                ))
                score += PENALTY_BLACKLIST_INTERACTION

        # Vector 2: Sanctions list staleness (+30 points)
        list_age: Optional[float] = None
        if (
            policy.sanctions_screening_enabled
            and policy.sanctions_list_updated_at is not None
            and policy.max_sanctions_list_age_seconds is not None
        ):
            # A negative age means the snapshot post-dates the transaction
            # (replay of a historical payload) and is not a staleness condition.
            list_age = tx.timestamp_utc - policy.sanctions_list_updated_at
            if list_age > policy.max_sanctions_list_age_seconds:
                flags.append(RiskVectorFlag(
                    vector_name="SANCTIONS_LIST_STALE",
                    score_penalty=PENALTY_SANCTIONS_LIST_STALE,
                    severity="HIGH",
                    details=(
                        f"Sanctions snapshot is {list_age:,.0f}s old at transaction time, "
                        f"exceeding the {policy.max_sanctions_list_age_seconds:,.0f}s limit. "
                        "Designations and delistings since the snapshot are invisible to this "
                        "screen, in both directions."
                    ),
                ))
                score += PENALTY_SANCTIONS_LIST_STALE

        # Vector 3: High Value Transfer Spike (+40 points)
        if tx.value_usd > policy.max_transfer_usd:
            flags.append(RiskVectorFlag(
                vector_name="HIGH_VALUE_SPIKE",
                score_penalty=PENALTY_HIGH_VALUE_SPIKE,
                severity="HIGH",
                details=(
                    f"Transfer value ${tx.value_usd:,.2f} exceeds threshold limit "
                    f"${policy.max_transfer_usd:,.2f}."
                ),
            ))
            score += PENALTY_HIGH_VALUE_SPIKE

        # Vector 4: Abnormal Gas Price Spike (+20 points). The fixed ceiling and
        # the optional regime-relative baseline share one flag and one penalty,
        # so a transaction that trips both is not double-counted.
        gas_reasons: List[str] = []
        if tx.gas_price_gwei > policy.max_gas_gwei:
            gas_reasons.append(
                f"effective gas price {tx.gas_price_gwei:.1f} Gwei exceeds the fixed ceiling "
                f"{policy.max_gas_gwei:.1f} Gwei"
            )
        if policy.gas_baseline_gwei is not None:
            baseline_trigger = policy.gas_baseline_gwei * policy.gas_baseline_multiple
            if tx.gas_price_gwei > baseline_trigger:
                gas_reasons.append(
                    f"effective gas price {tx.gas_price_gwei:.1f} Gwei exceeds "
                    f"{policy.gas_baseline_multiple:g}x the {policy.gas_baseline_gwei:.1f} Gwei "
                    f"baseline ({baseline_trigger:.1f} Gwei)"
                )
        if gas_reasons:
            flags.append(RiskVectorFlag(
                vector_name="GAS_SPIKE_MEV",
                score_penalty=PENALTY_GAS_SPIKE_MEV,
                severity="MEDIUM",
                details="Gas anomaly: " + "; ".join(gas_reasons) + ".",
            ))
            score += PENALTY_GAS_SPIKE_MEV

        # Vector 5: Unapproved Smart Contract Function (+30 points), or a
        # categorically prohibited method (forces a block).
        blocking_method_hit = tx.method_signature in policy.blocking_methods
        if blocking_method_hit:
            flags.append(RiskVectorFlag(
                vector_name="BLOCKING_METHOD_CALL",
                score_penalty=PENALTY_BLOCKING_METHOD_CALL,
                severity="CRITICAL",
                details=(
                    f"Method signature '{tx.method_signature}' is categorically prohibited by "
                    "policy. Approval-granting calls in particular move no value, so the "
                    "high-value vector cannot see the exposure they create."
                ),
            ))
            score += PENALTY_BLOCKING_METHOD_CALL
        elif tx.method_signature == NATIVE_TRANSFER_SIGNATURE:
            # Empty calldata: there is no function call to approve. A
            # receive()/fallback() on a contract recipient is still reachable --
            # the sanctions and value vectors remain the controls that apply.
            pass
        elif (
            tx.method_signature == UNKNOWN_METHOD_SIGNATURE
            or tx.method_signature not in policy.whitelisted_methods
        ):
            if tx.method_signature == UNKNOWN_METHOD_SIGNATURE:
                detail = (
                    "Calldata was present but could not be decoded to a known selector; treated "
                    "as unapproved."
                )
            else:
                detail = f"Method signature '{tx.method_signature}' is not in whitelisted methods."
            flags.append(RiskVectorFlag(
                vector_name="UNAPPROVED_METHOD_CALL",
                score_penalty=PENALTY_UNAPPROVED_METHOD_CALL,
                severity="HIGH",
                details=detail,
            ))
            score += PENALTY_UNAPPROVED_METHOD_CALL

        final_score = min(MAX_RISK_SCORE, score)

        # A listed-address match or a categorically prohibited method blocks on
        # its own, independently of the score arithmetic, so re-tuning penalties
        # can never silently unblock either.
        if matched or blocking_method_hit or final_score >= BLOCK_SCORE_THRESHOLD:
            status = "HIGH_RISK_BLOCK"
            is_blocked = True
        elif final_score >= ANOMALY_SCORE_THRESHOLD:
            status = "ANOMALY_SUSPECTED"
            is_blocked = False
        else:
            status = "TRANSACTION_SAFE"
            is_blocked = False

        screening_note = (
            "" if policy.sanctions_screening_enabled
            else " SANCTIONS SCREENING DISABLED - this verdict is not a sanctions clearance."
        )
        notes = (
            f"ON-CHAIN TX AUDIT [{tx.tx_hash[:10]}... - {status}]: Composite Risk Score = "
            f"{final_score}/100. Flags Triggered = {len(flags)}.{screening_note}"
        )

        if is_blocked:
            logger.error("HIGH RISK ON-CHAIN ANOMALY DETECTED: %s", notes)
        elif status == "ANOMALY_SUSPECTED":
            logger.warning(notes)
        else:
            logger.info(notes)

        return OnChainMonitoringReport(
            tx_hash=tx.tx_hash,
            risk_score=final_score,
            risk_flags=flags,
            is_blocked=is_blocked,
            status=status,
            audit_notes=notes,
            matched_sanctioned_addresses=matched,
            sanctions_screening_performed=policy.sanctions_screening_enabled,
            sanctions_list_updated_at=policy.sanctions_list_updated_at,
            sanctions_list_age_seconds=list_age,
        )
