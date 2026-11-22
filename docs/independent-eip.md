# Independent DCV hostname

CPU/GPU create/start automatically claims the shared `dcv.kwesidavis.com` EIP when
unassigned and prepares trusted TLS. Create/start preserves an existing association;
publishers and dry runs never claim it. The allocation is account-owned. CLI
stop/destroy releases only the selected owner. With the shared cleanup stack below,
native automatic stop also releases the address after its owner reaches `stopped`.
Start claims the hostname if free. No CPU-return tags, original CPU identity, extra
workstation EIP or DNS edits.

`python3 studio workstation connect NAME` automatically selects the owner's trusted
hostname or a non-owner's current public IP. Use `hostname status` to inspect ownership,
`hostname assign NAME` for an intentional transfer or retry, and `hostname release`
to leave it unassigned. The optional `--hostname` flag checks ownership; it does not assign it.
An automatic-setup failure reports a warning and retry command, then verifies the
remaining endpoint before reporting readiness.

Every running desktop must retain a reachable raw-IP or trusted DCV endpoint.
Native public-IP recovery is checked after transfer, with bounded stop/start only
when needed; a GPU rearms and verifies its stop timer before restarting. Certificate warnings on raw-IP
connections are acceptable; blocked access is not.

## Automatic release after stopping

Deploy once per account in `us-east-1`; existing and future stop timers need no edits:

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name studio-dcv-stop-release --capabilities CAPABILITY_IAM \
  --template-file infra/workstation-hostname-stop-release.json
```

The shared handler reacts to EC2 stopped events and reconciles every minute if an
event is missed. It verifies the configured EIP's current stopped owner, immutable
CloudFormation identity and exact association before detaching. It cannot release
the allocation, change DNS/IAM, or start/stop/terminate compute. Native expiration
still stops EC2 independently if hostname cleanup fails. Release is asynchronous;
use `hostname status` to check availability and CloudWatch's `/aws/lambda/studio-dcv-stop-release`
log group to investigate cleanup errors. Logs expire after seven days.

The role's only EC2 write action is `DisassociateAddress`, with separate grants for
the configured, tagged EIP and network interfaces in the configured VPC. `VpcId`
defaults to `vpc-01bbe5d01838f70b2`; existing workstation interfaces need no new tags.

Automatic cleanup retains the former-owner tag and certificate grant for the CLI's
existing reconciliation on the next claim; `owner: null` with a `stale_owner_tag`
means the address is free. This avoids background certificate changes racing an
intentional transfer. Manual CLI stop still reconciles permissions immediately.
For an isolated test, override `AllocationId`, `Hostname` and `OwnerStack`, plus
`VpcId` if using a different VPC. Deployment stack names must be at most 64
characters. Roll back by deleting only `studio-dcv-stop-release`;
the EIP allocation, DNS, workstation disks and native stop timers remain intact.

See [Studio CLI](studio-cli.md#explicit-trusted-hostname) for the full owner, TLS,
rollback, race, permissions and acceptance contract. Historical commands are
retired and remain in Git history. The named lifecycle is merged in PR #116;
Issue #125 records the automatic-assignment correction and its acceptance evidence.
