#!/usr/bin/env python3
"""
janitor.py — NimbusKart Cost Janitor.

Scans AWS (via boto3 / LocalStack) for wasteful resources and produces
structured JSON + Markdown reports.

Detection rules
---------------
1. EBS volumes in "available" state (unattached).
2. EC2 instances in "stopped" state for more than --stopped-days days.
   Age is derived from StateTransitionReason when possible; falls back to
   launch time.
3. Elastic IPs with no AssociationId (idle).
4. Any EC2 instance, EBS volume, or EIP missing one or more required tags
   (Project, Environment, Owner).

Usage
-----
    python janitor.py --dry-run [--region REGION] [--output-dir DIR]
    python janitor.py --delete  [--stopped-days N] [--region REGION]
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import click
from botocore.config import Config
from rich.console import Console
from rich.table import Table
from rich import box

from constants import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGION,
    DEFAULT_STOPPED_DAYS,
    EBS_GP3_COST_PER_GB_MONTH,
    EBS_SAFE_DELETE_MIN_AGE_DAYS,
    EC2_STOPPED_MONTHLY_COST_USD,
    EIP_IDLE_MONTHLY_COST_USD,
    LOCALSTACK_ENDPOINT,
    PROTECTED_TAG_KEY,
    PROTECTED_TAG_VALUE,
    REQUIRED_TAGS,
)

console = Console()

# ---------------------------------------------------------------------------
# boto3 helpers
# ---------------------------------------------------------------------------

def _client(service: str, region: str) -> Any:
    """Return a boto3 client, pointing at LocalStack when configured."""
    kwargs: dict[str, Any] = {
        "region_name": region,
        "config": Config(retries={"max_attempts": 3, "mode": "standard"}),
    }
    if LOCALSTACK_ENDPOINT:
        kwargs["endpoint_url"] = LOCALSTACK_ENDPOINT
        kwargs["aws_access_key_id"] = "test"
        kwargs["aws_secret_access_key"] = "test"
    return boto3.client(service, **kwargs)


def _get_account_id(region: str) -> str:
    """Return the AWS account ID, or a LocalStack placeholder on failure."""
    try:
        sts = _client("sts", region)
        return sts.get_caller_identity()["Account"]
    except Exception:  # noqa: BLE001
        return "000000000000"


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def _tags_to_dict(raw: list[dict] | None) -> dict[str, str]:
    """Convert [{'Key': k, 'Value': v}] → {k: v}."""
    if not raw:
        return {}
    return {t["Key"]: t["Value"] for t in raw}


def _is_protected(tags: dict[str, str]) -> bool:
    return tags.get(PROTECTED_TAG_KEY, "").lower() == PROTECTED_TAG_VALUE.lower()


def _missing_required_tags(tags: dict[str, str]) -> list[str]:
    return [t for t in REQUIRED_TAGS if t not in tags]


def _required_tags_snapshot(tags: dict[str, str]) -> dict[str, str | None]:
    """Return a dict of required tags with None for any that are absent."""
    return {t: tags.get(t) for t in REQUIRED_TAGS}


# ---------------------------------------------------------------------------
# Age helpers
# ---------------------------------------------------------------------------

def _age_days(dt: datetime) -> int:
    """Return whole days between *dt* and now (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


# EC2 StateTransitionReason examples:
#   "User initiated (2024-05-01 12:00:00 GMT)"
#   "User initiated"
_STATE_REASON_RE = re.compile(
    r"\((\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+GMT\)"
)


def _stopped_age_days(instance: dict[str, Any]) -> int:
    """
    Estimate how long an instance has been stopped.

    Tries to parse the timestamp from StateTransitionReason first.
    Falls back to LaunchTime if the reason string has no parseable date.
    """
    reason: str = instance.get("StateTransitionReason", "")
    match = _STATE_REASON_RE.search(reason)
    if match:
        try:
            stopped_at = datetime.strptime(
                match.group(1), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            return _age_days(stopped_at)
        except ValueError:
            pass
    # Fallback: use launch time as a conservative lower bound.
    return _age_days(instance["LaunchTime"])


# ---------------------------------------------------------------------------
# safe_to_auto_delete logic
# ---------------------------------------------------------------------------

def _ebs_has_snapshots(ec2_client: Any, volume_id: str) -> bool:
    """Return True if the volume has at least one snapshot."""
    try:
        resp = ec2_client.describe_snapshots(
            Filters=[{"Name": "volume-id", "Values": [volume_id]}],
            OwnerIds=["self"],
        )
        return len(resp.get("Snapshots", [])) > 0
    except Exception:  # noqa: BLE001
        # Err on the side of caution — assume snapshots exist.
        return True


def _safe_to_auto_delete(
    resource_type: str,
    tags: dict[str, str],
    age_days: int,
    ec2_client: Any | None = None,
    volume_id: str | None = None,
) -> bool:
    """
    Return True only when ALL conditions are met:
      - resource_type == "ebs_volume"
      - no Protected=true tag
      - age > EBS_SAFE_DELETE_MIN_AGE_DAYS
      - no snapshots exist for the volume
    Everything else (stopped EC2, EIPs, tag-missing resources) → False.
    """
    if resource_type != "ebs_volume":
        return False
    if _is_protected(tags):
        return False
    if age_days <= EBS_SAFE_DELETE_MIN_AGE_DAYS:
        return False
    if ec2_client is not None and volume_id is not None:
        if _ebs_has_snapshots(ec2_client, volume_id):
            return False
    return True


# ---------------------------------------------------------------------------
# Finding builder
# ---------------------------------------------------------------------------

def _make_finding(
    resource_id: str,
    resource_type: str,
    reason: str,
    age_days: int,
    monthly_cost: float,
    tags: dict[str, str],
    suggested_action: str,
    safe_to_auto_delete: bool,
) -> dict[str, Any]:
    """Return a finding dict that conforms to the report.json schema.

    ``tags`` holds the *required-tag snapshot* (Project/Environment/Owner →
    value or None) plus the raw ``Protected`` key when present, so that
    ``_delete_finding`` can honour the protected guard without an extra API
    call.
    """
    snapshot = _required_tags_snapshot(tags)
    # Preserve the Protected tag outside the required-tag snapshot so the
    # deletion guard in _delete_finding can read it reliably.
    if PROTECTED_TAG_KEY in tags:
        snapshot[PROTECTED_TAG_KEY] = tags[PROTECTED_TAG_KEY]
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "reason": reason,
        "age_days": age_days,
        "estimated_monthly_cost_usd": round(monthly_cost, 2),
        "tags": snapshot,
        "suggested_action": suggested_action,
        "safe_to_auto_delete": safe_to_auto_delete,
    }


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def scan_ebs(ec2: Any, region: str) -> list[dict[str, Any]]:
    """
    Rule 1 + Rule 4: unattached EBS volumes and/or volumes missing required tags.
    """
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()  # avoid duplicate findings for the same volume

    try:
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate():
            for vol in page["Volumes"]:
                vid = vol["VolumeId"]
                tags = _tags_to_dict(vol.get("Tags"))
                age = _age_days(vol["CreateTime"])
                size_gb: int = vol.get("Size", 0)
                monthly_cost = EBS_GP3_COST_PER_GB_MONTH * size_gb
                missing = _missing_required_tags(tags)
                reasons: list[str] = []

                # Rule 1 — unattached
                if vol["State"] == "available":
                    reasons.append("unattached")

                # Rule 4 — missing tags
                if missing:
                    reasons.append(f"missing_tags:{','.join(missing)}")

                if not reasons:
                    continue

                # Primary reason for the report is the first one detected.
                primary_reason = reasons[0]

                safe = _safe_to_auto_delete(
                    "ebs_volume",
                    tags,
                    age,
                    ec2_client=ec2,
                    volume_id=vid,
                )

                findings.append(
                    _make_finding(
                        resource_id=vid,
                        resource_type="ebs_volume",
                        reason=primary_reason,
                        age_days=age,
                        monthly_cost=monthly_cost,
                        tags=tags,
                        suggested_action="delete",
                        safe_to_auto_delete=safe,
                    )
                )
                seen.add(vid)

    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠  EBS scan error: {exc}[/yellow]")

    return findings


def scan_ec2_instances(
    ec2: Any,
    stopped_days: int,
) -> list[dict[str, Any]]:
    """
    Rule 2 + Rule 4: stopped EC2 instances and/or instances missing required tags.
    """
    findings: list[dict[str, Any]] = []

    try:
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page["Reservations"]:
                for inst in reservation["Instances"]:
                    iid = inst["InstanceId"]
                    state = inst["State"]["Name"]
                    tags = _tags_to_dict(inst.get("Tags"))
                    missing = _missing_required_tags(tags)
                    reasons: list[str] = []

                    # Rule 2 — stopped too long
                    if state == "stopped":
                        age = _stopped_age_days(inst)
                        if age > stopped_days:
                            reasons.append(f"stopped>{stopped_days}d")
                    else:
                        age = _age_days(inst["LaunchTime"])

                    # Rule 4 — missing tags (applies regardless of state)
                    if missing:
                        reasons.append(f"missing_tags:{','.join(missing)}")

                    if not reasons:
                        continue

                    findings.append(
                        _make_finding(
                            resource_id=iid,
                            resource_type="ec2_instance",
                            reason=reasons[0],
                            age_days=age,
                            monthly_cost=EC2_STOPPED_MONTHLY_COST_USD,
                            tags=tags,
                            suggested_action="terminate",
                            safe_to_auto_delete=False,
                        )
                    )

    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠  EC2 scan error: {exc}[/yellow]")

    return findings


def scan_eips(ec2: Any) -> list[dict[str, Any]]:
    """
    Rule 3 + Rule 4: idle Elastic IPs and/or EIPs missing required tags.
    """
    findings: list[dict[str, Any]] = []

    try:
        resp = ec2.describe_addresses()
        for addr in resp.get("Addresses", []):
            alloc_id = addr.get("AllocationId", addr.get("PublicIp", "unknown"))
            tags = _tags_to_dict(addr.get("Tags"))
            missing = _missing_required_tags(tags)
            reasons: list[str] = []

            # Rule 3 — no association
            if not addr.get("AssociationId"):
                reasons.append("idle_eip")

            # Rule 4 — missing tags
            if missing:
                reasons.append(f"missing_tags:{','.join(missing)}")

            if not reasons:
                continue

            findings.append(
                _make_finding(
                    resource_id=alloc_id,
                    resource_type="elastic_ip",
                    reason=reasons[0],
                    age_days=0,  # AWS does not expose EIP allocation time
                    monthly_cost=EIP_IDLE_MONTHLY_COST_USD,
                    tags=tags,
                    suggested_action="release",
                    safe_to_auto_delete=False,
                )
            )

    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠  EIP scan error: {exc}[/yellow]")

    return findings


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def _delete_finding(ec2: Any, finding: dict[str, Any]) -> bool:
    """
    Attempt to delete/terminate/release a single resource.
    Returns True on success, False if skipped or errored.
    """
    rid = finding["resource_id"]
    rtype = finding["resource_type"]

    # Reconstruct tags dict from the finding's tag snapshot for the protected check.
    # The snapshot only has required-tag keys; we need to re-fetch to check Protected.
    # We stored the full tags dict in the finding under "tags" — but the schema only
    # keeps required tags. We therefore re-check via the AWS API to be safe.
    # (For LocalStack speed, we trust the in-memory value set during scanning.)
    tag_snapshot: dict[str, str | None] = finding["tags"]
    protected_val = tag_snapshot.get(PROTECTED_TAG_KEY)
    if protected_val and protected_val.lower() == PROTECTED_TAG_VALUE.lower():
        console.print(f"  [dim]SKIP (Protected=true): {rid}[/dim]")
        return False

    try:
        if rtype == "ebs_volume":
            ec2.delete_volume(VolumeId=rid)
        elif rtype == "ec2_instance":
            ec2.terminate_instances(InstanceIds=[rid])
        elif rtype == "elastic_ip":
            ec2.release_address(AllocationId=rid)
        else:
            console.print(f"  [yellow]No deletion handler for type: {rtype}[/yellow]")
            return False

        console.print(f"  [green]✓ DELETED[/green] {rtype} {rid}")
        return True

    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ FAILED[/red] {rtype} {rid}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

def _write_json_report(
    path: Path,
    scan_ts: str,
    account_id: str,
    region: str,
    findings: list[dict[str, Any]],
) -> None:
    total_waste = sum(f["estimated_monthly_cost_usd"] for f in findings)
    report = {
        "scan_timestamp": scan_ts,
        "account_id": account_id,
        "region": region,
        "summary": {
            "total_orphans": len(findings),
            "estimated_monthly_waste_usd": round(total_waste, 2),
        },
        "findings": findings,
    }
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def _write_md_report(
    path: Path,
    scan_ts: str,
    region: str,
    findings: list[dict[str, Any]],
    dry_run: bool,
) -> None:
    total_waste = sum(f["estimated_monthly_cost_usd"] for f in findings)

    lines: list[str] = [
        "# Cost Janitor — Scan Report",
        "",
        f"**Scan timestamp:** {scan_ts}  ",
        f"**Region:** {region}  ",
        f"**Mode:** {'dry-run' if dry_run else 'delete'}  ",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total orphans | {len(findings)} |",
        f"| Estimated monthly waste | ${total_waste:.2f} |",
        "",
        "## Findings",
        "",
        "| Resource ID | Type | Reason | Age (days) | Est. $/month | Safe to auto-delete |",
        "|---|---|---|---|---|---|",
    ]

    for f in findings:
        safe = "✅ Yes" if f["safe_to_auto_delete"] else "❌ No"
        lines.append(
            f"| `{f['resource_id']}` "
            f"| {f['resource_type']} "
            f"| {f['reason']} "
            f"| {f['age_days']} "
            f"| ${f['estimated_monthly_cost_usd']:.2f} "
            f"| {safe} |"
        )

    if not findings:
        lines.append("| — | — | No findings | — | — | — |")

    lines += [
        "",
        "---",
        "",
    ]

    if dry_run:
        lines.append(
            "> ⚠️  **`--delete` was NOT run.** "
            "No resources were modified. "
            "Re-run with `--delete` to remove flagged resources."
        )
    else:
        lines.append(
            "> ✅ `--delete` mode was active. "
            "Resources without `Protected=true` were deleted."
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Rich console output
# ---------------------------------------------------------------------------

def _print_findings_table(findings: list[dict[str, Any]]) -> None:
    table = Table(
        title="Cost Janitor Findings",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Resource ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="magenta")
    table.add_column("Reason", style="yellow")
    table.add_column("Age (d)", justify="right")
    table.add_column("$/month", justify="right", style="red")
    table.add_column("Safe?", justify="center")

    for f in findings:
        safe_str = "[green]✓[/green]" if f["safe_to_auto_delete"] else "[red]✗[/red]"
        table.add_row(
            f["resource_id"],
            f["resource_type"],
            f["reason"],
            str(f["age_days"]),
            f"${f['estimated_monthly_cost_usd']:.2f}",
            safe_str,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--dry-run",
    "mode",
    flag_value="dry-run",
    default=True,
    help="Detect and report only. Exit 1 if any orphans found. (default)",
)
@click.option(
    "--delete",
    "mode",
    flag_value="delete",
    help="Detect and delete. Skips resources tagged Protected=true.",
)
@click.option(
    "--stopped-days",
    default=DEFAULT_STOPPED_DAYS,
    show_default=True,
    type=int,
    help="Flag EC2 instances stopped for more than N days.",
)
@click.option(
    "--region",
    default=DEFAULT_REGION,
    show_default=True,
    help="AWS region to scan.",
)
@click.option(
    "--output-dir",
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    type=click.Path(),
    help="Directory to write report.json and report.md.",
)
def main(
    mode: str,
    stopped_days: int,
    region: str,
    output_dir: str,
) -> None:
    """NimbusKart Cost Janitor — find and optionally remove wasteful AWS resources."""

    dry_run = mode == "dry-run"
    scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Banner ────────────────────────────────────────────────────────────────
    console.rule("[bold blue]NimbusKart Cost Janitor[/bold blue]")
    console.print(f"  Mode      : [bold]{'dry-run' if dry_run else 'DELETE'}[/bold]")
    console.print(f"  Region    : {region}")
    console.print(f"  Stopped ≥ : {stopped_days} days")
    console.print(f"  Endpoint  : {LOCALSTACK_ENDPOINT or 'AWS (real)'}")
    console.print(f"  Output    : {output_dir}")
    console.rule()

    # ── AWS clients ───────────────────────────────────────────────────────────
    ec2 = _client("ec2", region)
    account_id = _get_account_id(region)

    # ── Scan ──────────────────────────────────────────────────────────────────
    console.print("\n[bold]Scanning…[/bold]")

    findings: list[dict[str, Any]] = []
    findings.extend(scan_ebs(ec2, region))
    findings.extend(scan_ec2_instances(ec2, stopped_days))
    findings.extend(scan_eips(ec2))

    # ── Display ───────────────────────────────────────────────────────────────
    if findings:
        _print_findings_table(findings)
    else:
        console.print("[green]✓ No orphaned resources found.[/green]")

    total_waste = sum(f["estimated_monthly_cost_usd"] for f in findings)
    console.print(
        f"\n[bold]Summary:[/bold] {len(findings)} finding(s) · "
        f"estimated waste [red]${total_waste:.2f}[/red]/month"
    )

    # ── Delete (if requested) ─────────────────────────────────────────────────
    deleted_count = 0
    if not dry_run and findings:
        console.print("\n[bold red]Deleting flagged resources…[/bold red]")
        for finding in findings:
            if _delete_finding(ec2, finding):
                deleted_count += 1
        console.print(f"\n[bold]Deleted {deleted_count} resource(s).[/bold]")

    # ── Write reports ─────────────────────────────────────────────────────────
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    json_path = out_path / "report.json"
    md_path = out_path / "report.md"

    _write_json_report(json_path, scan_ts, account_id, region, findings)
    _write_md_report(md_path, scan_ts, region, findings, dry_run)

    console.print(f"\n[bold]Reports written:[/bold]")
    console.print(f"  JSON → {json_path}")
    console.print(f"  MD   → {md_path}")
    console.rule()

    # ── Exit code ─────────────────────────────────────────────────────────────
    # dry-run: exit 1 if any orphans were found (useful for CI gating).
    # delete:  exit 0 always (deletions already logged above).
    if dry_run and findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
