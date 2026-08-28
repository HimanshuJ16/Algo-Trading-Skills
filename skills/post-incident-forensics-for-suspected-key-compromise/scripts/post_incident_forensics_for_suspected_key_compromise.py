"""Post-incident forensics for a suspected cryptographic key compromise.

Correlates off-chain key-access logs (KMS / API gateway / signer) against an
independently-held access-control policy, classifies on-chain outflows from the
affected wallet relative to the suspected leak time, seals the analysed evidence
set under a SHA-256 digest, and emits a containment mandate.

Design commitments, and the reasons for them:

- **The digest covers the evidence, not a summary of it.** ``evidence_sha256``
  is computed over a canonical JSON manifest containing *every field of every
  record* the analysis consumed, plus the incident and chain-of-custody
  metadata. Hashing only record counts would leave every IP, timestamp, action
  and amount silently mutable under an unchanged "integrity" hash. RFC 3227
  s3.2 recommends generating checksums over the *collected evidence*.

- **The manifest digest is not the acquisition digest.** This engine sees
  parsed records, not the original artifact bytes. Hash the raw log files at
  acquisition and pass those digests as ``source_artifact_digests``; they are
  carried into the report and into the manifest, and their absence is itself a
  reported finding. RFC 3227 s4.1 requires custody documentation (who, when,
  where, how) -- hence ``custodian`` and ``collected_at`` are mandatory.

- **Absence of evidence is never reported as absence of compromise.** Empty
  logs, or a log window that does not span ``suspected_leak_time``, yield
  ``INSUFFICIENT_EVIDENCE``, not ``NO_EVIDENCE_OF_COMPROMISE``. NIST SP 800-57
  Part 1 Rev. 5 s5.5.2: "The worst form of key compromise is one that is not
  detected."

- **This engine is invoked on an already-suspected compromise.** It therefore
  clears a key only on clean evidence that actually covers the suspected leak
  time; on anything else it mandates containment. That asymmetry is deliberate.

- **The IP allowlist is policy, not log content.** It is supplied on
  ``AccessControlPolicy``, independently of the records under audit. An
  allowlist carried on each log record is written by whoever can write the log
  -- an attacker who can append a record can append themselves into its
  allowlist and vanish from the report.

- **A successful access and a rejected attempt are different findings.** NYDFS
  23 NYCRR 500.1(f) defines a *cybersecurity event* as any act "or attempt,
  successful or unsuccessful"; the 72-hour s500.17(a) notice is keyed to a
  *cybersecurity incident*. Collapsing a blocked 403 probe into the same
  counter as a successful ``EXPORT_KEY`` destroys that distinction and inverts
  triage priority.

- **An authorized source IP does not clear a key-material action.** A stolen
  session or an insider operates from an allowlisted host. Successful
  privileged actions are reported even when the source IP passes the allowlist.

- **Amounts are per-asset ``Decimal``.** Summing 50 ETH and 3 BTC into one
  "total exfiltrated" float is dimensionally meaningless, and binary floats
  cannot hold 0.1 ETH exactly. This figure feeds insurance claims and
  materiality assessments, so it is exact and it is keyed by asset.

- **Not every outflow is exfiltration.** Transfers to policy-authorized
  destinations, and transfers that completed before the suspected leak time,
  are counted separately. Attribution is fail-closed: a transfer is treated as
  pre-incident only if it precedes the leak time by more than the recorded
  clock offset (RFC 3227 s3.2 requires recording clock drift).

- **The engine is deterministic and has no clock of its own.**
  ``analysis_time`` is supplied by the caller, so a stored evidence set replays
  to an identical report and an identical evidence digest.

- **The engine computes no legal deadlines.** Regulatory notification duties are
  jurisdiction- and entity-specific; see ``references/standards.md``. The
  containment mandate is an operational instruction, not a compliance filing.

- **Key material never enters the report.** NIST SP 800-57 Part 1 Rev. 5 s8.3.5:
  a revocation notice should identify the key "excluding the key itself".
  Identifiers that look like raw key material are rejected at construction.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

EVIDENCE_MANIFEST_SCHEMA = "key-forensics-evidence-v2"
EVIDENCE_HASH_ALGORITHM = "sha256"          # FIPS 180-4 SHA-256
SHA256_HEX_LENGTH = 64

# --- Report statuses -------------------------------------------------------
STATUS_CONFIRMED = "KEY_COMPROMISE_CONFIRMED"
STATUS_SUSPECTED = "COMPROMISE_SUSPECTED"
STATUS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
STATUS_NO_EVIDENCE = "NO_EVIDENCE_OF_COMPROMISE"

# --- Finding categories ----------------------------------------------------
FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS = "UNAUTHORIZED_SUCCESSFUL_ACCESS"
FINDING_UNAUTHORIZED_ACCESS_ATTEMPT = "UNAUTHORIZED_ACCESS_ATTEMPT"
FINDING_PRIVILEGED_ACTION_FROM_AUTHORIZED_IP = "PRIVILEGED_ACTION_FROM_AUTHORIZED_IP"
FINDING_UNAUTHORIZED_OUTFLOW = "UNAUTHORIZED_OUTFLOW"
FINDING_EVIDENCE_GAP = "EVIDENCE_GAP"
FINDING_NO_SOURCE_ARTIFACT_DIGESTS = "NO_SOURCE_ARTIFACT_DIGESTS"
FINDING_DERIVED_KEY_EXPOSURE = "DERIVED_KEY_EXPOSURE"
FINDING_CLOCK_OFFSET_RECORDED = "CLOCK_OFFSET_RECORDED"
FINDING_DUPLICATE_TRANSFER_RECORDS = "DUPLICATE_TRANSFER_RECORDS"

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"

# Actions that expose the key material itself or its protection. Membership
# decides whether a *successful* action is reported even when its source IP is
# allowlisted. Engineering default -- override with this signer's own action
# names.
#
# Deliberately excludes SIGN_TRANSACTION and DECRYPT: those *use* the key, they
# do not expose it, and a trading bot performs them constantly. Including them
# would raise a HIGH finding on every routine signature and pin the status at
# COMPROMISE_SUSPECTED forever, which is indistinguishable from no signal.
DEFAULT_PRIVILEGED_ACTIONS = frozenset({
    "EXPORT_KEY",
    "WRAP_KEY",
    "UNWRAP_KEY",
    "PUT_KEY_POLICY",
    "SCHEDULE_KEY_DELETION",
})

# Raw private-key spellings that must never be passed as an identifier: 32-byte
# and 64-byte hex, with or without an 0x prefix.
_KEY_MATERIAL_RE = re.compile(r"\A(?:0x)?(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{128})\Z")
_HEX_ADDRESS_RE = re.compile(r"\A0x[0-9a-fA-F]+\Z")


class KeyForensicsError(ValueError):
    """Raised on an invalid forensic input, policy, or evidence set."""


# --- Validation helpers ----------------------------------------------------

def _require_token(value: Any, name: str, *, max_length: int = 512) -> str:
    """Validates an identifier token: a non-empty string with no interior whitespace.

    Interior whitespace is rejected rather than stripped: no legitimate key id,
    chain address, transaction hash or action name contains it, and a newline
    inside one would forge line breaks into the audit record this engine's
    findings are evidenced from.
    """
    if not isinstance(value, str) or not value.strip():
        raise KeyForensicsError(f"{name} must be a non-empty string, got {value!r}.")
    token = value.strip()
    if len(token) > max_length:
        raise KeyForensicsError(f"{name} must be at most {max_length} characters.")
    if any(ch.isspace() for ch in token):
        raise KeyForensicsError(f"{name} must not contain interior whitespace, got {value!r}.")
    return token


def _reject_key_material(token: str, name: str) -> str:
    """Rejects a token that looks like a raw private key.

    A forensic report is copied into tickets, insurance packs and log
    aggregators. NIST SP 800-57 Part 1 Rev. 5 s8.3.5 requires a revocation
    notice to identify the key *excluding the key itself*.
    """
    if _KEY_MATERIAL_RE.match(token):
        raise KeyForensicsError(
            f"{name} looks like raw key material (64/128 hex characters). Pass a key "
            "identifier, never the key itself. See NIST SP 800-57 Pt.1 Rev.5 s8.3.5."
        )
    return token


def normalize_chain_address(value: Any, name: str = "address") -> str:
    """Normalizes a blockchain address for comparison.

    Hex (``0x``-prefixed) addresses are lower-cased: EIP-55 checksummed and
    all-lowercase spellings denote the same EVM account. Every other encoding is
    returned trimmed but otherwise untouched, because Base58 (BTC, TRON) and
    Bech32 addresses are case-sensitive and lower-casing them changes or
    invalidates the address.
    """
    token = _require_token(value, name)
    return token.lower() if _HEX_ADDRESS_RE.match(token) else token


def parse_utc_timestamp(value: Any, name: str) -> datetime:
    """Parses an ISO-8601 timestamp and requires it to be timezone-aware.

    A naive timestamp cannot be ordered against the suspected leak time without
    guessing an offset, and guessing wrong reclassifies exfiltration as routine
    treasury activity. Rejected rather than assumed to be UTC.
    """
    token = _require_token(value, name, max_length=64)
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError as exc:
        raise KeyForensicsError(
            f"{name} must be an ISO-8601 timestamp (e.g. '2026-07-31T12:00:00Z'), got {value!r}."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KeyForensicsError(
            f"{name} must carry a UTC offset (e.g. '...Z' or '...+00:00'), got {value!r}."
        )
    return parsed.astimezone(timezone.utc)


def parse_decimal_amount(value: Any, name: str) -> Decimal:
    """Parses an exact non-negative asset amount.

    ``float`` is rejected: 0.1 has no exact binary representation, and this
    figure is quantified for an insurance claim and a materiality assessment.
    Pass the decimal string (or integer base units) straight from the RPC or
    indexer response.
    """
    if isinstance(value, float):
        raise KeyForensicsError(
            f"{name} must not be a float (binary floats cannot represent decimal asset "
            f"amounts exactly). Pass a decimal string such as '0.1', got {value!r}."
        )
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise KeyForensicsError(f"{name} must be a Decimal, int or decimal string, got {value!r}.")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise KeyForensicsError(f"{name} must be a decimal amount, got {value!r}.") from exc
    if not amount.is_finite():
        # NaN defeats every comparison and propagates silently through a sum.
        raise KeyForensicsError(f"{name} must be finite, got {value!r}.")
    if amount < 0:
        raise KeyForensicsError(f"{name} must be >= 0, got {amount}.")
    return amount


def _require_sha256_hex(value: Any, name: str) -> str:
    token = _require_token(value, name, max_length=SHA256_HEX_LENGTH)
    lowered = token.lower()
    if len(lowered) != SHA256_HEX_LENGTH or any(c not in "0123456789abcdef" for c in lowered):
        raise KeyForensicsError(f"{name} must be a 64-character SHA-256 hex digest, got {value!r}.")
    return lowered


def _require_str_tuple(values: Any, name: str) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise KeyForensicsError(f"{name} must be an iterable of strings, got {values!r}.")
    return tuple(_require_token(v, f"{name} entry") for v in values)


# --- Evidence inputs -------------------------------------------------------

@dataclass(frozen=True)
class KeyCompromiseIncident:
    """The incident under investigation, plus its RFC 3227 s4.1 custody record."""

    key_id: str
    wallet_address: str
    suspected_leak_time: str                  # ISO-8601, timezone-aware
    affected_systems: Sequence[str]
    custodian: str                            # who collected the evidence
    collected_at: str                         # ISO-8601, timezone-aware
    derived_key_ids: Sequence[str] = ()       # keys sharing a seed/HSM with key_id
    clock_offset_seconds: float = 0.0         # source-system clock minus UTC
    source_artifact_digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_id", _reject_key_material(
            _require_token(self.key_id, "key_id"), "key_id"))
        object.__setattr__(self, "wallet_address",
                           normalize_chain_address(self.wallet_address, "wallet_address"))
        object.__setattr__(self, "custodian", _require_token(self.custodian, "custodian"))
        parse_utc_timestamp(self.suspected_leak_time, "suspected_leak_time")
        parse_utc_timestamp(self.collected_at, "collected_at")

        systems = _require_str_tuple(self.affected_systems, "affected_systems")
        if not systems:
            raise KeyForensicsError("affected_systems must name at least one system.")
        object.__setattr__(self, "affected_systems", systems)

        derived = tuple(
            _reject_key_material(k, "derived_key_ids entry")
            for k in _require_str_tuple(self.derived_key_ids, "derived_key_ids")
        )
        if self.key_id in derived:
            raise KeyForensicsError("derived_key_ids must not repeat key_id.")
        object.__setattr__(self, "derived_key_ids", derived)

        if isinstance(self.clock_offset_seconds, bool) or not isinstance(
                self.clock_offset_seconds, (int, float)):
            raise KeyForensicsError("clock_offset_seconds must be a real number.")
        offset = float(self.clock_offset_seconds)
        if not math.isfinite(offset):
            raise KeyForensicsError("clock_offset_seconds must be finite.")
        object.__setattr__(self, "clock_offset_seconds", offset)

        if not isinstance(self.source_artifact_digests, Mapping):
            raise KeyForensicsError("source_artifact_digests must be a mapping of name -> digest.")
        digests = {
            _require_token(k, "source_artifact_digests key"): _require_sha256_hex(
                v, "source_artifact_digests value")
            for k, v in self.source_artifact_digests.items()
        }
        object.__setattr__(self, "source_artifact_digests", dict(sorted(digests.items())))

    @property
    def leak_time(self) -> datetime:
        return parse_utc_timestamp(self.suspected_leak_time, "suspected_leak_time")


@dataclass(frozen=True)
class KeyAccessLogEntry:
    """One KMS / signer / API-gateway access record, as collected."""

    timestamp: str                            # ISO-8601, timezone-aware
    ip_address: str
    action: str
    status_code: int
    principal: str = "UNKNOWN_PRINCIPAL"

    def __post_init__(self) -> None:
        parse_utc_timestamp(self.timestamp, "timestamp")
        object.__setattr__(self, "action", _require_token(self.action, "action"))
        object.__setattr__(self, "principal", _require_token(self.principal, "principal"))
        raw_ip = _require_token(self.ip_address, "ip_address", max_length=64)
        try:
            parsed = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise KeyForensicsError(
                f"ip_address must be a valid IP address, got {raw_ip!r}."
            ) from exc
        # ::ffff:192.0.2.1 and 192.0.2.1 are the same host; compare the IPv4 form
        # so an allowlist written in one notation matches traffic in the other.
        mapped = getattr(parsed, "ipv4_mapped", None)
        object.__setattr__(self, "ip_address", str(mapped or parsed))
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise KeyForensicsError(f"status_code must be an int, got {self.status_code!r}.")
        if not 100 <= self.status_code <= 599:
            raise KeyForensicsError(
                f"status_code must be an HTTP status 100-599, got {self.status_code}."
            )

    @property
    def at(self) -> datetime:
        return parse_utc_timestamp(self.timestamp, "timestamp")

    @property
    def succeeded(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(frozen=True)
class OnChainTransfer:
    """One on-chain value transfer observed for the affected wallet."""

    tx_hash: str
    from_address: str
    to_address: str
    amount: Any                               # Decimal | int | decimal str -- never float
    asset_symbol: str
    timestamp: str                            # ISO-8601, timezone-aware

    def __post_init__(self) -> None:
        object.__setattr__(self, "tx_hash", _require_token(self.tx_hash, "tx_hash"))
        object.__setattr__(self, "from_address",
                           normalize_chain_address(self.from_address, "from_address"))
        object.__setattr__(self, "to_address",
                           normalize_chain_address(self.to_address, "to_address"))
        object.__setattr__(self, "asset_symbol",
                           _require_token(self.asset_symbol, "asset_symbol", max_length=32).upper())
        object.__setattr__(self, "amount", parse_decimal_amount(self.amount, "amount"))
        parse_utc_timestamp(self.timestamp, "timestamp")

    @property
    def at(self) -> datetime:
        return parse_utc_timestamp(self.timestamp, "timestamp")


@dataclass(frozen=True)
class AccessControlPolicy:
    """The allowlists, held independently of the logs being audited."""

    authorized_networks: Sequence[str]        # IPs or CIDR blocks
    authorized_destinations: Sequence[str] = ()
    privileged_actions: FrozenSet[str] = DEFAULT_PRIVILEGED_ACTIONS

    def __post_init__(self) -> None:
        entries = _require_str_tuple(self.authorized_networks, "authorized_networks")
        if not entries:
            # An empty allowlist marks every access unauthorized, drowning the
            # real finding in noise; it is a misconfiguration, not a policy.
            raise KeyForensicsError(
                "authorized_networks must contain at least one IP or CIDR block."
            )
        networks = []
        for entry in entries:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise KeyForensicsError(
                    f"authorized_networks entry must be an IP or CIDR block, got {entry!r}."
                ) from exc
            if network.prefixlen == 0:
                # An all-addresses block authorizes the attacker too, and makes
                # the entire access audit vacuous while still reporting "clean".
                raise KeyForensicsError(
                    f"authorized_networks entry {entry!r} covers every address, which would "
                    "authorize any source IP and make the access audit vacuous."
                )
            networks.append(network)
        object.__setattr__(self, "authorized_networks", entries)
        object.__setattr__(self, "_networks", tuple(networks))
        object.__setattr__(self, "authorized_destinations", tuple(
            normalize_chain_address(d, "authorized_destinations entry")
            for d in _require_str_tuple(self.authorized_destinations, "authorized_destinations")
        ))
        object.__setattr__(self, "privileged_actions", frozenset(
            _require_str_tuple(sorted(self.privileged_actions), "privileged_actions")))

    def is_authorized_ip(self, ip_address: str) -> bool:
        """True if ``ip_address`` falls inside any allowlisted network.

        Networks of the other IP version are skipped rather than compared:
        ``IPv4Address in IPv6Network`` raises, and an allowlist legitimately
        mixes both families.
        """
        address = ipaddress.ip_address(ip_address)
        return any(address in network
                   for network in getattr(self, "_networks", ())
                   if address.version == network.version)


# --- Outputs ---------------------------------------------------------------

@dataclass(frozen=True)
class ForensicFinding:
    category: str
    severity: str
    description: str
    evidence_refs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainmentMandate:
    """Operational containment instruction. Not a regulatory filing."""

    containment_required: bool
    cease_cryptographic_protection: bool
    revoke_key_ids: Tuple[str, ...]
    rekey_key_ids: Tuple[str, ...]
    blocklist_addresses: Tuple[str, ...]
    revocation_reason: str
    determined_at: str
    actions: Tuple[str, ...]


@dataclass(frozen=True)
class KeyForensicsReport:
    key_id: str
    wallet_address: str
    status: str
    findings: Tuple[ForensicFinding, ...]
    unauthorized_successful_access_count: int
    unauthorized_attempt_count: int
    privileged_authorized_ip_access_count: int
    unauthorized_source_ips: Tuple[str, ...]
    unauthorized_transfer_count: int
    pre_incident_transfer_count: int
    authorized_transfer_count: int
    exfiltrated_by_asset: Mapping[str, str]   # asset symbol -> exact decimal string
    evidence_window_start: Optional[str]
    evidence_window_end: Optional[str]
    evidence_record_count: int
    evidence_hash_algorithm: str
    evidence_sha256: str
    source_artifact_digests: Mapping[str, str]
    custodian: str
    collected_at: str
    clock_offset_seconds: float
    analysis_time: str
    containment: ContainmentMandate
    audit_notes: str


# --- Evidence manifest -----------------------------------------------------

def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Unserializable evidence value of type {type(value).__name__}.")


def build_evidence_manifest(
    incident: KeyCompromiseIncident,
    access_logs: Sequence[KeyAccessLogEntry],
    transfers: Sequence[OnChainTransfer],
    policy: AccessControlPolicy,
    analysis_time: str,
) -> Dict[str, Any]:
    """Builds the canonical manifest that ``evidence_sha256`` is computed over.

    Every field of every record is included, so altering any single value in any
    record changes the digest. Record order is preserved as collected, because
    the sequence of a log is itself evidence.
    """
    return {
        "schema": EVIDENCE_MANIFEST_SCHEMA,
        "incident": {
            "key_id": incident.key_id,
            "wallet_address": incident.wallet_address,
            "suspected_leak_time": incident.leak_time.isoformat(),
            "affected_systems": list(incident.affected_systems),
            "derived_key_ids": list(incident.derived_key_ids),
            "clock_offset_seconds": incident.clock_offset_seconds,
        },
        "custody": {
            "custodian": incident.custodian,
            "collected_at": parse_utc_timestamp(incident.collected_at, "collected_at").isoformat(),
            "source_artifact_digests": dict(incident.source_artifact_digests),
            "analysis_time": parse_utc_timestamp(analysis_time, "analysis_time").isoformat(),
        },
        "policy": {
            "authorized_networks": list(policy.authorized_networks),
            "authorized_destinations": list(policy.authorized_destinations),
            "privileged_actions": sorted(policy.privileged_actions),
        },
        "access_logs": [
            {
                "timestamp": entry.at.isoformat(),
                "ip_address": entry.ip_address,
                "action": entry.action,
                "status_code": entry.status_code,
                "principal": entry.principal,
            }
            for entry in access_logs
        ],
        "transfers": [
            {
                "tx_hash": tx.tx_hash,
                "from_address": tx.from_address,
                "to_address": tx.to_address,
                "amount": tx.amount,
                "asset_symbol": tx.asset_symbol,
                "timestamp": tx.at.isoformat(),
            }
            for tx in transfers
        ],
    }


def compute_evidence_digest(manifest: Mapping[str, Any]) -> str:
    """SHA-256 (FIPS 180-4) over the canonical JSON serialization of the manifest.

    ``sort_keys`` plus the compact separators make the encoding canonical, so an
    independent party can recompute and verify the digest from the same records.
    """
    blob = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- Engine ----------------------------------------------------------------

class KeyForensicsAnalyzer:
    """Correlates off-chain access evidence with on-chain outflows for one incident."""

    def __init__(self, policy: AccessControlPolicy):
        if not isinstance(policy, AccessControlPolicy):
            raise KeyForensicsError("policy must be an AccessControlPolicy instance.")
        self.policy = policy

    def run_forensic_analysis(
        self,
        incident: KeyCompromiseIncident,
        access_logs: Sequence[KeyAccessLogEntry],
        on_chain_transfers: Sequence[OnChainTransfer],
        analysis_time: str,
    ) -> KeyForensicsReport:
        """Produces a ``KeyForensicsReport`` for one suspected key compromise.

        ``analysis_time`` is supplied by the caller rather than read from the
        system clock, so a stored evidence set replays to an identical report
        and an identical evidence digest.
        """
        if not isinstance(incident, KeyCompromiseIncident):
            raise KeyForensicsError("incident must be a KeyCompromiseIncident instance.")
        logs = self._as_sequence(access_logs, KeyAccessLogEntry, "access_logs")
        transfers = self._as_sequence(on_chain_transfers, OnChainTransfer, "on_chain_transfers")
        determined_at = parse_utc_timestamp(analysis_time, "analysis_time")

        findings: List[ForensicFinding] = []

        access = self._audit_access_logs(incident, logs, findings)
        outflows = self._trace_outflows(incident, transfers, findings)
        window_start, window_end = self._evidence_window(logs)
        self._audit_evidence_quality(
            incident, logs, transfers, window_start, window_end, findings)

        status = self._classify(findings)
        containment = self._build_containment(incident, outflows, status, determined_at)

        manifest = build_evidence_manifest(incident, logs, transfers, self.policy, analysis_time)
        evidence_hash = compute_evidence_digest(manifest)

        exfiltrated = {
            asset: format(amount, "f")
            for asset, amount in sorted(outflows["exfiltrated_by_asset"].items())
        }
        notes = (
            f"KEY FORENSICS [{incident.key_id} / {incident.wallet_address}] status={status}; "
            f"unauthorized successful accesses={access['successful']} "
            f"(rejected attempts={access['attempts']}, "
            f"privileged-from-allowlisted-IP={access['privileged']}); "
            f"unauthorized source IPs={list(access['ips'])}; "
            f"unauthorized outflows={outflows['unauthorized_count']} "
            f"(exfiltrated={exfiltrated or 'none'}); "
            f"pre-incident outflows={outflows['pre_incident_count']}, "
            f"authorized-destination outflows={outflows['authorized_count']}; "
            f"evidence records={len(logs) + len(transfers)}, "
            f"{EVIDENCE_HASH_ALGORITHM}={evidence_hash}; "
            f"custodian={incident.custodian}, collected_at={incident.collected_at}."
        )

        if status == STATUS_CONFIRMED:
            logger.critical("Key compromise confirmed: %s", notes)
        elif status in (STATUS_SUSPECTED, STATUS_INSUFFICIENT_EVIDENCE):
            logger.warning("Key compromise unresolved (%s): %s", status, notes)
        else:
            logger.info("No evidence of key compromise: %s", notes)

        return KeyForensicsReport(
            key_id=incident.key_id,
            wallet_address=incident.wallet_address,
            status=status,
            findings=tuple(findings),
            unauthorized_successful_access_count=access["successful"],
            unauthorized_attempt_count=access["attempts"],
            privileged_authorized_ip_access_count=access["privileged"],
            unauthorized_source_ips=tuple(access["ips"]),
            unauthorized_transfer_count=outflows["unauthorized_count"],
            pre_incident_transfer_count=outflows["pre_incident_count"],
            authorized_transfer_count=outflows["authorized_count"],
            exfiltrated_by_asset=exfiltrated,
            evidence_window_start=window_start.isoformat() if window_start else None,
            evidence_window_end=window_end.isoformat() if window_end else None,
            evidence_record_count=len(logs) + len(transfers),
            evidence_hash_algorithm=EVIDENCE_HASH_ALGORITHM,
            evidence_sha256=evidence_hash,
            source_artifact_digests=dict(incident.source_artifact_digests),
            custodian=incident.custodian,
            collected_at=incident.collected_at,
            clock_offset_seconds=incident.clock_offset_seconds,
            analysis_time=determined_at.isoformat(),
            containment=containment,
            audit_notes=notes,
        )

    # -- stages ------------------------------------------------------------

    @staticmethod
    def _as_sequence(values: Any, expected: type, name: str) -> Tuple[Any, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            raise KeyForensicsError(f"{name} must be a sequence of {expected.__name__}.")
        items = tuple(values)
        for item in items:
            if not isinstance(item, expected):
                raise KeyForensicsError(
                    f"{name} entries must be {expected.__name__}, got {type(item).__name__}."
                )
        return items

    def _audit_access_logs(
        self,
        incident: KeyCompromiseIncident,
        logs: Sequence[KeyAccessLogEntry],
        findings: List[ForensicFinding],
    ) -> Dict[str, Any]:
        successful: List[KeyAccessLogEntry] = []
        attempts: List[KeyAccessLogEntry] = []
        privileged: List[KeyAccessLogEntry] = []
        ips: List[str] = []              # insertion-ordered for a deterministic report
        successful_ips: List[str] = []

        for entry in logs:
            if not self.policy.is_authorized_ip(entry.ip_address):
                if entry.ip_address not in ips:
                    ips.append(entry.ip_address)
                if entry.succeeded:
                    successful.append(entry)
                    if entry.ip_address not in successful_ips:
                        successful_ips.append(entry.ip_address)
                else:
                    attempts.append(entry)
            elif entry.succeeded and entry.action in self.policy.privileged_actions:
                privileged.append(entry)

        if successful:
            findings.append(ForensicFinding(
                FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS,
                SEVERITY_CRITICAL,
                f"{len(successful)} successful key-access operation(s) from non-allowlisted "
                f"source IP(s) {successful_ips}. Key {incident.key_id} must be treated as "
                "disclosed.",
                tuple(f"{e.timestamp}|{e.ip_address}|{e.action}|{e.status_code}"
                      for e in successful),
            ))
        if attempts:
            findings.append(ForensicFinding(
                FINDING_UNAUTHORIZED_ACCESS_ATTEMPT,
                SEVERITY_HIGH,
                f"{len(attempts)} rejected access attempt(s) from non-allowlisted source IP(s). "
                "Rejected attempts are cybersecurity events, not evidence of disclosure; they "
                "are counted separately and never as successful accesses.",
                tuple(f"{e.timestamp}|{e.ip_address}|{e.action}|{e.status_code}"
                      for e in attempts),
            ))
        if privileged:
            findings.append(ForensicFinding(
                FINDING_PRIVILEGED_ACTION_FROM_AUTHORIZED_IP,
                SEVERITY_HIGH,
                f"{len(privileged)} successful privileged key operation(s) from allowlisted "
                "source IP(s). An allowlisted IP does not clear a key-material action: a stolen "
                "session or an insider operates from an authorized host.",
                tuple(f"{e.timestamp}|{e.ip_address}|{e.action}|{e.principal}"
                      for e in privileged),
            ))

        return {
            "successful": len(successful),
            "attempts": len(attempts),
            "privileged": len(privileged),
            "ips": ips,
        }

    def _trace_outflows(
        self,
        incident: KeyCompromiseIncident,
        transfers: Sequence[OnChainTransfer],
        findings: List[ForensicFinding],
    ) -> Dict[str, Any]:
        leak_time = incident.leak_time
        # Attribution is fail-closed against clock error: a transfer counts as
        # pre-incident only if it precedes the leak time by more than the
        # recorded source-clock offset (RFC 3227 s3.2).
        margin = abs(incident.clock_offset_seconds)
        exfiltrated: Dict[str, Decimal] = {}
        unauthorized: List[OnChainTransfer] = []
        pre_incident = 0
        authorized = 0
        destinations: List[str] = []

        for tx in transfers:
            if tx.from_address != incident.wallet_address:
                continue                      # inbound or unrelated: not an outflow
            if tx.to_address in self.policy.authorized_destinations:
                authorized += 1
                continue
            if (leak_time - tx.at).total_seconds() > margin:
                pre_incident += 1
                continue
            unauthorized.append(tx)
            exfiltrated[tx.asset_symbol] = exfiltrated.get(tx.asset_symbol, Decimal(0)) + tx.amount
            if tx.to_address not in destinations:
                destinations.append(tx.to_address)

        if unauthorized:
            amounts = ", ".join(f"{format(v, 'f')} {k}" for k, v in sorted(exfiltrated.items()))
            findings.append(ForensicFinding(
                FINDING_UNAUTHORIZED_OUTFLOW,
                SEVERITY_CRITICAL,
                f"{len(unauthorized)} unauthorized outflow(s) from {incident.wallet_address} "
                f"to {len(destinations)} destination(s) at or after the suspected leak time. "
                f"Exfiltrated: {amounts}.",
                tuple(tx.tx_hash for tx in unauthorized),
            ))

        return {
            "unauthorized_count": len(unauthorized),
            "pre_incident_count": pre_incident,
            "authorized_count": authorized,
            "exfiltrated_by_asset": exfiltrated,
            "destinations": tuple(destinations),
        }

    @staticmethod
    def _evidence_window(
        logs: Sequence[KeyAccessLogEntry],
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not logs:
            return None, None
        times = [entry.at for entry in logs]
        return min(times), max(times)

    def _audit_evidence_quality(
        self,
        incident: KeyCompromiseIncident,
        logs: Sequence[KeyAccessLogEntry],
        transfers: Sequence[OnChainTransfer],
        window_start: Optional[datetime],
        window_end: Optional[datetime],
        findings: List[ForensicFinding],
    ) -> None:
        leak_time = incident.leak_time
        if not logs:
            findings.append(ForensicFinding(
                FINDING_EVIDENCE_GAP,
                SEVERITY_CRITICAL,
                "No access-log evidence was supplied. The key cannot be cleared: absence of "
                "logs is not absence of access.",
            ))
        elif window_start is not None and window_end is not None and not (
                window_start <= leak_time <= window_end):
            findings.append(ForensicFinding(
                FINDING_EVIDENCE_GAP,
                SEVERITY_CRITICAL,
                f"The access-log window [{window_start.isoformat()}, {window_end.isoformat()}] "
                f"does not span the suspected leak time {leak_time.isoformat()}. The evidence "
                "cannot support any conclusion about access at the time of the suspected leak.",
            ))

        if not incident.source_artifact_digests:
            findings.append(ForensicFinding(
                FINDING_NO_SOURCE_ARTIFACT_DIGESTS,
                SEVERITY_MEDIUM,
                "No acquisition-time digests were recorded for the source log artifacts. The "
                "manifest digest seals the parsed records only; it cannot prove the original "
                "artifacts were unmodified before parsing (RFC 3227 s3.2).",
            ))

        if incident.derived_key_ids:
            findings.append(ForensicFinding(
                FINDING_DERIVED_KEY_EXPOSURE,
                SEVERITY_HIGH,
                f"{len(incident.derived_key_ids)} key(s) share a seed or module with "
                f"{incident.key_id} and are in the exposure radius. Where containment is "
                "mandated it must cover all affected keys (NIST SP 800-57 Pt.1 Rev.5 s5.5.2).",
                tuple(incident.derived_key_ids),
            ))

        # A paginated indexer query with overlapping windows returns the same
        # transfer twice, double-counting the exfiltration figure. Identical
        # records are reported rather than deduplicated: one transaction can
        # legitimately emit several transfer events, so silently collapsing them
        # would understate a real loss.
        seen: set = set()
        duplicates: List[str] = []
        for tx in transfers:
            fingerprint = (tx.tx_hash, tx.from_address, tx.to_address,
                           tx.asset_symbol, str(tx.amount), tx.timestamp)
            if fingerprint in seen and tx.tx_hash not in duplicates:
                duplicates.append(tx.tx_hash)
            seen.add(fingerprint)
        if duplicates:
            findings.append(ForensicFinding(
                FINDING_DUPLICATE_TRANSFER_RECORDS,
                SEVERITY_MEDIUM,
                f"{len(duplicates)} transfer(s) appear as byte-identical duplicate records. "
                "The exfiltration figure may be double-counted -- check the indexer query for "
                "overlapping pagination windows before quoting the loss.",
                tuple(duplicates),
            ))

        if incident.clock_offset_seconds != 0.0:
            findings.append(ForensicFinding(
                FINDING_CLOCK_OFFSET_RECORDED,
                SEVERITY_MEDIUM,
                f"Source-system clock offset of {incident.clock_offset_seconds}s recorded; "
                "outflows within that margin of the suspected leak time are attributed as "
                "unauthorized rather than pre-incident.",
            ))

    @staticmethod
    def _classify(findings: Sequence[ForensicFinding]) -> str:
        """Maps findings to a status.

        ``DERIVED_KEY_EXPOSURE`` is deliberately not a trigger: sibling keys
        existing is exposure radius, not evidence, and treating it as evidence
        would make a key with siblings impossible to clear.
        """
        categories = {f.category for f in findings}
        if categories & {FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS, FINDING_UNAUTHORIZED_OUTFLOW}:
            return STATUS_CONFIRMED
        if categories & {FINDING_UNAUTHORIZED_ACCESS_ATTEMPT,
                         FINDING_PRIVILEGED_ACTION_FROM_AUTHORIZED_IP}:
            return STATUS_SUSPECTED
        if FINDING_EVIDENCE_GAP in categories:
            return STATUS_INSUFFICIENT_EVIDENCE
        return STATUS_NO_EVIDENCE

    @staticmethod
    def _build_containment(
        incident: KeyCompromiseIncident,
        outflows: Mapping[str, Any],
        status: str,
        determined_at: datetime,
    ) -> ContainmentMandate:
        # Containment is mandated on anything short of a clean, evidenced
        # result: NIST SP 800-57 Pt.1 Rev.5 s8.3.5 revokes on emergency grounds
        # where there is "reason to believe" a key may have been disclosed, and
        # a key whose evidence has a gap has not been cleared.
        required = status != STATUS_NO_EVIDENCE
        keys = (incident.key_id,) + tuple(incident.derived_key_ids)
        blocklist = tuple(outflows["destinations"])

        if not required:
            return ContainmentMandate(
                containment_required=False,
                cease_cryptographic_protection=False,
                revoke_key_ids=(),
                rekey_key_ids=(),
                blocklist_addresses=(),
                revocation_reason="NOT_REQUIRED",
                determined_at=determined_at.isoformat(),
                actions=(),
            )

        reason = "KEY_COMPROMISE" if status == STATUS_CONFIRMED else "SUSPECTED_KEY_COMPROMISE"
        actions = [
            f"Cease all use of {incident.key_id} to apply cryptographic protection "
            "(NIST SP 800-57 Pt.1 Rev.5 s5.5).",
            f"Revoke {len(keys)} key(s) as soon as feasible and notify every relying party; "
            "the notice carries the key identifier, the revocation time and the reason -- "
            "never the key material itself (s8.3.5).",
            f"Re-key all {len(keys)} affected key(s) and monitor the re-keying to confirm every "
            "affected key was covered (s5.5.2).",
        ]
        if blocklist:
            actions.append(
                f"Report {len(blocklist)} exfiltration destination(s) to the relevant exchanges "
                "and chain-analytics providers. Freezing is discretionary for those venues -- "
                "treat it as best-effort recovery, never as a control."
            )
        actions.append(
            "Preserve the sealed evidence set and its digest before remediation touches the "
            "affected systems, and route the incident to compliance for the notification "
            "assessment this engine does not perform."
        )

        return ContainmentMandate(
            containment_required=True,
            cease_cryptographic_protection=True,
            revoke_key_ids=keys,
            rekey_key_ids=keys,
            blocklist_addresses=blocklist,
            revocation_reason=reason,
            determined_at=determined_at.isoformat(),
            actions=tuple(actions),
        )
