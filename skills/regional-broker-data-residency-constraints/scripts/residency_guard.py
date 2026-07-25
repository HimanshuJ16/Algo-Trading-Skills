"""
regional-broker-data-residency-constraints: Regional data residency compliance guard
verifying cloud hosting region alignment with broker legal jurisdictions.
"""
from dataclasses import dataclass, field
import logging
import os
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class Jurisdiction(Enum):
    INDIA = "IN"
    EUROPEAN_UNION = "EU"
    UNITED_STATES = "US"
    UNITED_KINGDOM = "UK"
    SINGAPORE = "SG"
    GLOBAL_UNRESTRICTED = "GLOBAL"


class DataResidencyViolationError(Exception):
    """Raised when hosting region breaches broker data residency requirements."""
    pass


@dataclass
class BrokerResidencyPolicy:
    broker_name: str
    jurisdiction: Jurisdiction
    allowed_aws_regions: Set[str]
    allowed_gcp_regions: Set[str]
    description: str


# Standard jurisdiction policy matrix
BROKER_RESIDENCY_POLICIES: Dict[str, BrokerResidencyPolicy] = {
    "zerodha": BrokerResidencyPolicy(
        broker_name="zerodha",
        jurisdiction=Jurisdiction.INDIA,
        allowed_aws_regions={"ap-south-1", "ap-south-2"},  # Mumbai, Hyderabad
        allowed_gcp_regions={"asia-south1", "asia-south2"},
        description="SEBI / RBI Data Localisation Regulation (India)",
    ),
    "upstox": BrokerResidencyPolicy(
        broker_name="upstox",
        jurisdiction=Jurisdiction.INDIA,
        allowed_aws_regions={"ap-south-1", "ap-south-2"},
        allowed_gcp_regions={"asia-south1", "asia-south2"},
        description="SEBI / RBI Data Localisation Regulation (India)",
    ),
    "degiro": BrokerResidencyPolicy(
        broker_name="degiro",
        jurisdiction=Jurisdiction.EUROPEAN_UNION,
        allowed_aws_regions={"eu-central-1", "eu-west-1", "eu-west-3", "eu-south-1"},  # Frankfurt, Ireland, Paris, Milan
        allowed_gcp_regions={"europe-west1", "europe-west3", "europe-west4"},
        description="EU MiFID II / GDPR Data Residency Framework",
    ),
    "alpaca": BrokerResidencyPolicy(
        broker_name="alpaca",
        jurisdiction=Jurisdiction.UNITED_STATES,
        allowed_aws_regions={"us-east-1", "us-east-2", "us-west-1", "us-west-2"},
        allowed_gcp_regions={"us-central1", "us-east1", "us-west1"},
        description="US SEC / FINRA Data Standards",
    ),
}


class DataResidencyComplianceGuard:
    """
    Verifies that the hosting cloud region complies with target broker data residency rules.
    """

    def __init__(self, policies: Optional[Dict[str, BrokerResidencyPolicy]] = None):
        self.policies = policies or BROKER_RESIDENCY_POLICIES

    def probe_current_region(self) -> Tuple[str, str]:
        """Probes environment variables to detect active cloud provider and region."""
        # Check AWS
        aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if aws_region:
            return ("AWS", aws_region.lower())

        # Check GCP
        gcp_region = os.environ.get("GCP_REGION") or os.environ.get("CLOUD_RUN_REGION")
        if gcp_region:
            return ("GCP", gcp_region.lower())

        # Fallback default for testing
        return ("CUSTOM", os.environ.get("TRADING_HOST_REGION", "ap-south-1").lower())

    def validate_residency(self, broker_name: str, current_region: Optional[str] = None) -> bool:
        """
        Validates whether current_region complies with target broker residency rules.
        """
        b_key = broker_name.lower()
        policy = self.policies.get(b_key)

        if not policy:
            logger.info(f"No strict data residency constraints registered for '{broker_name}'. Allowed.")
            return True

        provider, region = self.probe_current_region()
        region_to_check = current_region.lower() if current_region else region

        is_valid = False
        if region_to_check in {r.lower() for r in policy.allowed_aws_regions}:
            is_valid = True
        elif region_to_check in {r.lower() for r in policy.allowed_gcp_regions}:
            is_valid = True

        if not is_valid:
            err_msg = (
                f"DATA RESIDENCY VIOLATION: Broker '{broker_name}' requires processing in "
                f"{policy.jurisdiction.value} region (Allowed AWS: {policy.allowed_aws_regions}). "
                f"Active process region '{region_to_check}' violates regulation: {policy.description}"
            )
            logger.critical(err_msg)
            raise DataResidencyViolationError(err_msg)

        logger.info(
            f"Data Residency Validated: Broker '{broker_name}' in region '{region_to_check}' "
            f"complies with {policy.jurisdiction.value} jurisdiction."
        )
        return True
