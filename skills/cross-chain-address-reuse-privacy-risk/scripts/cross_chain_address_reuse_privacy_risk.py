"""Cross-chain wallet address reuse and privacy-risk audit engine.

Audits a trading desk's wallet registry for address and public-key reuse
across blockchain networks (EVM family, Bitcoin, Solana, ...), detects
deanonymization linkages (including cross-format linkages via revealed
public keys), and computes a 0-100 privacy risk score.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Identifier comparison rule:
# - "0x"-prefixed hex (EVM): case-insensitive — EIP-55 mixed case is a
#   checksum layer over the hex value, so lowercasing both sides is safe.
# - Everything else (base58, e.g. Bitcoin legacy / Solana): exact match —
#   in base58 both letter cases are distinct alphabet characters, so
#   lowercasing corrupts identity and would merge different addresses.
EVM_HEX_PREFIX = "0x"


def _is_evm_hex(identifier: str) -> bool:
    return identifier[:2].lower() == EVM_HEX_PREFIX


def _same_identifier(left: str, right: str) -> bool:
    if _is_evm_hex(left) and _is_evm_hex(right):
        return left.lower() == right.lower()
    return left == right


def _chain_key(chain_id: str) -> str:
    """Canonical grouping key for a chain label.

    Chain names are human-entered registry labels, not protocol identifiers.
    Without normalisation "Ethereum", "ethereum" and " Ethereum " count as
    three distinct chains and inflate the reuse metric this module exists to
    measure.
    """
    return chain_id.strip().casefold()


def _same_public_key(left: Optional[str], right: Optional[str]) -> bool:
    """Public-key equality for clustering.

    `None` means "not known / not yet revealed on-chain" and NEVER forms a
    linkage edge: two unspent Bitcoin addresses are not linked merely because
    neither has revealed its key. Only concrete, equal key material links.
    """
    if left is None or right is None:
        return False
    return _same_identifier(left, right)


@dataclass
class WalletAddressRecord:
    chain_id: str                      # e.g. 'Ethereum', 'Arbitrum', 'Solana', 'Bitcoin'
    address: str
    # None = public key not known / not yet revealed on-chain (e.g. an unspent
    # Bitcoin P2PKH output, whose key stays hashed until first spend). Never
    # substitute a placeholder string such as "UNKNOWN": every record sharing
    # that placeholder would be clustered together and any one KYC linkage
    # would contaminate all of them.
    public_key: Optional[str]
    is_kyc_linked: bool                # True if linked to KYC exchange deposit/withdrawal
    wallet_label: str                  # e.g. 'Arbitrage_Bot_01'


@dataclass
class PrivacyRiskReport:
    address: str
    reused_chains_count: int
    chains_list: List[str]
    is_kyc_contaminated: bool
    privacy_risk_score: float           # 0.0 (Minimal) to 100.0 (Extreme Deanonymization Risk)
    risk_level: str                     # 'NOT_TRACKED', 'LOW', 'MEDIUM', 'HIGH'
    remediation_actions: List[str]
    linked_public_keys: List[str] = field(default_factory=list)


class CrossChainAddressPrivacyAuditor:
    """
    Crypto custody & security engine for auditing cross-chain wallet address reuse,
    detecting deanonymization linkages, and computing privacy risk scores.

    Records are linked into one cluster when they share an identical address
    (case-insensitive for 0x hex only, exact for base58) or an identical
    public key; linkage is transitive (connected component). An address absent
    from the registry yields a NOT_TRACKED verdict — absence of data is not
    evidence of safety.
    """

    def __init__(self, high_risk_threshold: float = 70.0, total_tracked_chains: int = 5,
                 medium_risk_threshold: float = 40.0):
        if isinstance(high_risk_threshold, bool) or not isinstance(high_risk_threshold, (int, float)):
            raise ValueError("high_risk_threshold must be a number in [0.0, 100.0].")
        if isinstance(medium_risk_threshold, bool) or not isinstance(medium_risk_threshold, (int, float)):
            raise ValueError("medium_risk_threshold must be a number in [0.0, 100.0].")
        if not (0.0 <= medium_risk_threshold <= high_risk_threshold <= 100.0):
            raise ValueError(
                f"Thresholds must satisfy 0.0 <= medium ({medium_risk_threshold}) "
                f"<= high ({high_risk_threshold}) <= 100.0."
            )
        if isinstance(total_tracked_chains, bool) or not isinstance(total_tracked_chains, int) \
                or total_tracked_chains < 1:
            raise ValueError("total_tracked_chains must be an integer >= 1.")
        self.high_risk_threshold = float(high_risk_threshold)
        self.medium_risk_threshold = float(medium_risk_threshold)
        self.total_tracked_chains = total_tracked_chains
        self.wallets: List[WalletAddressRecord] = []

    def register_wallet(self, wallet: WalletAddressRecord) -> None:
        """Registers a wallet record, rejecting malformed or duplicate entries."""
        if not isinstance(wallet, WalletAddressRecord):
            raise ValueError("wallet must be a WalletAddressRecord instance.")
        for attr in ("chain_id", "address", "wallet_label"):
            value = getattr(wallet, attr)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"WalletAddressRecord.{attr} must be a non-empty string.")
        # public_key is optional (None = not yet revealed on-chain), but an
        # empty/blank string is rejected: it is an ambiguous placeholder, and
        # accepting it would silently cluster every such record together.
        if wallet.public_key is not None and (
                not isinstance(wallet.public_key, str) or not wallet.public_key.strip()):
            raise ValueError(
                "WalletAddressRecord.public_key must be a non-empty string or None "
                "(None = key not yet revealed on-chain)."
            )
        if not isinstance(wallet.is_kyc_linked, bool):
            raise ValueError("WalletAddressRecord.is_kyc_linked must be a bool.")

        for existing in self.wallets:
            same_key = (
                (existing.public_key is None and wallet.public_key is None)
                or _same_public_key(existing.public_key, wallet.public_key)
            )
            if (_chain_key(existing.chain_id) == _chain_key(wallet.chain_id)
                    and _same_identifier(existing.address, wallet.address)
                    and same_key):
                raise ValueError(
                    f"Duplicate wallet registration rejected: chain_id={wallet.chain_id}, "
                    f"address={wallet.address}, public_key={wallet.public_key}."
                )

        observed_chains = {_chain_key(w.chain_id) for w in self.wallets} | {_chain_key(wallet.chain_id)}
        if len(observed_chains) > self.total_tracked_chains:
            logger.warning(
                "Registry now spans %d distinct chains but total_tracked_chains=%d; "
                "reuse scores will clamp at 50 — update the configuration.",
                len(observed_chains), self.total_tracked_chains,
            )
        self.wallets.append(wallet)

    def _expand_cluster(self, seed_indices: Set[int]) -> Set[int]:
        """Transitive closure over address-equality and public-key-equality edges."""
        cluster = set(seed_indices)
        frontier = list(seed_indices)
        while frontier:
            i = frontier.pop()
            for j, other in enumerate(self.wallets):
                if j in cluster:
                    continue
                if (_same_identifier(self.wallets[i].address, other.address)
                        or _same_public_key(self.wallets[i].public_key, other.public_key)):
                    cluster.add(j)
                    frontier.append(j)
        return cluster

    def audit_address_privacy(self, target_address: str) -> PrivacyRiskReport:
        """
        Audits a target wallet address across all registered chains and computes
        a Privacy Risk Score for its full linkage cluster (same address on any
        chain, or same public key under any address format).
        """
        if not isinstance(target_address, str) or not target_address.strip():
            raise ValueError("target_address must be a non-empty string.")

        seed_indices = {i for i, w in enumerate(self.wallets)
                        if _same_identifier(w.address, target_address)}
        if not seed_indices:
            return PrivacyRiskReport(
                address=target_address, reused_chains_count=0, chains_list=[],
                is_kyc_contaminated=False, privacy_risk_score=0.0, risk_level="NOT_TRACKED",
                remediation_actions=[
                    "NOT TRACKED: address is absent from the wallet registry — privacy "
                    "status is UNKNOWN, not LOW. Register the address on every active "
                    "chain before drawing risk conclusions."
                ],
            )

        cluster = [self.wallets[i] for i in sorted(self._expand_cluster(seed_indices))]
        # Group by normalised chain key so label casing/whitespace variants are
        # one chain, but display the first-registered spelling.
        chain_labels = {}
        for w in cluster:
            chain_labels.setdefault(_chain_key(w.chain_id), w.chain_id.strip())
        chains = [chain_labels[k] for k in sorted(chain_labels)]
        reused_count = len(chains)
        is_kyc = any(w.is_kyc_linked for w in cluster)

        # Risk Score Calculation:
        # 1. Chain reuse weight (up to 50 pts): 0 when the cluster touches a
        #    single chain - one chain is by definition NO reuse, and charging
        #    it made a perfectly isolated address score 50.0 (MEDIUM) on a
        #    desk configured with total_tracked_chains=1. Otherwise
        #    (reused_count / total_tracked_chains) * 50.
        # 2. KYC contamination penalty (50 pts if linked anywhere in the cluster)
        reuse_score = (
            0.0 if reused_count <= 1
            else min(50.0, (reused_count / float(self.total_tracked_chains)) * 50.0)
        )
        kyc_penalty = 50.0 if is_kyc else 0.0

        risk_score = round(min(100.0, reuse_score + kyc_penalty), 2)

        remediations = []
        if reused_count > 1:
            remediations.append(
                f"ADDRESS REUSE DETECTED across {reused_count} chains ({', '.join(chains)}). "
                "Isolate chains onto distinct BIP-44 accounts: coin_type separates chain "
                "families (Bitcoin 0', EVM 60', Solana 501' per SLIP-44), but an EVM key pair "
                "yields the identical 0x address on every EVM network, so EVM-to-EVM "
                "isolation requires distinct account' indexes or separate seeds."
            )
        distinct_addresses = {w.address.lower() if _is_evm_hex(w.address) else w.address
                              for w in cluster}
        if len(distinct_addresses) > 1:
            remediations.append(
                "PUBLIC KEY LINKAGE: cluster spans multiple distinct addresses joined by "
                "shared key material — once a spend reveals a public key on-chain, analytics "
                "firms can link these addresses. Rotate to per-chain key pairs."
            )
        if is_kyc:
            remediations.append(
                "KYC CONTAMINATION: Wallet is linked to centralized exchange deposits. Discontinue usage for proprietary strategies."
            )

        if risk_score >= self.high_risk_threshold:
            risk_level = "HIGH"
            logger.critical(f"HIGH PRIVACY RISK [{target_address}]: Score {risk_score}/100. {'; '.join(remediations)}")
        elif risk_score >= self.medium_risk_threshold:
            risk_level = "MEDIUM"
            logger.warning(f"MEDIUM PRIVACY RISK [{target_address}]: Score {risk_score}/100.")
        else:
            risk_level = "LOW"
            if not remediations:
                remediations.append(
                    "Wallet address exhibits strong cross-chain privacy isolation."
                )
            else:
                # Never append a clean bill of health after a finding: the
                # score is below the alert thresholds, but reuse or key
                # linkage WAS detected and must stay the last word.
                remediations.append(
                    "Score is below the alert thresholds, but the findings above are real "
                    "linkages — remediate them rather than treating this as isolated."
                )

        return PrivacyRiskReport(
            address=target_address,
            reused_chains_count=reused_count,
            chains_list=chains,
            is_kyc_contaminated=is_kyc,
            privacy_risk_score=risk_score,
            risk_level=risk_level,
            remediation_actions=remediations,
            # Only revealed keys; None means "not yet revealed on-chain" and is
            # not a linkage (mixing it in would also break sorted()).
            linked_public_keys=sorted({w.public_key for w in cluster if w.public_key is not None}),
        )
