# Independent DCV hostname

The shared `dcv.kwesidavis.com` EIP is account-owned, not CPU-owned. Use
`python3 studio workstation hostname status`, `hostname assign NAME`, and
`hostname release`. Create/start never claims it; stop/destroy releases only the
selected owner. No CPU-return tags, original CPU identity, extra EIP or DNS edits.

Every running desktop must retain a reachable raw-IP or trusted DCV endpoint.
Native public-IP recovery is checked after transfer, with bounded stop/start only
when needed and GPU restart headroom permits. Certificate warnings on raw-IP
connections are acceptable; blocked access is not.

See [Studio CLI](studio-cli.md#explicit-trusted-hostname) for the full owner, TLS,
rollback, race, permissions and acceptance contract. Historical commands are
retired and remain in Git history; use the integration branch until merged.
