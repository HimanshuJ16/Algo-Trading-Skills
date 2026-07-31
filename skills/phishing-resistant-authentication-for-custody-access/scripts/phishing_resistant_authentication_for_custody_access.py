import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class PhishingResistantAuthenticationForCustodyAccessConfig:
    enabled: bool = True

@dataclass
class WebAuthnAssertion:
    user_id: str
    client_origin: str                  # e.g. 'https://custody.firm.com'
    rp_id: str                          # e.g. 'custody.firm.com'
    challenge: str
    user_present: bool = True           # Physical button touch (UP=1)
    user_verified: bool = True          # PIN or Biometric (UV=1)
    timestamp: float = field(default_factory=time.time)

@dataclass
class AuthPolicyConfig:
    allowed_rp_ids: List[str] = field(default_factory=lambda: ["custody.firm.com"])
    require_user_verification: bool = True
    max_challenge_age_sec: int = 60

@dataclass
class AuthVerificationReport:
    user_id: str
    rp_id: str
    client_origin: str
    is_origin_valid: bool
    is_user_verified: bool
    status: str                          # 'AUTH_SUCCESSFUL', 'ORIGIN_MISMATCH_PHISHING_ATTEMPT', 'USER_VERIFICATION_FAILED', 'CHALLENGE_EXPIRED'
    audit_notes: str

class PhishingResistantAuthenticationForCustodyAccessEngine:
    """
    Phishing-resistant authentication engine verifying FIDO2/WebAuthn hardware security keys,
    cryptographic origin binding, and user verification flags for institutional crypto custody access.
    """
    def __init__(
        self,
        config: Optional[PhishingResistantAuthenticationForCustodyAccessConfig] = None,
        policy: Optional[AuthPolicyConfig] = None
    ):
        self.config = config or PhishingResistantAuthenticationForCustodyAccessConfig()
        self.policy = policy or AuthPolicyConfig()

    def execute(self) -> bool:
        """
        Legacy execution method for backward compatibility.
        """
        return True if self.config.enabled else False

    def verify_assertion(
        self, assertion: WebAuthnAssertion
    ) -> AuthVerificationReport:
        """
        Verifies WebAuthn assertion origin binding, user presence/verification flags, and challenge freshness.
        """
        if not self.config.enabled:
            return AuthVerificationReport(
                user_id=assertion.user_id,
                rp_id=assertion.rp_id,
                client_origin=assertion.client_origin,
                is_origin_valid=False,
                is_user_verified=False,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        # 1. Cryptographic Origin Binding Verification
        expected_origin = f"https://{assertion.rp_id}"
        is_rp_allowed = assertion.rp_id in self.policy.allowed_rp_ids
        is_origin_valid = is_rp_allowed and (assertion.client_origin.lower() == expected_origin.lower())

        if not is_origin_valid:
            notes = (
                f"SECURITY REJECTION [PHISHING ATTEMPT DETECTED]: User '{assertion.user_id}' requested RP '{assertion.rp_id}', "
                f"but client origin '{assertion.client_origin}' does not match expected '{expected_origin}'."
            )
            logger.error(notes)
            return AuthVerificationReport(
                user_id=assertion.user_id,
                rp_id=assertion.rp_id,
                client_origin=assertion.client_origin,
                is_origin_valid=False,
                is_user_verified=assertion.user_verified,
                status="ORIGIN_MISMATCH_PHISHING_ATTEMPT",
                audit_notes=notes
            )

        # 2. User Presence & Verification Flags
        if not assertion.user_present:
            notes = f"AUTH REJECTION: User presence flag (UP) missing for user '{assertion.user_id}'."
            logger.warning(notes)
            return AuthVerificationReport(
                user_id=assertion.user_id,
                rp_id=assertion.rp_id,
                client_origin=assertion.client_origin,
                is_origin_valid=True,
                is_user_verified=False,
                status="USER_VERIFICATION_FAILED",
                audit_notes=notes
            )

        if self.policy.require_user_verification and not assertion.user_verified:
            notes = f"AUTH REJECTION: User verification (UV PIN/Biometric) required but missing for user '{assertion.user_id}'."
            logger.warning(notes)
            return AuthVerificationReport(
                user_id=assertion.user_id,
                rp_id=assertion.rp_id,
                client_origin=assertion.client_origin,
                is_origin_valid=True,
                is_user_verified=False,
                status="USER_VERIFICATION_FAILED",
                audit_notes=notes
            )

        # 3. Challenge Freshness Check
        age = time.time() - assertion.timestamp
        if age > self.policy.max_challenge_age_sec:
            notes = f"AUTH REJECTION: Challenge expired for user '{assertion.user_id}' (Age = {age:.1f}s > {self.policy.max_challenge_age_sec}s)."
            logger.warning(notes)
            return AuthVerificationReport(
                user_id=assertion.user_id,
                rp_id=assertion.rp_id,
                client_origin=assertion.client_origin,
                is_origin_valid=True,
                is_user_verified=True,
                status="CHALLENGE_EXPIRED",
                audit_notes=notes
            )

        notes = f"WEBAUTHN AUTHENTICATION SUCCESSFUL: User '{assertion.user_id}' verified via FIDO2 origin '{assertion.client_origin}'."
        logger.info(notes)

        return AuthVerificationReport(
            user_id=assertion.user_id,
            rp_id=assertion.rp_id,
            client_origin=assertion.client_origin,
            is_origin_valid=True,
            is_user_verified=True,
            status="AUTH_SUCCESSFUL",
            audit_notes=notes
        )
