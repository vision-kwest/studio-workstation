# Independent DCV Elastic IP lifecycle (Issue #77)

The Studio DCV Elastic IP allocation `eipalloc-0879a34aa1a1298dd` and Route 53 record `dcv.kwesidavis.com` are account-owned resources, not CPU-instance resources. The address may remain **unassociated** while retaining its allocation, DNS record, and ACM certificate.

Use the Studio CLI commands only after verifying exact stack ownership:

```bash
python3 studio workstation hostname-release --execute
python3 studio workstation hostname-assign --stack studio-<workstation-stack> --execute
```

The release operation requires a stopped, recognized source instance. The assignment operation requires an unassociated allocation, a running verified CloudFormation workstation, valid local DCV TLS, and (for the supervised GPU) the PR #65 independent expiration guard. After assignment, public DNS/TLS is verified. An assignment failure attempts to disassociate the target without releasing the allocation.

**Limitations pending Issue #77 completion:** Existing GPU ACM enrollment, GPU stop/destroy, and CPU/GPU legacy handoff still refer to the original CPU anchor. Do not use these new commands for a GPU replacement until those paths have been reconciled and offline regression-tested. The original CPU destroy path must independently verify release first.

**Live release acceptance (2026-11-11):** The old CPU was stopped; the EIP was disassociated and its `StudioDcvInstanceId` tag deleted. AWS `DescribeAddresses` independently confirmed allocation `eipalloc-0879a34aa1a1298dd` remains reserved, with no association/instance owner and `StudioIssue=43`. Assignment and full workstation acceptance remain untested.
