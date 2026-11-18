"""Read-only DCV endpoint checks. IP certificate warnings never imply reachability."""
import http.client
import ipaddress
import ssl

from runtime import issue43_hostname_handoff as shared


def _probe(host, trusted):
    context = ssl.create_default_context() if trusted else ssl._create_unverified_context()
    connection = http.client.HTTPSConnection(host, 8443, timeout=5, context=context)
    try:
        connection.request("GET", "/")  # No credentials, redirects or instance mutations.
        response = connection.getresponse()
        if response.status != 200 or "text/html" not in response.getheader("Content-Type", "").lower():
            raise ValueError("DCV HTTPS endpoint did not return an HTML login page")
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError("DCV HTTPS login page unreachable: " + str(exc)) from exc
    finally:
        connection.close()


def resolve(state, region, aws, verify_hostname, *, hostname=None, probe=None):
    """Use freshly verified EC2 state, not historical names or CF output IPs."""
    if state.get("state") != "running" or not state.get("public_ip"):
        raise ValueError("No running public DCV endpoint; check EC2 addressing and TCP 8443 ingress")
    ip = str(ipaddress.IPv4Address(state["public_ip"]))
    owner = region == shared.REGION and ip == shared.EIP_IP
    if hostname is not None and (hostname != shared.HOST or not owner):
        raise ValueError("Requested trusted hostname does not belong to this workstation")
    if owner:
        address = shared._eip(aws)
        if (address.get("InstanceId") != state["instance"] or
                shared._tags(address).get("StudioDcvInstanceId") != state["instance"]):
            raise ValueError("Shared EIP ownership mismatch; refusing trusted DCV URL")
        url = verify_hostname(shared.HOST, ip)  # Never downgrade failed trusted TLS.
    else:
        url = "https://" + ip + ":8443"
    (probe or _probe)(shared.HOST if owner else ip, owner)
    return {"url": url, "trusted": owner, "reachable": True}
