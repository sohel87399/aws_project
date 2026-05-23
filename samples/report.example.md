## 🧹 Cost Janitor Report

> **5 orphaned resource(s) detected** — estimated monthly waste: **$52.93**

| | |
|---|---|
| **Scan timestamp** | 2026-05-20T06:00:04Z |
| **Account ID** | 847392015648 |
| **Region** | us-east-1 |
| **Mode** | dry-run (no resources modified) |
| **Triggered by** | PR #214 — `feature/checkout-performance` |

---

## Summary

| Metric | Value |
|---|---|
| Total orphans detected | 5 |
| Estimated monthly waste | **$52.93** |
| Safe to auto-delete | 1 |
| Requires human review | 4 |
| Protected (skipped) | 0 |

---

## Findings

| # | Resource ID | Type | Reason | Age (days) | Est. $/month | Safe to auto-delete |
|---|---|---|---|---|---|---|
| 1 | `vol-0f3a8c21d4b7e9051` | EBS Volume | unattached | 47 | $8.00 | ✅ Yes |
| 2 | `i-0b7d3f92a1c84e560` | EC2 Instance | stopped > 14 days | 31 | $0.64 | ❌ No |
| 3 | `eipalloc-0c4e7f1a2b3d8059` | Elastic IP | idle — no association | 0 | $3.65 | ❌ No |
| 4 | `vol-0d9f4a17b2c63e802` | EBS Volume | missing tags: Project, Owner | 83 | $40.00 | ❌ No |
| 5 | `i-0a2c6e85f3d17b904` | EC2 Instance | missing tags: Environment, Owner | 12 | $0.64 | ❌ No |

---

## Finding details

### 1 · `vol-0f3a8c21d4b7e9051` — Unattached EBS Volume · $8.00/month

| Field | Value |
|---|---|
| Size | 100 GB (gp3) |
| State | available (unattached) |
| Age | 47 days |
| Owner | data-engineering |
| Environment | staging |
| Safe to auto-delete | ✅ Yes — no snapshots within 7 days, no Protected tag, age > 7 days |

**Recommended action:** Delete. This volume has been detached for 47 days with no recent snapshot activity. At $8.00/month it is the second-largest waste item in this scan.

---

### 2 · `i-0b7d3f92a1c84e560` — Stopped EC2 Instance · $0.64/month

| Field | Value |
|---|---|
| Instance type | t3.micro |
| State | stopped |
| Stopped for | 31 days (threshold: 14 days) |
| Owner | backend-team |
| Environment | staging |
| Safe to auto-delete | ❌ No — stopped instances require human confirmation before termination |

**Recommended action:** Confirm with `backend-team` whether this instance is a scheduled batch worker or a decommissioned dev box. If decommissioned, terminate. If it is a standby, add tag `do-not-delete=true`.

---

### 3 · `eipalloc-0c4e7f1a2b3d8059` — Idle Elastic IP · $3.65/month

| Field | Value |
|---|---|
| Public IP | 54.210.183.47 |
| Association | None |
| Owner | platform-team |
| Environment | staging |
| Safe to auto-delete | ❌ No — EIPs are never auto-released; confirm no pending re-association |

**Recommended action:** Release if the associated load balancer or NAT Gateway has been decommissioned. AWS charges $0.005/hr ($3.65/month) for every unassociated EIP.

---

### 4 · `vol-0d9f4a17b2c63e802` — Untagged EBS Volume · $40.00/month

| Field | Value |
|---|---|
| Size | 500 GB (gp3) |
| State | available (unattached) |
| Age | 83 days |
| Project tag | ❌ missing |
| Owner tag | ❌ missing |
| Environment tag | prod |
| Safe to auto-delete | ❌ No — missing ownership tags; cannot confirm safe to delete |

**Recommended action:** Identify the owner via CloudTrail (`ec2:CreateVolume` event on the volume's creation date). Tag it with `Project` and `Owner` before the next scan, or delete if unowned. At $40.00/month this is the largest single waste item.

---

### 5 · `i-0a2c6e85f3d17b904` — Untagged EC2 Instance · $0.64/month

| Field | Value |
|---|---|
| Instance type | t3.micro |
| State | running |
| Age | 12 days |
| Project tag | nimbuskart |
| Environment tag | ❌ missing |
| Owner tag | ❌ missing |
| Safe to auto-delete | ❌ No — running instances are never auto-terminated |

**Recommended action:** Apply missing `Environment` and `Owner` tags immediately. This instance will continue to appear in every scan until tagged. If it is a personal dev box with no owner, terminate it.

---

## Cost breakdown

```
vol-0d9f4a17b2c63e802  (500 GB EBS, untagged)   $40.00  ████████████████████████████████████████  75.6%
eipalloc-0c4e7f1a2b3d8059  (idle EIP)            $ 3.65  ████                                       6.9%
vol-0f3a8c21d4b7e9051  (100 GB EBS, unattached)  $ 8.00  ████████                                  15.1%
i-0b7d3f92a1c84e560    (stopped EC2, root EBS)   $ 0.64  ▌                                          1.2%
i-0a2c6e85f3d17b904    (running EC2, root EBS)   $ 0.64  ▌                                          1.2%
                                                 ──────
TOTAL                                            $52.93/month  →  $635.16/year if unresolved
```

---

## Recommended next steps

1. **Immediate (today):** Delete `vol-0f3a8c21d4b7e9051` — safe to auto-delete, saves $8.00/month.
2. **This sprint:** Identify owner of `vol-0d9f4a17b2c63e802` via CloudTrail and either tag or delete — saves $40.00/month.
3. **This sprint:** Release `eipalloc-0c4e7f1a2b3d8059` if the associated service is decommissioned — saves $3.65/month.
4. **Owner action required:** `backend-team` to confirm disposition of `i-0b7d3f92a1c84e560` within 5 business days.
5. **Policy enforcement:** Apply mandatory tag policy via AWS Config or Service Control Policy to prevent untagged resources from being created in future.

---

> ⚠️ **`--delete` was NOT run.** No resources were modified during this scan.
> Re-run with `--delete` to remove resources where `safe_to_auto_delete = true`.
> All other findings require human review before deletion.

---

_Generated by NimbusKart Cost Janitor v1.0 · Scan: 2026-05-20T06:00:04Z · Region: us-east-1 · Account: 847392015648_
