# Cost Janitor — Scan Report

**Scan timestamp:** 2026-05-23T16:55:51Z  
**Region:** us-east-1  
**Mode:** delete  

## Summary

| Metric | Value |
|---|---|
| Total orphans | 8 |
| Estimated monthly waste | $58.21 |

## Findings

| Resource ID | Type | Reason | Age (days) | Est. $/month | Safe to auto-delete |
|---|---|---|---|---|---|
| `vol-d76eead0` | ebs_volume | unattached | 0 | $8.00 | ❌ No |
| `vol-4646b202` | ebs_volume | missing_tags:Project,Environment,Owner | 0 | $0.64 | ❌ No |
| `vol-4ea2246c` | ebs_volume | unattached | 0 | $40.00 | ❌ No |
| `vol-97eecf20` | ebs_volume | missing_tags:Project,Environment,Owner | 0 | $0.64 | ❌ No |
| `vol-21493ed7` | ebs_volume | unattached | 0 | $4.00 | ❌ No |
| `i-9e6466231e3d4261c` | ec2_instance | stopped>14d | 31 | $0.64 | ❌ No |
| `i-5419aef9c3c1a6eeb` | ec2_instance | missing_tags:Environment,Owner | 0 | $0.64 | ❌ No |
| `eipalloc-cb6b29a8` | elastic_ip | idle_eip | 0 | $3.65 | ❌ No |

---

> ✅ `--delete` mode was active. Resources without `Protected=true` were deleted.
