"""
multi-party-computation-mpc-custody-solutions: quorum authorisation gate for
threshold-signature (MPC/TSS) custody.

What this module is and is not
------------------------------
It is the **policy and audit gate that decides whether an MPC signing ceremony
may be started**, and it leaves an auditable record of the requests it refused.

It is **not** a threshold signature implementation, and it deliberately produces
**no signature and no signature-like value**. Threshold ECDSA is a multi-round
interactive protocol between the shard holders (CGGMP21 "CMP", GG18, GG20); a
policy layer cannot and must not synthesise its output. Two consequences follow,
and both are load-bearing:

* The engine never sees, receives, or stores private key-share material. Its
  input is an *attestation* per shard node carrying a **public** commitment, so
  the module is structurally incapable of reconstructing a key -- the failure
  mode this whole skill exists to prevent.
* A returned ``is_authorized=True`` means "the quorum policy is satisfied, start
  the ceremony in your MPC library". It is not evidence that a signature exists.

What is deliberately out of scope
---------------------------------
Value tiering, timelocks, and distinct-approver governance belong to
``multi-signature-approval-for-large-transfers``; destination allow-listing
belongs to ``exchange-withdrawal-whitelist-enforcement``. ``amount_usd`` and
``destination_address`` are carried here as **audit context only** and are never
used to allow or deny. Every report says so in ``warnings``.

Standards footing
-----------------
There is no NIST-approved threshold signature scheme. NIST IR 8214C, published
final in January 2026, is a *call for submissions*, not a standard, so the
threshold protocol itself cannot be FIPS-validated the way an HSM's module can.
Protocol choice therefore rests on peer-reviewed analysis plus the
implementation's disclosed vulnerability posture -- which is why this engine
requires an explicit hardening attestation and denies without one. See
``references/standards.md``.

Determinism
-----------
``evaluate_signing_authorization`` accepts an ``evaluation_date``. It defaults to
today only as a convenience; pass it explicitly for reproducible, auditable
output. The engine holds no mutable state and performs no I/O; mutating the
config object after constructing the engine is unsupported, because validation
runs once at construction.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

__all__ = [
    "MPCCustodyError",
    "MPCCustodyConfigError",
    "MPCSigningRequestError",
    "MPCShardNode",
    "MPCShardAttestation",
    "MPCCustodyConfig",
    "MPCSigningRequest",
    "MPCSigningAuthorizationReport",
    "MPCCustodyEngine",
    "derive_shard_attestation_commitment",
    "STATUS_AUTHORIZED",
    "STATUS_UNAUTHORIZED_NODE",
    "STATUS_DUPLICATE_ATTESTATION",
    "STATUS_KEY_EPOCH_MISMATCH",
    "STATUS_THRESHOLD_NOT_MET",
    "STATUS_FAILURE_DOMAIN_CONCENTRATION",
    "STATUS_PROTOCOL_NOT_HARDENED",
    "STATUS_KEY_REFRESH_OVERDUE",
    "PROTOCOL_CMP",
    "PROTOCOL_GG18",
    "PROTOCOL_GG20",
]

# --------------------------------------------------------------------------
# Protocols and their disclosed vulnerability posture
# --------------------------------------------------------------------------

#: CGGMP21 (Canetti-Gennaro-Goldfeder-Makriyannis-Peled, eprint 2021/060).
#: Two variants: 4-round (3-round presigning + 1-round online) and 7-round
#: (6-round presigning + 1-round online). Proactive refresh and identifiable
#: aborts are properties of the protocol itself.
PROTOCOL_CMP = "CMP"
#: Gennaro-Goldfeder 2018. 9 rounds of signing, no identifiable abort.
PROTOCOL_GG18 = "GG18"
#: Gennaro-Goldfeder 2020. 7 rounds, non-interactive online signing.
PROTOCOL_GG20 = "GG20"

SUPPORTED_PROTOCOLS = frozenset({PROTOCOL_CMP, PROTOCOL_GG18, PROTOCOL_GG20})

#: CVE-2023-33241 (disclosed 2023-08-09) is a flaw in the GG18/GG20
#: *specification*: parties do not check that a counterparty's Paillier modulus
#: is a biprime free of small factors, which lets a malicious party extract the
#: other parties' key shares. Every implementation of the unpatched pseudocode
#: is affected. CGGMP21 is not in scope of this CVE.
_CVE_2023_33241_AFFECTED = frozenset({PROTOCOL_GG18, PROTOCOL_GG20})

#: TSSHOCK (Verichains, Black Hat USA 2023) is a family of *implementation*
#: key-extraction attacks confirmed against GG18, GG20 **and CGGMP21**
#: libraries, several of which had already been audited. No protocol choice
#: exempts an operator from confirming their library is patched.
_TSSHOCK_AFFECTED = SUPPORTED_PROTOCOLS

# --------------------------------------------------------------------------
# Decision statuses, in the priority order the engine reports them
# --------------------------------------------------------------------------

STATUS_AUTHORIZED = "MPC_SIGNING_AUTHORIZED"
STATUS_UNAUTHORIZED_NODE = "MPC_UNAUTHORIZED_NODE"
STATUS_DUPLICATE_ATTESTATION = "MPC_DUPLICATE_SHARD_ATTESTATION"
STATUS_KEY_EPOCH_MISMATCH = "MPC_KEY_EPOCH_MISMATCH"
STATUS_THRESHOLD_NOT_MET = "MPC_THRESHOLD_NOT_MET"
STATUS_FAILURE_DOMAIN_CONCENTRATION = "MPC_FAILURE_DOMAIN_CONCENTRATION"
STATUS_PROTOCOL_NOT_HARDENED = "MPC_PROTOCOL_NOT_HARDENED"
STATUS_KEY_REFRESH_OVERDUE = "MPC_KEY_REFRESH_OVERDUE"

#: Request-level anomalies rank above standing configuration defects, because a
#: request that trips one is evidence about *this* ceremony.
_DENIAL_PRIORITY = (
    STATUS_UNAUTHORIZED_NODE,
    STATUS_DUPLICATE_ATTESTATION,
    STATUS_KEY_EPOCH_MISMATCH,
    STATUS_THRESHOLD_NOT_MET,
    STATUS_FAILURE_DOMAIN_CONCENTRATION,
    STATUS_PROTOCOL_NOT_HARDENED,
    STATUS_KEY_REFRESH_OVERDUE,
)

OUT_OF_SCOPE_WARNING = (
    "Value tiering, timelocks, and destination allow-listing are NOT enforced "
    "here: amount_usd and destination_address are audit context only. See "
    "multi-signature-approval-for-large-transfers and "
    "exchange-withdrawal-whitelist-enforcement."
)

_MIN_COMMITMENT_HEX_LEN = 32
_MAX_COMMITMENT_HEX_LEN = 128


class MPCCustodyError(ValueError):
    """Base error for invalid MPC custody configuration or signing input.

    Custody authorisation must fail loudly. A quorum policy built on impossible
    values (a threshold of zero, a shard roster with duplicate node ids) would
    authorise ceremonies it has no basis to authorise, so the engine raises
    instead of scoring.
    """


class MPCCustodyConfigError(MPCCustodyError):
    """Raised when the shard roster or quorum policy is internally inconsistent."""


class MPCSigningRequestError(MPCCustodyError):
    """Raised when a signing request or a shard attestation is malformed."""


def _as_date(value: date, field_name: str) -> date:
    """Normalise a ``date``-like audit input to a plain ``date``.

    ``datetime`` subclasses ``date``, but ``datetime - date`` raises ``TypeError``.
    Without this, passing a ``datetime`` would blow up deep inside the refresh
    check with an error that says nothing about what the caller did wrong.
    """
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise MPCCustodyError(f"{field_name} must be a date, got {value!r}.")
    return value


def derive_shard_attestation_commitment(
    public_share_commitment: str,
    key_epoch: int,
    tx_hash: str,
) -> str:
    """Derive the per-ceremony commitment a shard node puts in its attestation.

    ``public_share_commitment`` is the node's **public** key-share commitment
    (in threshold ECDSA each party's public share is public data by
    construction). It is *not* the secret share, and nothing secret may be
    passed here.

    The derivation is deterministic in the public share so that two hosts
    holding the *same* shard produce the *same* commitment -- that equality is
    what lets the engine detect a cloned shard masquerading as two independent
    parties. A node that instead emits a random per-ceremony nonce will not be
    caught by that check; see :class:`MPCCustodyEngine` for the limitation.

    Returns:
        A 64-character lowercase SHA-256 hex digest.

    Raises:
        MPCSigningRequestError: if any input is empty or the epoch is < 1.
    """
    if not public_share_commitment or not public_share_commitment.strip():
        raise MPCSigningRequestError("public_share_commitment must be non-empty.")
    if not tx_hash or not tx_hash.strip():
        raise MPCSigningRequestError("tx_hash must be non-empty.")
    if not isinstance(key_epoch, int) or isinstance(key_epoch, bool) or key_epoch < 1:
        raise MPCSigningRequestError(f"key_epoch must be an int >= 1, got {key_epoch!r}.")

    payload = "|".join((public_share_commitment.strip(), str(key_epoch), tx_hash.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class MPCShardNode:
    """One holder of a key share in the t-of-N group.

    ``failure_domain`` is the blast radius the operator assigns to this node --
    the cloud account, hosting provider, or physical operator whose compromise
    would yield control of it. Two nodes in different regions of the same cloud
    account share a failure domain; a node in a self-operated HSM does not.
    """

    node_id: str
    failure_domain: str
    #: Free-text description of the custodial role, carried into the audit trail.
    description: str = ""


@dataclass
class MPCShardAttestation:
    """A shard node's assertion that it is present and willing for this ceremony.

    This type has **no field for key-share material**, and that is deliberate: a
    policy layer that can receive shares is a policy layer that can be made to
    reconstruct a key.

    ``share_commitment`` is a non-secret hex commitment, normally produced by
    :func:`derive_shard_attestation_commitment`. ``key_epoch`` is the proactive
    secret sharing (PSS) refresh generation the node's share belongs to.
    """

    node_id: str
    share_commitment: str
    key_epoch: int


def _default_shard_roster() -> List["MPCShardNode"]:
    """Illustrative 3-node roster spanning three independent failure domains."""
    return [
        MPCShardNode("BOT_NODE_01", "aws-prod-account", "Trading bot signer"),
        MPCShardNode("CUSTODIAN_CLOUD_02", "custodian-saas", "Third-party custodian co-signer"),
        MPCShardNode("HSM_NODE_03", "on-prem-hsm", "Self-operated HSM shard"),
    ]


@dataclass
class MPCCustodyConfig:
    """Quorum policy and shard roster for one MPC-controlled wallet.

    ``N`` is derived from ``nodes`` rather than configured separately, so a
    roster and a shard count can never disagree.

    The two hardening flags default to ``False`` -- deny by default. An operator
    must positively attest that their MPC library carries the fixes; an engine
    that assumed patched software would authorise ceremonies on exactly the
    libraries that lost funds in 2023.
    """

    nodes: List[MPCShardNode] = field(default_factory=_default_shard_roster)
    threshold_t: int = 2
    protocol: str = PROTOCOL_CMP
    #: Current PSS refresh generation. Bump on every successful key refresh.
    current_key_epoch: int = 1
    last_key_refresh_date: Optional[date] = None
    #: Engineering default with no regulatory basis -- calibrate and record it.
    refresh_interval_days: float = 90.0
    #: Distinct failure domains an attesting quorum must span. Defaults to
    #: ``threshold_t``: if t shards share one domain, one compromise signs.
    min_distinct_failure_domains: Optional[int] = None
    implementation_hardened_against_cve_2023_33241: bool = False
    implementation_hardened_against_tsshock: bool = False
    #: Whether an overdue proactive refresh denies signing outright. Defaults to
    #: warn: hard-denying can strand a treasury over a hygiene item, and that
    #: trade-off belongs to the operator, not to this library.
    deny_on_overdue_refresh: bool = False


@dataclass
class MPCSigningRequest:
    """One transaction awaiting MPC quorum authorisation.

    ``amount_usd`` and ``destination_address`` are **audit context only**. This
    engine never allows or denies on them -- value tiering belongs to
    ``multi-signature-approval-for-large-transfers`` and destination
    allow-listing to ``exchange-withdrawal-whitelist-enforcement``. They are
    required so the audit record of a denial is complete.
    """

    tx_hash: str
    amount_usd: float
    destination_address: str
    attestations: List[MPCShardAttestation]


@dataclass
class MPCSigningAuthorizationReport:
    """Auditable decision on whether an MPC signing ceremony may begin.

    There is deliberately **no** ``signature_r`` / ``signature_s`` on this type.
    This engine authorises a ceremony; the MPC library produces the signature.
    """

    tx_hash: str
    is_authorized: bool
    status: str
    protocol: str
    threshold_required: int
    num_shards: int
    accepted_attestation_count: int
    accepted_nodes: List[str]
    distinct_failure_domains: int
    key_epoch: int
    evaluation_date: date
    amount_usd: float
    destination_address: str
    findings: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    audit_notes: str = ""


class MPCCustodyEngine:
    """Quorum authorisation gate for threshold-signature (MPC/TSS) custody.

    Checks a signing request against the shard roster and quorum policy and
    returns an auditable authorisation decision. It never touches key-share
    material and never emits a signature.

    Known limitations, stated rather than hidden:

    * Cloned-shard detection compares ``share_commitment`` values for equality.
      It catches a shard restored onto a second host only when nodes derive the
      commitment deterministically from their public share (see
      :func:`derive_shard_attestation_commitment`). A node emitting a random
      value per ceremony evades it.
    * Attestations are trusted as authenticated by the transport. This engine
      does not verify node identity; mutual TLS or signed attestations must sit
      underneath it.
    * The engine is stateless and performs no replay detection. A ``tx_hash``
      may be authorised twice; idempotency belongs to the caller.
    """

    def __init__(self, config: Optional[MPCCustodyConfig] = None) -> None:
        self.config = config or MPCCustodyConfig()
        self._validate_config(self.config)
        self._nodes_by_id: Dict[str, MPCShardNode] = {
            node.node_id: node for node in self.config.nodes
        }
        self._num_shards: int = len(self.config.nodes)
        self._min_domains: int = (
            self.config.min_distinct_failure_domains
            if self.config.min_distinct_failure_domains is not None
            else self.config.threshold_t
        )

    # -- configuration -----------------------------------------------------

    @staticmethod
    def _validate_config(config: MPCCustodyConfig) -> None:
        """Reject a quorum policy that cannot mean what it appears to mean."""
        if config.protocol not in SUPPORTED_PROTOCOLS:
            raise MPCCustodyConfigError(
                f"Unsupported protocol {config.protocol!r}; "
                f"expected one of {sorted(SUPPORTED_PROTOCOLS)}."
            )

        nodes = list(config.nodes)
        if len(nodes) < 3:
            raise MPCCustodyConfigError(
                f"MPC custody requires N >= 3 shard nodes, got {len(nodes)}. "
                "A smaller group leaves no independent third party and no "
                "surviving shard when one is lost."
            )

        node_ids = [node.node_id for node in nodes]
        if any(not node_id or not node_id.strip() for node_id in node_ids):
            raise MPCCustodyConfigError("Every shard node requires a non-empty node_id.")
        padded = [node_id for node_id in node_ids if node_id != node_id.strip()]
        if padded:
            raise MPCCustodyConfigError(
                f"Shard node_id(s) {padded!r} carry surrounding whitespace. Node ids are "
                "matched verbatim against attestations, so a padded id would silently "
                "deny every ceremony its holder takes part in."
            )
        if len(set(node_ids)) != len(node_ids):
            raise MPCCustodyConfigError(
                f"Duplicate node_id in shard roster: {sorted(node_ids)}. "
                "Each shard must be held by exactly one distinct party."
            )
        if any(not node.failure_domain or not node.failure_domain.strip() for node in nodes):
            raise MPCCustodyConfigError(
                "Every shard node requires a non-empty failure_domain; an "
                "unassigned blast radius cannot be checked for concentration."
            )

        threshold = config.threshold_t
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            raise MPCCustodyConfigError(f"threshold_t must be an int, got {threshold!r}.")
        if threshold < 2:
            raise MPCCustodyConfigError(
                f"threshold_t must be >= 2, got {threshold}. A threshold of 1 lets a "
                "single compromised shard sign, and a threshold of 0 would authorise "
                "a ceremony with no participants at all."
            )
        if threshold > len(nodes):
            raise MPCCustodyConfigError(
                f"threshold_t ({threshold}) exceeds the {len(nodes)} configured shard "
                "nodes; the quorum can never be met."
            )

        epoch = config.current_key_epoch
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise MPCCustodyConfigError(f"current_key_epoch must be an int, got {epoch!r}.")
        if epoch < 1:
            raise MPCCustodyConfigError(f"current_key_epoch must be >= 1, got {epoch}.")

        interval = config.refresh_interval_days
        if not isinstance(interval, (int, float)) or isinstance(interval, bool):
            raise MPCCustodyConfigError(
                f"refresh_interval_days must be numeric, got {interval!r}."
            )
        if not math.isfinite(interval) or interval <= 0:
            raise MPCCustodyConfigError(
                f"refresh_interval_days must be finite and > 0, got {interval!r}."
            )

        min_domains = config.min_distinct_failure_domains
        if min_domains is not None:
            if not isinstance(min_domains, int) or isinstance(min_domains, bool):
                raise MPCCustodyConfigError(
                    f"min_distinct_failure_domains must be an int, got {min_domains!r}."
                )
            if not 1 <= min_domains <= threshold:
                raise MPCCustodyConfigError(
                    f"min_distinct_failure_domains ({min_domains}) must be between 1 "
                    f"and threshold_t ({threshold}); requiring more distinct domains "
                    "than signers are needed makes every quorum unsatisfiable."
                )

    @property
    def num_shards(self) -> int:
        """N -- the number of shard nodes, snapshotted at construction."""
        return self._num_shards

    # -- request validation ------------------------------------------------

    def _validate_request(self, request: MPCSigningRequest) -> None:
        """Reject a malformed request rather than counting it toward a quorum."""
        if not request.tx_hash or not request.tx_hash.strip():
            raise MPCSigningRequestError("tx_hash must be non-empty.")
        if not request.destination_address or not request.destination_address.strip():
            raise MPCSigningRequestError(
                "destination_address must be non-empty; it is recorded in the audit "
                "trail even though it is not enforced here."
            )
        if not isinstance(request.amount_usd, (int, float)) or isinstance(request.amount_usd, bool):
            raise MPCSigningRequestError(
                f"amount_usd must be numeric, got {request.amount_usd!r}."
            )
        if not math.isfinite(request.amount_usd) or request.amount_usd < 0:
            raise MPCSigningRequestError(
                f"amount_usd must be finite and >= 0, got {request.amount_usd!r}."
            )
        if not request.attestations:
            raise MPCSigningRequestError(
                "At least one shard attestation is required; an empty ceremony must "
                "never reach a threshold check."
            )

        for attestation in request.attestations:
            self._validate_attestation(attestation)

    @staticmethod
    def _validate_attestation(attestation: MPCShardAttestation) -> None:
        if not attestation.node_id or not attestation.node_id.strip():
            raise MPCSigningRequestError("Every attestation requires a non-empty node_id.")

        commitment = (attestation.share_commitment or "").strip()
        if not commitment:
            raise MPCSigningRequestError(
                f"Attestation from {attestation.node_id!r} carries an empty "
                "share_commitment; an absent commitment must never be counted "
                "toward a quorum."
            )
        if not _MIN_COMMITMENT_HEX_LEN <= len(commitment) <= _MAX_COMMITMENT_HEX_LEN:
            raise MPCSigningRequestError(
                f"Attestation from {attestation.node_id!r} has a share_commitment of "
                f"{len(commitment)} chars; expected {_MIN_COMMITMENT_HEX_LEN}-"
                f"{_MAX_COMMITMENT_HEX_LEN} hex chars."
            )
        try:
            int(commitment, 16)
        except ValueError as exc:
            raise MPCSigningRequestError(
                f"Attestation from {attestation.node_id!r} has a non-hex "
                "share_commitment."
            ) from exc

        epoch = attestation.key_epoch
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise MPCSigningRequestError(
                f"Attestation from {attestation.node_id!r} has a non-integer key_epoch "
                f"{epoch!r}."
            )
        if epoch < 1:
            raise MPCSigningRequestError(
                f"Attestation from {attestation.node_id!r} has key_epoch {epoch}; "
                "epochs start at 1."
            )

    # -- evaluation --------------------------------------------------------

    def evaluate_signing_authorization(
        self,
        request: MPCSigningRequest,
        evaluation_date: Optional[date] = None,
    ) -> MPCSigningAuthorizationReport:
        """Decide whether the MPC signing ceremony for ``request`` may begin.

        Every check runs and every failure is reported. ``status`` names the
        highest-priority failure (see ``_DENIAL_PRIORITY``) so an alert can be
        routed on one field without losing the rest of the audit trail.

        Args:
            request: the ceremony to authorise.
            evaluation_date: audit date. Defaults to today; pass it explicitly
                for reproducible output.

        Returns:
            An :class:`MPCSigningAuthorizationReport`. ``is_authorized=True``
            means "start the ceremony", never "a signature exists".

        Raises:
            MPCSigningRequestError: if the request or an attestation is malformed.
        """
        self._validate_request(request)
        eval_date = (
            _as_date(evaluation_date, "evaluation_date")
            if evaluation_date is not None else date.today()
        )
        config = self.config

        findings: List[str] = []
        warnings: List[str] = [OUT_OF_SCOPE_WARNING]
        failures: Set[str] = set()

        # 1. Roster membership. Removing a party from a t-of-N group requires a
        #    resharing, not an allowlist edit, so an attestation from outside the
        #    roster is either a misconfiguration or an adversary probing the
        #    ceremony. Neither is safe to resolve silently at signing time.
        unauthorized = sorted(
            {a.node_id for a in request.attestations if a.node_id not in self._nodes_by_id}
        )
        if unauthorized:
            failures.add(STATUS_UNAUTHORIZED_NODE)
            findings.append(
                f"Attestation from node(s) outside the shard roster: {unauthorized}. "
                f"Authorised shards: {sorted(self._nodes_by_id)}."
            )

        roster_attestations = [
            a for a in request.attestations if a.node_id in self._nodes_by_id
        ]

        # 2. One shard, one vote. A repeated node_id is a double-count attempt; a
        #    repeated commitment across *different* node ids means one shard is
        #    running on two hosts, so the quorum is not t independent parties.
        seen_nodes: Set[str] = set()
        duplicate_nodes: Set[str] = set()
        for attestation in roster_attestations:
            if attestation.node_id in seen_nodes:
                duplicate_nodes.add(attestation.node_id)
            seen_nodes.add(attestation.node_id)
        if duplicate_nodes:
            failures.add(STATUS_DUPLICATE_ATTESTATION)
            findings.append(
                f"Node(s) {sorted(duplicate_nodes)} submitted more than one attestation "
                "for this ceremony; each shard carries exactly one vote."
            )

        commitment_owners: Dict[str, Set[str]] = {}
        for attestation in roster_attestations:
            key = attestation.share_commitment.strip().lower()
            commitment_owners.setdefault(key, set()).add(attestation.node_id)
        cloned = sorted(
            sorted(owners) for owners in commitment_owners.values() if len(owners) > 1
        )
        if cloned:
            failures.add(STATUS_DUPLICATE_ATTESTATION)
            findings.append(
                f"Identical share_commitment presented by distinct node ids {cloned}: "
                "one key share appears to be running on more than one host, so the "
                "quorum does not represent t independent parties."
            )

        # 3. PSS epoch. A refresh invalidates every prior share, so a mixed-epoch
        #    quorum means a stale shard is participating and the ceremony would
        #    abort inside the protocol anyway.
        stale = sorted(
            {a.node_id for a in roster_attestations if a.key_epoch != config.current_key_epoch}
        )
        if stale:
            failures.add(STATUS_KEY_EPOCH_MISMATCH)
            findings.append(
                f"Node(s) {stale} attested on a key epoch other than the current epoch "
                f"{config.current_key_epoch}; shares from a superseded refresh "
                "generation cannot participate."
            )

        accepted = [
            a for a in roster_attestations
            if a.node_id not in duplicate_nodes
            and a.key_epoch == config.current_key_epoch
            and len(commitment_owners[a.share_commitment.strip().lower()]) == 1
        ]
        accepted_nodes = sorted({a.node_id for a in accepted})
        accepted_count = len(accepted_nodes)

        # 4. Threshold quorum.
        if accepted_count < config.threshold_t:
            failures.add(STATUS_THRESHOLD_NOT_MET)
            findings.append(
                f"Quorum not met: {accepted_count} accepted attestation(s) against a "
                f"threshold of {config.threshold_t}-of-{self.num_shards} "
                f"(protocol {config.protocol})."
            )

        # 5. Failure-domain independence. If t shards share a blast radius, the
        #    threshold buys nothing against a compromise of that domain.
        domains = sorted({self._nodes_by_id[nid].failure_domain for nid in accepted_nodes})
        if accepted_count >= config.threshold_t and len(domains) < self._min_domains:
            failures.add(STATUS_FAILURE_DOMAIN_CONCENTRATION)
            findings.append(
                f"Attesting quorum spans {len(domains)} failure domain(s) {domains} but "
                f"{self._min_domains} are required: a single compromise of one domain "
                "would yield a signing quorum."
            )

        # 6. Implementation hardening. Protocol choice alone is not a security
        #    posture; both 2023 attack families targeted deployed libraries.
        if (config.protocol in _CVE_2023_33241_AFFECTED
                and not config.implementation_hardened_against_cve_2023_33241):
            failures.add(STATUS_PROTOCOL_NOT_HARDENED)
            findings.append(
                f"{config.protocol} is affected by CVE-2023-33241 (counterparty Paillier "
                "modulus not validated as a small-factor-free biprime, permitting full "
                "key extraction) and no mitigation is attested. Confirm the library "
                "validates Paillier keys via zero-knowledge proof, then set "
                "implementation_hardened_against_cve_2023_33241."
            )
        if (config.protocol in _TSSHOCK_AFFECTED
                and not config.implementation_hardened_against_tsshock):
            failures.add(STATUS_PROTOCOL_NOT_HARDENED)
            findings.append(
                f"No TSSHOCK mitigation attested for {config.protocol}. Verichains "
                "demonstrated key extraction against audited GG18, GG20 and CGGMP21 "
                "libraries in 1-2 signing ceremonies, so choosing CMP does not exempt "
                "an implementation. Confirm the patched library version, then set "
                "implementation_hardened_against_tsshock."
            )

        # 7. Proactive refresh cadence -- hygiene, denying only on request.
        refresh_note = self._assess_refresh(eval_date)
        if refresh_note is not None:
            if config.deny_on_overdue_refresh:
                failures.add(STATUS_KEY_REFRESH_OVERDUE)
                findings.append(refresh_note)
            else:
                warnings.append(refresh_note)

        if config.threshold_t == self.num_shards:
            warnings.append(
                f"threshold_t == N ({config.threshold_t}-of-{self.num_shards}): the group "
                "has no fault tolerance, and the permanent loss of any single shard "
                "makes the wallet unspendable."
            )

        status = next((s for s in _DENIAL_PRIORITY if s in failures), STATUS_AUTHORIZED)
        is_authorized = not failures

        if is_authorized:
            notes = (
                f"MPC SIGNING AUTHORIZED [{request.tx_hash}]: quorum {accepted_count}/"
                f"{config.threshold_t} of {self.num_shards} across {len(domains)} failure "
                f"domain(s) at key epoch {config.current_key_epoch} "
                f"(protocol {config.protocol}). Start the ceremony in the MPC library; "
                "no signature is produced here."
            )
            logger.info("%s", notes)
        else:
            notes = (
                f"MPC SIGNING DENIED [{request.tx_hash}] status={status}: "
                + " ".join(findings)
            )
            logger.critical("%s", notes)

        return MPCSigningAuthorizationReport(
            tx_hash=request.tx_hash,
            is_authorized=is_authorized,
            status=status,
            protocol=config.protocol,
            threshold_required=config.threshold_t,
            num_shards=self.num_shards,
            accepted_attestation_count=accepted_count,
            accepted_nodes=accepted_nodes,
            distinct_failure_domains=len(domains),
            key_epoch=config.current_key_epoch,
            evaluation_date=eval_date,
            amount_usd=float(request.amount_usd),
            destination_address=request.destination_address,
            findings=findings,
            warnings=warnings,
            audit_notes=notes,
        )

    def _assess_refresh(self, eval_date: date) -> Optional[str]:
        """Return a finding if proactive key refresh is unverifiable or overdue."""
        config = self.config
        if config.last_key_refresh_date is None:
            return (
                "No last_key_refresh_date recorded: proactive secret sharing cadence is "
                "unverifiable. Without periodic refresh an attacker can collect shards "
                "across an unbounded window."
            )
        last_refresh = _as_date(config.last_key_refresh_date, "last_key_refresh_date")
        elapsed_days = (eval_date - last_refresh).days
        if elapsed_days < 0:
            return (
                f"last_key_refresh_date ({last_refresh.isoformat()}) is "
                f"after evaluation_date ({eval_date.isoformat()}): the refresh record is "
                "inconsistent and cannot be relied on."
            )
        if elapsed_days > config.refresh_interval_days:
            return (
                f"Proactive key refresh overdue: {elapsed_days} days since "
                f"{last_refresh.isoformat()} against a "
                f"{config.refresh_interval_days:.0f}-day policy."
            )
        return None
