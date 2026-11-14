# Independent DCV Elastic IP lifecycle (Issue #77)

The Studio DCV Elastic IP allocation `eipalloc-0879a34aa1a1298dd` and Route 53 record `dcv.kwesidavis.com` are account-owned resources, not CPU-instance resources. The address may remain **unassociated** while retaining its allocation, DNS record, and ACM certificate.

Use the Studio CLI commands only after verifying exact stack ownership:

```bash
python3 studio workstation hostname-release --execute
python3 studio workstation hostname-assign --stack studio-<workstation-stack> --execute
```

The release operation requires a stopped, recognized source instance. The assignment operation requires an unassociated allocation, a running verified CloudFormation workstation, valid local DCV TLS, and (for the supervised GPU) the PR #65 independent expiration guard. After assignment, public DNS/TLS is verified. An assignment failure attempts to disassociate the target without releasing the allocation.

**Current implementation:** PRs #79, #89 and #91 merged independent EIP assignment, guarded GPU planning/handoff and stop cleanup. The older `runtime/issue43_hostname_handoff.py` contains dated CPU anchor constants for historical helpers; the active `hostname-assign`/`hostname-release` path does not require the original instance. The CLI's old default stack value is historical; pass `--stack` explicitly.

**Live evidence:** On 2026-11-13, the EIP was safely disassociated from the stopped `cpu-rez77` workstation. AWS verified allocation `eipalloc-0879a34aa1a1298dd` retained, no associated instance or network interface, and the stale `StudioDcvInstanceId` tag removed. The tested original and replacement CPU stacks were subsequently deleted along with their root EBS, leaving account-owned EIP/DNS/ACM unaffected. Public browser login on a newly created replacement should be retested before claiming fresh end-to-end acceptance.
