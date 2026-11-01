"""Issue #43: optional, instance-pinned EC2 -> Route 53 A record reconciler.

Lambda entrypoint: runtime.dcv_dynamic_dns.lambda_handler. No RES imports.
An independent scheduled reconciliation is required: EC2 events may be delayed or missed.
"""
import ipaddress
import os

HOST = "dcv.kwesidavis.com."
ZONE = "Z02607163QPWX22FZ2Q9M"
REGION = "us-east-1"
TTL = 60


def _record(r53):
    response = r53.list_resource_record_sets(
        HostedZoneId=ZONE, StartRecordName=HOST, StartRecordType="A", MaxItems="1"
    )
    records = response.get("ResourceRecordSets", [])
    if records and records[0]["Name"].lower() == HOST and records[0]["Type"] == "A":
        record = records[0]
        if "AliasTarget" in record or len(record.get("ResourceRecords", [])) != 1:
            raise ValueError("Refusing to overwrite complex/alias DNS record")
        return record
    return None


def reconcile(event, ec2, route53, instance_id):
    """Return a status; never mutate for an unexpected owner or stale state event."""
    if not instance_id or not instance_id.startswith("i-"):
        raise ValueError("Pin a specific EC2 instance via STUDIO_DNS_INSTANCE_ID")
    if event.get("source") == "aws.ec2":
        if (event.get("detail-type") != "EC2 Instance State-change Notification"
                or event.get("region") != REGION
                or event.get("detail", {}).get("instance-id") != instance_id):
            return {"status": "ignored"}
    elif event.get("source") == "studio.dcv.reconcile":
        if event.get("instance-id") != instance_id:
            return {"status": "ignored"}
    else:
        return {"status": "ignored"}

    # Read *actual* EC2 state, never trust possibly out-of-order event state.
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [instance for res in response.get("Reservations", [])
                 for instance in res.get("Instances", [])]
    if len(instances) != 1 or instances[0].get("InstanceId") != instance_id:
        raise RuntimeError("Pinned instance not found; manual investigation required")
    instance = instances[0]
    tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
    if tags.get("StudioDcvDnsHost") != HOST.rstrip(".") or tags.get("StudioDcvDnsManaged") != "true":
        raise RuntimeError("Pinned instance missing exact DNS ownership tags")
    state = instance.get("State", {}).get("Name")
    if state not in ("running", "pending", "stopping", "stopped", "shutting-down", "terminated"):
        raise RuntimeError("Unrecognized EC2 state")

    existing = _record(route53)
    if state == "running":
        public_ip = instance.get("PublicIpAddress")
        if not public_ip:
            return {"status": "deferred", "reason": "no public IPv4 yet"}
        ip = ipaddress.ip_address(public_ip)
        if ip.version != 4 or not ip.is_global:
            raise RuntimeError("Instance does not have an eligible public IPv4")
        if existing and existing["ResourceRecords"][0]["Value"] == public_ip:
            return {"status": "unchanged"}
        desired = {"Name": HOST, "Type": "A", "TTL": TTL,
                   "ResourceRecords": [{"Value": public_ip}]}
        action = "UPSERT"
    elif state == "pending":
        return {"status": "deferred", "reason": "instance pending"}
    elif existing is not None:
        desired = existing  # DELETE requires exact previous record fields.
        action = "DELETE"
    else:
        return {"status": "unchanged", "reason": "offline and no A record"}

    route53.change_resource_record_sets(
        HostedZoneId=ZONE, ChangeBatch={"Comment": "Issue 43 pinned Studio DCV DNS",
                                       "Changes": [{"Action": action, "ResourceRecordSet": desired}]}
    )
    return {"status": "changed", "action": action, "instance_id": instance_id}


def lambda_handler(event, context):
    """Uses Lambda execution role, not workstation credentials."""
    import boto3
    instance_id = os.environ.get("STUDIO_DNS_INSTANCE_ID", "")
    return reconcile(event, boto3.client("ec2", region_name=REGION),
                     boto3.client("route53"), instance_id)
