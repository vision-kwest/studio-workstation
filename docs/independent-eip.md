# Independent DCV hostname

CPU/GPU create/start automatically claims the shared `dcv.kwesidavis.com` EIP when
unassigned and prepares trusted TLS. An occupied hostname stays with its current
owner, even if stopped; publishers and dry runs never claim it. The allocation is
account-owned. Stop/destroy releases only the selected owner; start claims it again
if free. No CPU-return tags, original CPU identity, extra EIP or DNS edits.

`python3 studio workstation connect NAME` automatically selects the owner's trusted
hostname or a non-owner's current public IP. Use `hostname status` to inspect ownership,
`hostname assign NAME` for an intentional transfer or retry, and `hostname release`
to leave it unassigned. The optional `--hostname` flag checks ownership; it does not assign it.
An automatic-setup failure reports a warning and retry command, then verifies the
remaining endpoint before reporting readiness.

Every running desktop must retain a reachable raw-IP or trusted DCV endpoint.
Native public-IP recovery is checked after transfer, with bounded stop/start only
when needed and GPU restart headroom permits. Certificate warnings on raw-IP
connections are acceptable; blocked access is not.

See [Studio CLI](studio-cli.md#explicit-trusted-hostname) for the full owner, TLS,
rollback, race, permissions and acceptance contract. Historical commands are
retired and remain in Git history. The named lifecycle is merged in PR #116;
Issue #125 records the automatic-assignment correction and its acceptance evidence.
