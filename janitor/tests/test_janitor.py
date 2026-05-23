"""
test_janitor.py — pytest unit tests for the NimbusKart Cost Janitor.

All AWS calls are intercepted by moto (@mock_aws).  No real credentials,
no LocalStack, no network traffic.

The module-level patch below sets LOCALSTACK_ENDPOINT = None before any
janitor code runs, so boto3 clients created inside the scanners go through
moto's interceptors rather than trying to reach http://localhost:4566.

Test index (maps to the 10 required cases)
------------------------------------------
Case 1  – TestEbsScanner::test_unattached_volume_is_flagged
Case 2  – TestEbsScanner::test_attached_volume_is_not_flagged
Case 3  – TestEc2Scanner::test_stopped_instance_older_than_threshold_is_flagged
Case 4  – TestEc2Scanner::test_stopped_instance_within_threshold_is_not_flagged
Case 5  – TestEipScanner::test_idle_eip_is_flagged
Case 6  – TestEc2Scanner::test_instance_missing_owner_tag_is_flagged
Case 7  – TestDeleteMode::test_protected_resource_is_skipped_in_delete_mode
Case 8  – TestReportSchema::test_report_json_top_level_keys
Case 9  – TestCLIExitCodes::test_dry_run_exits_1_when_orphans_found
Case 10 – TestCLIExitCodes::test_dry_run_exits_0_when_no_orphans_found
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from click.testing import CliRunner
from moto import mock_aws

# ---------------------------------------------------------------------------
# sys.path — make "janitor/" importable when pytest is run from any directory
# ---------------------------------------------------------------------------
_JANITOR_DIR = os.path.join(os.path.dirname(__file__), "..")
if _JANITOR_DIR not in sys.path:
    sys.path.insert(0, _JANITOR_DIR)

# ---------------------------------------------------------------------------
# Disable LocalStack endpoint BEFORE importing janitor so every boto3 client
# the module creates goes through moto's interceptors.
# ---------------------------------------------------------------------------
import constants as _c  # noqa: E402

_c.LOCALSTACK_ENDPOINT = None  # type: ignore[assignment]

from janitor import (  # noqa: E402
    _delete_finding,
    _make_finding,
    _stopped_age_days,
    _write_json_report,
    main,
    scan_ebs,
    scan_ec2_instances,
    scan_eips,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

REGION = "us-east-1"
AZ = "us-east-1a"

# A fully-tagged resource satisfies all tag rules.
FULL_TAGS = [
    {"Key": "Project", "Value": "nimbuskart"},
    {"Key": "Environment", "Value": "staging"},
    {"Key": "Owner", "Value": "platform-team"},
]


def _ec2_client() -> boto3.client:
    """Return a plain boto3 EC2 client (moto intercepts it)."""
    return boto3.client("ec2", region_name=REGION)


def _run_instance(ec2, tags: list[dict] | None = None) -> str:
    """Launch one t3.micro and return its instance ID."""
    kwargs: dict = {"ImageId": "ami-00000000", "MinCount": 1, "MaxCount": 1}
    if tags:
        kwargs["TagSpecifications"] = [{"ResourceType": "instance", "Tags": tags}]
    resp = ec2.run_instances(**kwargs)
    return resp["Instances"][0]["InstanceId"]


def _stop_instance(ec2, iid: str) -> None:
    ec2.stop_instances(InstanceIds=[iid])


def _create_volume(ec2, tags: list[dict] | None = None, size: int = 20) -> str:
    """Create a gp3 volume and return its volume ID."""
    kwargs: dict = {"AvailabilityZone": AZ, "Size": size, "VolumeType": "gp3"}
    if tags:
        kwargs["TagSpecifications"] = [{"ResourceType": "volume", "Tags": tags}]
    return ec2.create_volume(**kwargs)["VolumeId"]


# ---------------------------------------------------------------------------
# Case 1 & 2 — EBS scanner
# ---------------------------------------------------------------------------

class TestEbsScanner:
    """Cases 1 and 2: unattached vs attached EBS volumes."""

    @mock_aws
    def test_unattached_volume_is_flagged(self):
        """Case 1 — an available (unattached) EBS volume must appear in findings."""
        ec2 = _ec2_client()
        vid = _create_volume(ec2)  # never attached → state = available

        findings = scan_ebs(ec2, REGION)

        resource_ids = [f["resource_id"] for f in findings]
        assert vid in resource_ids, "Unattached volume must be flagged"

        finding = next(f for f in findings if f["resource_id"] == vid)
        assert finding["resource_type"] == "ebs_volume"
        assert finding["reason"] == "unattached"

    @mock_aws
    def test_attached_volume_is_not_flagged(self):
        """Case 2 — a volume attached to a running instance must NOT be flagged."""
        ec2 = _ec2_client()

        # Launch an instance; moto automatically creates and attaches a root volume.
        iid = _run_instance(ec2, tags=FULL_TAGS)

        # Find the root volume that moto attached to this instance.
        resp = ec2.describe_volumes(
            Filters=[{"Name": "attachment.instance-id", "Values": [iid]}]
        )
        attached_vids = [v["VolumeId"] for v in resp["Volumes"]]
        assert attached_vids, "moto should have created a root volume"

        # Tag the attached volume with all required tags so it is not flagged
        # for missing tags — we only want to test the attachment-state rule here.
        ec2.create_tags(Resources=attached_vids, Tags=FULL_TAGS)

        findings = scan_ebs(ec2, REGION)
        flagged_ids = [f["resource_id"] for f in findings]

        for vid in attached_vids:
            assert vid not in flagged_ids, (
                f"Attached volume {vid} must not be flagged"
            )


# ---------------------------------------------------------------------------
# Cases 3, 4, 6 — EC2 scanner
# ---------------------------------------------------------------------------

class TestEc2Scanner:
    """Cases 3, 4, 6: stopped-instance age threshold and missing-tag detection."""

    @mock_aws
    def test_stopped_instance_older_than_threshold_is_flagged(self):
        """Case 3 — stopped instance whose stop-time is > 14 days ago is flagged."""
        ec2 = _ec2_client()
        iid = _run_instance(ec2, tags=FULL_TAGS)
        _stop_instance(ec2, iid)

        # Simulate the instance having been stopped 20 days ago by injecting a
        # StateTransitionReason that _stopped_age_days() can parse.
        stopped_at = datetime.now(timezone.utc) - timedelta(days=20)
        reason_str = stopped_at.strftime("User initiated (%Y-%m-%d %H:%M:%S GMT)")

        # Patch _stopped_age_days to return 20 for this instance so the test
        # is deterministic regardless of moto's internal timestamp handling.
        original = _stopped_age_days

        def _fake_stopped_age(inst: dict) -> int:
            if inst.get("InstanceId") == iid:
                return 20
            return original(inst)

        with patch("janitor._stopped_age_days", side_effect=_fake_stopped_age):
            findings = scan_ec2_instances(ec2, stopped_days=14)

        flagged_ids = [f["resource_id"] for f in findings]
        assert iid in flagged_ids, "Instance stopped 20 days ago must be flagged (threshold=14)"

        finding = next(f for f in findings if f["resource_id"] == iid)
        assert "stopped" in finding["reason"]

    @mock_aws
    def test_stopped_instance_within_threshold_is_not_flagged(self):
        """Case 4 — stopped instance whose stop-time is only 5 days ago is NOT flagged."""
        ec2 = _ec2_client()
        iid = _run_instance(ec2, tags=FULL_TAGS)
        _stop_instance(ec2, iid)

        # Patch _stopped_age_days to return 5 — well within the 14-day threshold.
        original = _stopped_age_days

        def _fake_stopped_age(inst: dict) -> int:
            if inst.get("InstanceId") == iid:
                return 5
            return original(inst)

        with patch("janitor._stopped_age_days", side_effect=_fake_stopped_age):
            # Use a high stopped_days so only the age matters, not missing tags.
            # We also supply full tags so the instance has no other reason to be flagged.
            findings = scan_ec2_instances(ec2, stopped_days=14)

        # Filter to only the stopped-age reason; the instance may still appear
        # for missing-tag reasons if FULL_TAGS weren't applied — but it must NOT
        # appear with a "stopped>" reason.
        stopped_findings = [
            f for f in findings
            if f["resource_id"] == iid and "stopped>" in f["reason"]
        ]
        assert stopped_findings == [], (
            "Instance stopped only 5 days ago must not be flagged for stopped age"
        )

    @mock_aws
    def test_instance_missing_owner_tag_is_flagged(self):
        """Case 6 — an EC2 instance missing the 'Owner' tag must be flagged."""
        ec2 = _ec2_client()
        # Provide Project and Environment but deliberately omit Owner.
        partial_tags = [
            {"Key": "Project", "Value": "nimbuskart"},
            {"Key": "Environment", "Value": "staging"},
        ]
        iid = _run_instance(ec2, tags=partial_tags)

        # Use a very high stopped_days so only the tag rule fires.
        findings = scan_ec2_instances(ec2, stopped_days=99999)

        flagged_ids = [f["resource_id"] for f in findings]
        assert iid in flagged_ids, "Instance missing Owner tag must be flagged"

        finding = next(f for f in findings if f["resource_id"] == iid)
        assert "missing_tags" in finding["reason"]
        assert finding["tags"]["Owner"] is None


# ---------------------------------------------------------------------------
# Case 5 — EIP scanner
# ---------------------------------------------------------------------------

class TestEipScanner:
    """Case 5: idle Elastic IP (no AssociationId) is flagged."""

    @mock_aws
    def test_idle_eip_is_flagged(self):
        """Case 5 — an EIP with no AssociationId must appear in findings."""
        ec2 = _ec2_client()
        alloc = ec2.allocate_address(Domain="vpc")
        alloc_id = alloc["AllocationId"]

        findings = scan_eips(ec2)

        resource_ids = [f["resource_id"] for f in findings]
        assert alloc_id in resource_ids, "Idle EIP must be flagged"

        finding = next(f for f in findings if f["resource_id"] == alloc_id)
        assert finding["resource_type"] == "elastic_ip"
        assert finding["reason"] == "idle_eip"
        assert finding["safe_to_auto_delete"] is False


# ---------------------------------------------------------------------------
# Case 7 — delete mode + Protected tag
# ---------------------------------------------------------------------------

class TestDeleteMode:
    """Case 7: resources tagged Protected=true are skipped in --delete mode."""

    @mock_aws
    def test_protected_resource_is_skipped_in_delete_mode(self, tmp_path):
        """
        Case 7 — an EIP tagged Protected=true must survive a --delete run.

        Strategy: allocate an EIP, tag it Protected=true, run the CLI in
        --delete mode, then verify the EIP still exists.
        """
        ec2 = _ec2_client()
        alloc = ec2.allocate_address(Domain="vpc")
        alloc_id = alloc["AllocationId"]

        # Tag the EIP as protected.
        ec2.create_tags(
            Resources=[alloc_id],
            Tags=[{"Key": "Protected", "Value": "true"}],
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--delete", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        # The CLI must not crash.
        assert result.exit_code == 0, (
            f"CLI exited {result.exit_code}; output:\n{result.output}"
        )

        # The EIP must still exist — it was not released.
        addresses = ec2.describe_addresses()["Addresses"]
        surviving_ids = [a["AllocationId"] for a in addresses]
        assert alloc_id in surviving_ids, (
            "Protected EIP must not be released in --delete mode"
        )

    @mock_aws
    def test_unprotected_eip_is_released_in_delete_mode(self, tmp_path):
        """Complement of Case 7 — an unprotected idle EIP IS released."""
        ec2 = _ec2_client()
        alloc = ec2.allocate_address(Domain="vpc")
        alloc_id = alloc["AllocationId"]

        runner = CliRunner()
        runner.invoke(
            main,
            ["--delete", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        addresses = ec2.describe_addresses()["Addresses"]
        surviving_ids = [a["AllocationId"] for a in addresses]
        assert alloc_id not in surviving_ids, (
            "Unprotected idle EIP must be released in --delete mode"
        )


# ---------------------------------------------------------------------------
# Case 8 — report.json schema
# ---------------------------------------------------------------------------

class TestReportSchema:
    """Case 8: report.json must contain all required top-level keys."""

    # Required schema keys from the spec.
    TOP_LEVEL_KEYS = {"scan_timestamp", "account_id", "region", "summary", "findings"}
    SUMMARY_KEYS = {"total_orphans", "estimated_monthly_waste_usd"}
    FINDING_KEYS = {
        "resource_id",
        "resource_type",
        "reason",
        "age_days",
        "estimated_monthly_cost_usd",
        "tags",
        "suggested_action",
        "safe_to_auto_delete",
    }

    @mock_aws
    def test_report_json_top_level_keys(self, tmp_path):
        """Case 8 — report.json must have all required top-level keys."""
        # Create one finding so the findings array is non-empty.
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")  # idle EIP → one finding

        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        report_path = tmp_path / "report.json"
        assert report_path.exists(), "report.json must be written"

        data = json.loads(report_path.read_text(encoding="utf-8"))

        missing_top = self.TOP_LEVEL_KEYS - data.keys()
        assert not missing_top, f"report.json missing top-level keys: {missing_top}"

    @mock_aws
    def test_report_json_summary_keys(self, tmp_path):
        """summary block must contain total_orphans and estimated_monthly_waste_usd."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")

        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        missing_summary = self.SUMMARY_KEYS - data["summary"].keys()
        assert not missing_summary, f"summary missing keys: {missing_summary}"

    @mock_aws
    def test_report_json_finding_keys(self, tmp_path):
        """Each finding must contain all required schema keys."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")

        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert data["findings"], "Expected at least one finding"

        for finding in data["findings"]:
            missing = self.FINDING_KEYS - finding.keys()
            assert not missing, f"Finding {finding.get('resource_id')} missing keys: {missing}"

    @mock_aws
    def test_report_json_total_orphans_matches_findings_length(self, tmp_path):
        """summary.total_orphans must equal len(findings)."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")
        ec2.allocate_address(Domain="vpc")  # two idle EIPs

        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert data["summary"]["total_orphans"] == len(data["findings"])

    @mock_aws
    def test_report_json_waste_equals_sum_of_finding_costs(self, tmp_path):
        """summary.estimated_monthly_waste_usd must equal sum of finding costs."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")

        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        expected = round(
            sum(f["estimated_monthly_cost_usd"] for f in data["findings"]), 2
        )
        assert data["summary"]["estimated_monthly_waste_usd"] == expected

    @mock_aws
    def test_report_md_is_also_written(self, tmp_path):
        """report.md must be written alongside report.json on every run."""
        runner = CliRunner()
        runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )
        assert (tmp_path / "report.md").exists(), "report.md must always be written"


# ---------------------------------------------------------------------------
# Cases 9 & 10 — CLI exit codes
# ---------------------------------------------------------------------------

class TestCLIExitCodes:
    """Cases 9 and 10: --dry-run exit codes."""

    @mock_aws
    def test_dry_run_exits_1_when_orphans_found(self, tmp_path):
        """Case 9 — dry-run exits 1 when at least one orphan is detected."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")  # idle EIP → one finding

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        assert result.exit_code == 1, (
            f"Expected exit 1 with orphans; got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )

    @mock_aws
    def test_dry_run_exits_0_when_no_orphans_found(self, tmp_path):
        """Case 10 — dry-run exits 0 when the account is clean."""
        # Empty moto environment — no resources at all.
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--dry-run", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        assert result.exit_code == 0, (
            f"Expected exit 0 with no orphans; got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )

    @mock_aws
    def test_delete_mode_always_exits_0(self, tmp_path):
        """--delete mode must exit 0 regardless of how many resources are found."""
        ec2 = _ec2_client()
        ec2.allocate_address(Domain="vpc")

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--delete", "--region", REGION, "--output-dir", str(tmp_path)],
        )

        assert result.exit_code == 0, (
            f"--delete mode must exit 0; got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )
