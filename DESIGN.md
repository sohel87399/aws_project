# Cost Janitor — Design Note

## Multi-cloud extension

The current monolithic `janitor.py` couples AWS-specific boto3 calls to the
core detection logic. To add GCP without rewriting that logic, split the
package into two layers:

```
janitor/
  core/
    models.py        # Finding, Report dataclasses
    pricing.py       # cost arithmetic (cloud-agnostic)
    report.py        # JSON + Markdown writers
    cli.py           # Click entrypoint; loads provider from registry
  providers/
    base.py          # BaseProvider ABC
    aws/scanner.py   # boto3 implementation
    gcp/scanner.py   # google-cloud-compute implementation
    azure/scanner.py # (future)
```

`core/` has zero cloud-SDK imports. Every provider implements the same ABC:

```python
# janitor/providers/base.py
from abc import ABC, abstractmethod
from janitor.core.models import Finding

class BaseProvider(ABC):
    @abstractmethod
    def list_unattached_volumes(self) -> list[Finding]: ...

    @abstractmethod
    def list_stopped_instances(self, stopped_days: int) -> list[Finding]: ...

    @abstractmethod
    def list_idle_ips(self) -> list[Finding]: ...

    @abstractmethod
    def list_untagged_resources(self) -> list[Finding]: ...
```

A provider registry dict in `cli.py` maps the `--provider` flag to the
concrete class: `{"aws": AwsProvider, "gcp": GcpProvider}`. Adding Azure
means adding `janitor/providers/azure/scanner.py`, implementing the four
methods, and registering `"azure": AzureProvider` — no changes to `core/`.

---

## Permissions

**Dry-run role** — read-only, safe to attach to any CI runner:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "JanitorReadOnly",
    "Effect": "Allow",
    "Action": [
      "ec2:DescribeVolumes",
      "ec2:DescribeInstances",
      "ec2:DescribeAddresses",
      "ec2:DescribeTags",
      "s3:ListBuckets",
      "s3:GetBucketTagging"
    ],
    "Resource": "*"
  }]
}
```

**Delete mode** requires three additional actions:
`ec2:DeleteVolume`, `ec2:ReleaseAddress`, `ec2:TerminateInstances`.

These must live in a **separate role** for two reasons. First, blast radius:
a misconfigured dry-run scan cannot accidentally delete anything if the
runner's role physically lacks the delete actions. Second, auditability:
CloudTrail shows which role called `TerminateInstances`, making it
unambiguous whether a deletion was human-initiated or automated. The delete
role should require MFA for human assumption and should only be assumable by
the CI pipeline's OIDC identity — never attached permanently to an EC2
instance profile or a developer's IAM user.

---

## Safety net

**Failure mode 1 — stopped instance that is not idle.**
A blue/green standby or a nightly batch job (e.g. a Spark EMR bootstrap
instance) is stopped between runs. It looks identical to a forgotten
development box. Naïve deletion causes a missed batch window or a failed
deployment promotion.

Guardrail: require **both** conditions before auto-deletion is permitted —
the instance must be stopped for ≥ 30 days (not the current 14-day default)
**and** must not carry a `do-not-delete=true` tag. Additionally, send a Slack
notification via webhook 24 hours before the deletion job fires, listing the
instance ID, Name tag, and Owner tag. This gives the owner one business day
to intervene.

**Failure mode 2 — detached EBS volume mid-restore.**
An ops engineer detaches a volume to mount it on a recovery instance during a
DR drill or a failed migration rollback. The volume sits in `available` state
for hours. The janitor flags it as an orphan and, if `safe_to_auto_delete` is
true, deletes it — destroying the only copy of the data being recovered.

Guardrail: before setting `safe_to_auto_delete=true`, call
`ec2:DescribeSnapshots` filtered by `volume-id`. If any snapshot was created
within the last 7 days, force `safe_to_auto_delete=false` and add
`"reason": "recent_snapshot_exists"` to the finding. This is already
implemented in `_safe_to_auto_delete()` in `janitor.py`.

---

## Observability

| Metric | Source | Destination | Alert threshold |
|---|---|---|---|
| `orphans_detected_total` | `report.json → summary.total_orphans` | CloudWatch custom metric (`NimbusKart/CostJanitor`) | > 0 for 3 consecutive daily runs |
| `estimated_waste_usd_monthly` | `report.json → summary.estimated_monthly_waste_usd` | CloudWatch custom metric (`NimbusKart/CostJanitor`) | > $100 in a single scan |
| `janitor_run_duration_seconds` | GitHub Actions step duration log → CloudWatch Logs Insights | CloudWatch alarm on log metric filter | > 300 s (indicates API throttling or LocalStack timeout) |
| `protected_skips_total` | Count of findings where `safe_to_auto_delete=false` and `Protected=true` in `report.json` | CloudWatch custom metric (`NimbusKart/CostJanitor`) | > 10 in a single scan (suggests the tag is being misused to hide real waste) |

Publish the first two metrics by parsing `report.json` in a post-scan Lambda
or a GitHub Actions step that calls `aws cloudwatch put-metric-data`.

---

## What was not built

This implementation covers a single AWS account and three resource types (EC2,
EBS, EIP). It has no multi-account support — extending to an AWS Organization
would require iterating over accounts via `organizations:ListAccounts` and
assuming a cross-account role with `sts:AssumeRole` in each. Pricing is
hard-coded in `constants.py` rather than fetched live from the AWS Price List
API or Cost Explorer, so figures will drift as AWS adjusts rates. Deletion
outcomes are reported only as a GitHub PR comment; there is no Slack or
PagerDuty webhook. RDS clusters, Lambda functions, and unused NAT Gateways are
not scanned despite being common sources of waste. Finally, each run
overwrites `report.json` — there is no historical store, so trend analysis
(e.g. "waste grew 20% this week") is not possible without an external time
series database.
