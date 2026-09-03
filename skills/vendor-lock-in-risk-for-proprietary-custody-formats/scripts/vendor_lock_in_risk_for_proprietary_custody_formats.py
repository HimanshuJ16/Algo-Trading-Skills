"""
vendor-lock-in-risk-for-proprietary-custody-formats: portability and exit-risk
scoring for institutional crypto custody providers.

What this module is and is not
------------------------------
It is a **structured scoring aid** that turns a documented custodian profile into
an auditable portability score, a lock-in risk level, and an exit-cost estimate.
It is not a recovery test. The only evidence that keys are recoverable is an
executed offline drill in which real key material is reconstructed without any
vendor involvement; ``simulate_disaster_recovery_drill`` models whether such a
drill *could* succeed given the documented profile, and nothing more.

Every boolean on ``CustodyProviderProfile`` is an assertion the reviewer must be
able to support from the custody agreement, the vendor's recovery documentation,
or a drill report. The engine scores what it is told and cannot detect a vendor
overstating its own export capability.

Key material vs. derivation metadata
------------------------------------
These are different things, and conflating them is the classic way to conclude
that an unrecoverable custodian is portable.

* **Secret-bearing formats** (BIP-39 mnemonic, SLIP-0039 shares, WIF keys,
  proprietary MPC shares, HSM blobs) carry -- or are -- the secret. Without one
  of these, nothing can be signed.
* **Derivation metadata** (``BIP32_HD_PATH``) carries no secret at all. BIP-39
  defines only mnemonic-to-seed and explicitly leaves wallet structure to
  "BIP-0032 or similar methods", so a seed alone does not tell you which
  accounts, coin types or script types the custodian actually used.

A custodian that exports derivation paths and nothing else exports no keys. The
scoring below therefore gates on secret-bearing material and treats derivation
metadata as a required *complement*, never as portability in its own right.

Heuristic weights
-----------------
``FORMAT_PORTABILITY`` and the score bonuses are **engineering defaults with no
external standards basis**. They encode an ordinal judgement (an open, widely
implemented seed format is more portable than a single-implementation
proprietary share, which is more portable than a non-exportable enclave blob),
and they are deliberately coarse. Recalibrate them against your own recovery
drill outcomes rather than treating the number as authoritative. See
``references/standards.md``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

#: Days added to the exit timeline when no independent offline recovery tool
#: exists, on the assumption that recovery then depends on vendor engineering
#: turnaround. An engineering default with no contractual or regulatory basis;
#: replace it with the notice period in your own custody agreement.
RECOVERY_TOOL_DELAY_DAYS = 14


class KeyFormatType(Enum):
    BIP39_MNEMONIC = "BIP39_MNEMONIC"                # Open standard 12/24 word seed phrase
    SLIP39_SHAMIR = "SLIP39_SHAMIR"                  # Open standard Shamir shares (SLIP-0039)
    BIP32_HD_PATH = "BIP32_HD_PATH"                  # Derivation metadata only -- carries no secret
    WIF_PRIVATE_KEY = "WIF_PRIVATE_KEY"              # Wallet Import Format (single raw private key)
    PROPRIETARY_MPC_SHARE = "PROPRIETARY_MPC_SHARE"  # Vendor-specific MPC key share encoding
    PROPRIETARY_HSM_BLOB = "PROPRIETARY_HSM_BLOB"    # Enclave-bound non-exportable HSM blob


class LockInRiskLevel(Enum):
    LOW = "LOW"             # Open secret-bearing export, derivation disclosed, no vendor dependency
    MEDIUM = "MEDIUM"       # Recoverable, but through a narrower or vendor-specific path
    HIGH = "HIGH"           # Recovery depends on vendor software or vendor availability
    CRITICAL = "CRITICAL"   # No independent recovery path


class CustodyArchitecture(Enum):
    SINGLE_SIG = "SINGLE_SIG"
    MULTISIG_ON_CHAIN = "MULTISIG_ON_CHAIN"  # E.g. Safe (Gnosis), native Bitcoin multisig
    MPC_THRESHOLD = "MPC_THRESHOLD"          # E.g. 2-of-3 MPC threshold signatures
    PROPRIETARY_VAULT = "PROPRIETARY_VAULT"  # Closed proprietary hardware vault


class CustodyAnalyzerError(ValueError):
    """Raised when a custodian profile or portfolio is internally invalid.

    A lock-in assessment must fail loudly. Scoring a portfolio carrying
    impossible values (negative wallet counts, a negative gas fee) produces an
    authoritative-looking exit cost built on garbage, which is worse than no
    estimate at all.
    """


@dataclass
class CustodyProviderProfile:
    """Documented export and recovery characteristics of a custodian.

    Args:
        supported_key_formats: Formats the custodian will actually export under
            the executed agreement -- not formats it uses internally.
        open_source_recovery_tool_available: True only if an offline recovery
            path exists that runs without vendor involvement and whose source can
            be independently reviewed. For BIP-39/SLIP-0039 exports this is
            satisfied by any standards-compliant third-party wallet. For
            proprietary MPC shares it requires the vendor to publish a working
            offline utility, which some vendors do and others do not -- verify it
            for your vendor rather than assuming either way.
        requires_vendor_active_api_for_exit: True if key material or assets can
            only be obtained while the vendor's service is operational. This is
            the insolvency-exposure flag: if it is True, key material held today
            is not sufficient to recover tomorrow.
    """

    provider_id: str
    provider_name: str
    architecture: CustodyArchitecture
    supported_key_formats: List[KeyFormatType]
    open_source_recovery_tool_available: bool
    requires_vendor_active_api_for_exit: bool
    flat_export_fee_usd: float = 0.0
    estimated_exit_days: int = 7


@dataclass
class AssetPortfolio:
    """Scope of the assets that would have to be moved in an exit.

    ``total_value_usd`` is contextual only. It is validated but deliberately does
    not feed the score, because lock-in risk is a property of the custody
    architecture, not of the balance sitting in it.
    """

    total_value_usd: float
    num_wallets: int
    num_blockchain_networks: int
    avg_network_gas_fee_per_tx_usd: float = 5.0


@dataclass
class CustodyLockInAssessment:
    provider_id: str
    provider_name: str
    lock_in_risk_level: LockInRiskLevel
    open_standard_compliance_ratio: float  # Share of declared formats that are open standards
    portability_score: float               # 0.0 to 100.0
    estimated_migration_cost_usd: float
    estimated_migration_days: int
    risk_factors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class CustodyLockInAnalyzer:
    """Institutional custody vendor lock-in and key-portability analyzer.

    Evaluates declared key export formats, scores portability from the *best*
    export path available, assigns a lock-in risk level, estimates exit cost and
    duration, and models an offline disaster recovery drill.
    """

    #: Formats defined by a public specification. Membership here says the format
    #: is open; it does not say the format carries a secret (BIP32_HD_PATH does
    #: not), nor that every wallet implements it (SLIP-0039 is deliberately not
    #: BIP-39 compatible and is implemented by far fewer wallets).
    OPEN_STANDARDS = frozenset({
        KeyFormatType.BIP39_MNEMONIC,
        KeyFormatType.SLIP39_SHAMIR,
        KeyFormatType.BIP32_HD_PATH,
        KeyFormatType.WIF_PRIVATE_KEY,
    })

    #: Formats that actually carry (or are) signing material.
    SECRET_BEARING_FORMATS = frozenset({
        KeyFormatType.BIP39_MNEMONIC,
        KeyFormatType.SLIP39_SHAMIR,
        KeyFormatType.WIF_PRIVATE_KEY,
        KeyFormatType.PROPRIETARY_MPC_SHARE,
        KeyFormatType.PROPRIETARY_HSM_BLOB,
    })

    #: Formats that describe *where* keys live without disclosing them.
    DERIVATION_METADATA_FORMATS = frozenset({KeyFormatType.BIP32_HD_PATH})

    #: Formats whose recovery needs the derivation structure as well as the
    #: secret, because the secret is a seed rather than a finished key.
    SEED_DERIVED_FORMATS = frozenset({
        KeyFormatType.BIP39_MNEMONIC,
        KeyFormatType.SLIP39_SHAMIR,
    })

    #: Ordinal portability of each secret-bearing format, 0.0 (unrecoverable
    #: without the vendor) to 1.0 (recoverable with commodity open-source
    #: tooling). Heuristic; see the module docstring.
    FORMAT_PORTABILITY: Dict[KeyFormatType, float] = {
        # Broadest cross-wallet support of any key backup format.
        KeyFormatType.BIP39_MNEMONIC: 1.00,
        # Open standard recovering a whole HD tree, but explicitly not BIP-39
        # compatible and implemented by a much narrower set of wallets.
        KeyFormatType.SLIP39_SHAMIR: 0.90,
        # Universally importable, but one key per address: it does not scale to a
        # large wallet estate and carries no HD structure.
        KeyFormatType.WIF_PRIVATE_KEY: 0.85,
        # Recoverable only through vendor-specific reconstruction logic. Some
        # vendors publish an offline open-source utility for exactly this and
        # some do not, which is why the recovery-tool attestation, rather than
        # the format itself, dominates the score here.
        KeyFormatType.PROPRIETARY_MPC_SHARE: 0.30,
        # Enclave-bound and non-exportable by construction.
        KeyFormatType.PROPRIETARY_HSM_BLOB: 0.00,
    }

    #: Score contribution for an independently reviewable offline recovery path.
    RECOVERY_TOOL_BONUS = 30.0
    #: Score contribution for not depending on a live vendor service to exit.
    NO_VENDOR_API_BONUS = 20.0
    #: Maximum score contributed by the export format itself.
    FORMAT_WEIGHT = 50.0

    def __init__(self) -> None:
        logger.debug("Initialized custody vendor lock-in risk analyzer")

    def calculate_open_standard_ratio(self, key_formats: List[KeyFormatType]) -> float:
        """Share of declared formats defined by a public specification, as a percentage.

        This is a *coverage diagnostic*, not the portability driver. It is an
        average, so it falls when a custodian declares additional proprietary
        formats even though the open export path is unchanged. Portability is
        scored from the best available format instead; see
        :meth:`calculate_format_portability`.
        """
        if not key_formats:
            return 0.0

        open_count = sum(1 for kf in key_formats if kf in self.OPEN_STANDARDS)
        return (open_count / len(key_formats)) * 100.0

    def calculate_format_portability(self, key_formats: List[KeyFormatType]) -> float:
        """Portability of the *best* secret-bearing format offered, 0.0 to 1.0.

        Returns 0.0 when no secret-bearing format is exportable at all, which
        includes the case of a custodian that exports derivation metadata only.
        Taking the maximum rather than the mean keeps the measure monotonic: a
        custodian never scores worse for offering an additional export option.
        """
        weights = [
            self.FORMAT_PORTABILITY[kf]
            for kf in key_formats
            if kf in self.SECRET_BEARING_FORMATS
        ]
        return max(weights) if weights else 0.0

    def _validate(self, provider: CustodyProviderProfile, portfolio: AssetPortfolio) -> None:
        """Rejects profiles and portfolios that cannot describe a real exit."""
        if portfolio.total_value_usd < 0:
            raise CustodyAnalyzerError("Portfolio total value cannot be negative.")
        if portfolio.num_wallets < 0:
            raise CustodyAnalyzerError("Portfolio wallet count cannot be negative.")
        if portfolio.num_blockchain_networks < 0:
            raise CustodyAnalyzerError("Portfolio blockchain network count cannot be negative.")
        if portfolio.avg_network_gas_fee_per_tx_usd < 0:
            raise CustodyAnalyzerError("Average network gas fee cannot be negative.")
        if provider.flat_export_fee_usd < 0:
            raise CustodyAnalyzerError("Vendor export fee cannot be negative.")
        if provider.estimated_exit_days < 0:
            raise CustodyAnalyzerError("Estimated exit days cannot be negative.")

    def evaluate_custody_provider(
        self, provider: CustodyProviderProfile, portfolio: AssetPortfolio
    ) -> CustodyLockInAssessment:
        """Scores portability, lock-in risk level and exit cost for one custodian.

        Raises:
            CustodyAnalyzerError: if the portfolio or provider carries values
                that cannot describe a real exit.
        """
        self._validate(provider, portfolio)

        formats = provider.supported_key_formats
        open_ratio = self.calculate_open_standard_ratio(formats)
        format_portability = self.calculate_format_portability(formats)
        has_secret_format = any(kf in self.SECRET_BEARING_FORMATS for kf in formats)
        has_derivation_metadata = any(kf in self.DERIVATION_METADATA_FORMATS for kf in formats)
        best_is_seed_derived = any(
            kf in self.SEED_DERIVED_FORMATS and self.FORMAT_PORTABILITY[kf] == format_portability
            for kf in formats
        )

        risk_factors: List[str] = []
        recommendations: List[str] = []

        # 1. Portability score. No exportable secret means no recovery, and no
        #    amount of tooling or API independence changes that -- the bonuses are
        #    therefore gated rather than accumulating from zero.
        if format_portability <= 0.0:
            portability_score = 0.0
            if has_derivation_metadata and not has_secret_format:
                risk_factors.append(
                    "Custodian exports derivation metadata only. A BIP-32 path carries no secret; "
                    "no key material is obtainable in any scenario."
                )
            else:
                risk_factors.append(
                    "No exportable key material: every declared format is enclave-bound or absent. "
                    "Recovery depends entirely on the vendor remaining solvent and operational."
                )
            recommendations.append(
                "Do not onboard without a contractual, drill-tested export of secret-bearing key "
                "material (BIP-39/SLIP-0039 seed or equivalent) held outside vendor infrastructure."
            )
        else:
            portability_score = self.FORMAT_WEIGHT * format_portability

            if provider.open_source_recovery_tool_available:
                portability_score += self.RECOVERY_TOOL_BONUS
            else:
                risk_factors.append(
                    "No independently reviewable offline recovery tool; reconstruction depends on "
                    "vendor-supplied software."
                )
                recommendations.append(
                    "Mandate an offline, source-available key reconstruction tool, and escrow the "
                    "exact source and binary you have drill-tested."
                )

            if not provider.requires_vendor_active_api_for_exit:
                portability_score += self.NO_VENDOR_API_BONUS
            else:
                risk_factors.append(
                    "Asset exit requires an active vendor service (insolvency and outage exposure): "
                    "key material held today is not sufficient to recover tomorrow."
                )
                recommendations.append(
                    "Require cold backup key share extraction into a self-hosted vault or HSM, taken "
                    "and verified while the vendor is still operational."
                )

        portability_score = min(100.0, max(0.0, portability_score))

        # 2. Format-specific recovery caveats a single score cannot express.
        if best_is_seed_derived and not has_derivation_metadata:
            risk_factors.append(
                "Custodian does not disclose derivation paths. BIP-39 defines only mnemonic-to-seed "
                "and leaves wallet structure to BIP-32/44, so a seed alone may not locate funds "
                "across non-default account, coin-type or script-type conventions."
            )
            recommendations.append(
                "Require the full derivation path and script type per network in writing, and "
                "confirm them by deriving a known funded address before relying on the backup."
            )
        if KeyFormatType.SLIP39_SHAMIR in formats and KeyFormatType.BIP39_MNEMONIC not in formats:
            risk_factors.append(
                "SLIP-0039 shares are an open standard but are explicitly not BIP-39 compatible; "
                "restoration requires a wallet that implements SLIP-0039."
            )
        if (
            KeyFormatType.WIF_PRIVATE_KEY in formats
            and format_portability == self.FORMAT_PORTABILITY[KeyFormatType.WIF_PRIVATE_KEY]
        ):
            risk_factors.append(
                "WIF export is one key per address and carries no HD structure; the export must "
                "enumerate every derived address in use or funds will be silently left behind."
            )

        # 3. Lock-in risk level. LOW additionally requires that the estate can be
        #    located, not merely that a secret can be extracted.
        estate_is_locatable = has_derivation_metadata or not best_is_seed_derived
        if portability_score >= 85.0 and open_ratio >= 75.0 and estate_is_locatable:
            risk_level = LockInRiskLevel.LOW
        elif portability_score >= 60.0:
            risk_level = LockInRiskLevel.MEDIUM
        elif portability_score >= 35.0:
            risk_level = LockInRiskLevel.HIGH
        else:
            risk_level = LockInRiskLevel.CRITICAL
            risk_factors.append(
                "CRITICAL LOCK-IN: no independent recovery path; an insolvency or prolonged vendor "
                "outage is expected to result in permanent loss of access."
            )

        # 4. Exit cost and duration. The transaction count assumes one sweep per
        #    wallet per network, which is an upper bound whenever wallets are not
        #    funded on every supported network.
        on_chain_tx_count = portfolio.num_wallets * portfolio.num_blockchain_networks
        estimated_gas_cost = on_chain_tx_count * portfolio.avg_network_gas_fee_per_tx_usd
        total_migration_cost = provider.flat_export_fee_usd + estimated_gas_cost

        extra_days = 0 if provider.open_source_recovery_tool_available else RECOVERY_TOOL_DELAY_DAYS
        total_migration_days = provider.estimated_exit_days + extra_days

        logger.info(
            "Custody lock-in assessment [%s]: risk=%s portability=%.1f exit_cost_usd=%.2f",
            provider.provider_name, risk_level.value, portability_score, total_migration_cost,
        )

        return CustodyLockInAssessment(
            provider_id=provider.provider_id,
            provider_name=provider.provider_name,
            lock_in_risk_level=risk_level,
            open_standard_compliance_ratio=open_ratio,
            portability_score=portability_score,
            estimated_migration_cost_usd=total_migration_cost,
            estimated_migration_days=total_migration_days,
            risk_factors=risk_factors,
            recommendations=recommendations,
        )

    def simulate_disaster_recovery_drill(
        self, provider: CustodyProviderProfile, is_vendor_responsive: bool = True
    ) -> Tuple[bool, str]:
        """Models whether a key recovery drill could succeed for this profile.

        This is a desk exercise over the declared profile, not evidence of
        recoverability. Only an executed offline drill that reconstructs real key
        material with the vendor uninvolved demonstrates self-sovereignty.

        Args:
            is_vendor_responsive: False models insolvency or a prolonged outage.
                Success in that scenario additionally requires that exit does not
                depend on the vendor's service, since key material that can only
                be fetched from a dead service cannot be fetched at all.
        """
        formats = provider.supported_key_formats
        has_exportable_secret = self.calculate_format_portability(formats) > 0.0
        has_open_secret_format = any(
            kf in self.OPEN_STANDARDS and kf in self.SECRET_BEARING_FORMATS for kf in formats
        )

        if not has_exportable_secret:
            logger.error(
                "DISASTER RECOVERY FAILED [%s]: no exportable key material in any scenario",
                provider.provider_name,
            )
            return (
                False,
                f"Key Recovery FAILED for '{provider.provider_name}'. No exportable key material: "
                f"assets are recoverable only through vendor-operated processes.",
            )

        if is_vendor_responsive:
            if provider.requires_vendor_active_api_for_exit:
                return (
                    True,
                    f"Vendor API active. Key export drill SUCCEEDED for '{provider.provider_name}' "
                    f"but is vendor-dependent: this is NOT evidence of self-sovereignty. Re-run "
                    f"with is_vendor_responsive=False.",
                )
            return (
                True,
                f"Vendor API active. Key export drill SUCCEEDED for '{provider.provider_name}'. "
                f"Re-run with is_vendor_responsive=False to evidence self-sovereignty.",
            )

        # Vendor outage / insolvency scenario.
        if provider.requires_vendor_active_api_for_exit:
            logger.error(
                "DISASTER RECOVERY FAILED [%s]: exit path depends on an unavailable vendor service",
                provider.provider_name,
            )
            return (
                False,
                f"Vendor Offline. Emergency Key Recovery FAILED for '{provider.provider_name}'. "
                f"Key material is only obtainable through the vendor service, which is unavailable.",
            )

        # An open secret-bearing format is recoverable with commodity third-party
        # wallets; a proprietary share instead needs the vendor's offline tool.
        if has_open_secret_format or provider.open_source_recovery_tool_available:
            return (
                True,
                f"Vendor Offline. Emergency Key Recovery SUCCESSFUL using an offline independent "
                f"recovery path for '{provider.provider_name}'.",
            )

        logger.error(
            "DISASTER RECOVERY FAILED [%s]: proprietary format with no independent offline tool",
            provider.provider_name,
        )
        return (
            False,
            f"Vendor Offline. Emergency Key Recovery FAILED for '{provider.provider_name}'. "
            f"Assets LOCKED in proprietary format with no independent recovery tool.",
        )
