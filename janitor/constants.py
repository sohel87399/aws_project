"""
constants.py — Cost and policy constants for the NimbusKart Cost Janitor.

All pricing figures are on-demand, us-east-1, as of 2024.
Sources are cited inline; update them if AWS changes pricing.
"""

# ---------------------------------------------------------------------------
# Tag policy
# ---------------------------------------------------------------------------

# Every resource must carry all three of these tags.
REQUIRED_TAGS: list[str] = ["Project", "Environment", "Owner"]

# Tag that marks a resource as exempt from deletion.
PROTECTED_TAG_KEY: str = "Protected"
PROTECTED_TAG_VALUE: str = "true"  # compared case-insensitively at runtime

# ---------------------------------------------------------------------------
# Stopped-instance threshold (overridden by --stopped-days CLI flag)
# ---------------------------------------------------------------------------

DEFAULT_STOPPED_DAYS: int = 14

# ---------------------------------------------------------------------------
# Cost constants
# ---------------------------------------------------------------------------

# EBS gp3 storage: $0.08 per GB-month
# Source: https://aws.amazon.com/ebs/pricing/
EBS_GP3_COST_PER_GB_MONTH: float = 0.08

# Default EBS root volume size assumed for a stopped EC2 instance (GB).
# AWS default root volume for most AMIs is 8 GB.
EC2_ROOT_VOLUME_GB: int = 8

# Monthly cost attributed to a stopped EC2 instance (compute = $0, root EBS accrues).
# = EBS_GP3_COST_PER_GB_MONTH * EC2_ROOT_VOLUME_GB
# Source: https://aws.amazon.com/ebs/pricing/
EC2_STOPPED_MONTHLY_COST_USD: float = EBS_GP3_COST_PER_GB_MONTH * EC2_ROOT_VOLUME_GB  # $0.64

# Idle Elastic IP: $0.005/hour = $0.005 * 24 * 365 / 12 ≈ $3.65/month
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (Elastic IP Addresses section)
EIP_IDLE_COST_PER_HOUR: float = 0.005
EIP_IDLE_MONTHLY_COST_USD: float = round(EIP_IDLE_COST_PER_HOUR * 24 * 365 / 12, 2)  # $3.65

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

# LocalStack endpoint — set to None to target real AWS.
LOCALSTACK_ENDPOINT: str | None = "http://localhost:4566"

DEFAULT_REGION: str = "us-east-1"
DEFAULT_OUTPUT_DIR: str = "./output"

# Minimum age (days) for an unattached EBS volume to be considered safe to
# auto-delete (in addition to having no snapshots and no Protected tag).
EBS_SAFE_DELETE_MIN_AGE_DAYS: int = 7
