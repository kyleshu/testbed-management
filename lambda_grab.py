#!/usr/bin/env python3
"""
lambda_grab.py — Poll Lambda Cloud for instance availability and auto-launch.

Usage:
    python lambda_grab.py \
        --instance-types gpu_8x_h100_sxm4 gpu_8x_a100 \
        --count 2 \
        --ssh-key my-key-name \
        [--extra-ssh-keys key2 key3] \
        [--ssh-key-path ~/.ssh/id_rsa] \
        [--region us-west-1] \
        [--poll-interval 30] \
        [--name my-experiment] \
        [--filesystems fs-abc123] \
        [--dry-run]

    --ssh-key is used for the launch request (Lambda only supports one key at
    launch time). Once instances are active, --extra-ssh-keys are fetched by
    name from the Lambda API and injected into each node's authorized_keys.

Notifications (optional env vars):
    SLACK_WEBHOOK_URL   — post to a Slack channel when instances are grabbed
    NOTIFY_EMAIL        — recipient address (also requires SMTP_* vars below)
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS — SMTP credentials

Auth:
    LAMBDA_API_KEY      — Lambda Cloud API key (required)
"""

import argparse
import json
import os
import shlex
import smtplib
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://cloud.lambda.ai/api/v1"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _api_key() -> str:
    key = os.environ.get("LAMBDA_API_KEY", "").strip()
    if not key:
        sys.exit("ERROR: LAMBDA_API_KEY environment variable is not set.")
    return key


def _auth_header() -> dict:
    import base64
    token = base64.b64encode(f"{_api_key()}:".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "lambda-grab/1.0",
    }


def _request(method: str, path: str, body: dict | None = None, dry_run: bool = False):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None

    if dry_run and method != "GET":
        print(f"  [dry-run] {method} {url}")
        if body:
            print(f"           {json.dumps(body, indent=2)}")
        return {}

    req = urllib.request.Request(url, data=data, headers=_auth_header(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {body_text}") from exc


def list_instance_types() -> dict:
    """Returns {instance_type_name: {regions_with_capacity_available: [...], ...}}"""
    result = _request("GET", "/instance-types")
    return result.get("data", {})


def launch_instances(
    instance_type: str,
    region: str,
    count: int,
    ssh_key: str,
    name: str,
    filesystem_ids: list[str],
    dry_run: bool = False,
) -> list[str]:
    """Launch `count` instances and return their IDs."""
    # Lambda Cloud only supports a single SSH key per launch request.
    body = {
        "instance_type_name": instance_type,
        "region_name": region,
        "ssh_key_names": [ssh_key],
        "quantity": count,
        "name": name,
    }
    if filesystem_ids:
        body["file_system_names"] = filesystem_ids

    result = _request("POST", "/instance-operations/launch", body, dry_run=dry_run)
    return result.get("data", {}).get("instance_ids", [])


def get_instance(instance_id: str) -> dict:
    result = _request("GET", f"/instances/{instance_id}")
    return result.get("data", {})


def list_ssh_keys() -> list[dict]:
    """Return all SSH keys registered on the Lambda account.

    Each entry is a dict with 'id', 'name', and 'public_key'.
    """
    result = _request("GET", "/ssh-keys")
    return result.get("data", [])


def get_public_keys_by_name(key_names: list[str]) -> list[str]:
    """Look up public key material for the given key names from Lambda.

    Returns a list of public key strings (one per matched name).
    Warns and skips any names not found in the account.
    """
    all_keys = list_ssh_keys()
    by_name = {k["name"]: k["public_key"] for k in all_keys if "name" in k and "public_key" in k}

    public_keys = []
    for name in key_names:
        if name in by_name:
            public_keys.append(by_name[name])
        else:
            available = list(by_name.keys())
            print(f"  [warn] SSH key '{name}' not found in Lambda account. Available: {available}")
    return public_keys


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def find_candidate(
    instance_types: dict,
    wanted: list[str],
    preferred_region: str | None,
) -> tuple[str, str] | None:
    """
    Return (instance_type, region) for the first entry in `wanted` that has
    capacity. Respects preferred_region if supplied.
    """
    for type_name in wanted:
        info = instance_types.get(type_name)
        if not info:
            continue
        regions = [r.get("name", "") for r in info.get("regions_with_capacity_available", [])]
        if not regions:
            continue
        if preferred_region and preferred_region in regions:
            return type_name, preferred_region
        return type_name, regions[0]
    return None


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(message: str) -> None:
    """Print, ring the terminal bell, and fire optional webhook/email."""
    print(f"\n{'='*60}")
    print(f"  NOTIFICATION [{_ts()}]")
    print(f"  {message}")
    print(f"{'='*60}\n")
    print("\a", end="", flush=True)  # terminal bell

    # Slack
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            payload = json.dumps({"text": f":gpu: {message}"}).encode()
            req = urllib.request.Request(
                webhook, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            print("  [notified via Slack]")
        except Exception as exc:
            print(f"  [Slack notification failed: {exc}]")

    # Email
    to_addr = os.environ.get("NOTIFY_EMAIL", "").strip()
    if to_addr:
        try:
            smtp_host = os.environ.get("SMTP_HOST", "localhost")
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_pass = os.environ.get("SMTP_PASS", "")
            msg = MIMEText(message)
            msg["Subject"] = "Lambda Cloud: instances grabbed"
            msg["From"] = smtp_user or "lambda_grab@localhost"
            msg["To"] = to_addr
            with smtplib.SMTP(smtp_host, smtp_port) as s:
                s.ehlo()
                if smtp_port != 25:
                    s.starttls()
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.sendmail(msg["From"], [to_addr], msg.as_string())
            print(f"  [notified via email → {to_addr}]")
        except Exception as exc:
            print(f"  [email notification failed: {exc}]")


# ---------------------------------------------------------------------------
# Wait for instances to reach running state
# ---------------------------------------------------------------------------

def wait_for_instances(instance_ids: list[str], timeout: int = 600) -> list[dict]:
    """Poll until all instances are 'active', return their info dicts."""
    print(f"Waiting for {len(instance_ids)} instance(s) to become active…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        infos = [get_instance(iid) for iid in instance_ids]
        statuses = [i.get("status", "unknown") for i in infos]
        print(f"  [{_ts()}] statuses: {statuses}")
        if all(s == "active" for s in statuses):
            return infos
        if any(s in ("error", "terminated") for s in statuses):
            raise RuntimeError(f"One or more instances failed: {statuses}")
        time.sleep(15)
    raise TimeoutError("Instances did not become active within timeout.")


# ---------------------------------------------------------------------------
# Extra SSH key injection (post-launch)
# ---------------------------------------------------------------------------

def inject_extra_ssh_keys(
    instances: list[dict],
    extra_public_keys: list[str],
    ssh_key_path: str | None = None,
) -> None:
    """Append extra public keys to authorized_keys on each active instance.

    Connects via SSH using the launch key (from the agent or --ssh-key-path)
    and pipes the key material into ~/.ssh/authorized_keys.
    """
    if not extra_public_keys:
        return

    keys_block = "\n".join(extra_public_keys) + "\n"
    print(f"\nInjecting {len(extra_public_keys)} extra SSH key(s) into {len(instances)} instance(s)…")

    for inst in instances:
        ip = inst.get("ip")
        if not ip:
            print(f"  [warn] Instance {inst.get('id')} has no public IP, skipping key injection")
            continue

        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
        ]
        if ssh_key_path:
            ssh_cmd += ["-i", ssh_key_path]
        ssh_cmd += [f"ubuntu@{ip}", "tee -a ~/.ssh/authorized_keys > /dev/null"]

        print(f"  {ip} … ", end="", flush=True)
        try:
            subprocess.run(
                ssh_cmd,
                input=keys_block,
                text=True,
                check=True,
                timeout=60,
            )
            print("ok")
        except subprocess.CalledProcessError as exc:
            print(f"failed (exit {exc.returncode})")
            print(f"    [warn] Could not inject keys into {ip}. You can do it manually:")
            for key in extra_public_keys:
                print(f"    echo {shlex.quote(key)} >> ~/.ssh/authorized_keys")
        except subprocess.TimeoutExpired:
            print("timed out")


# ---------------------------------------------------------------------------
# Private network setup
# ---------------------------------------------------------------------------

def print_network_setup(instances: list[dict], name: str) -> str:
    """
    Print /etc/hosts setup instructions for the private network.
    Lambda instances in the same region share a 10.x.x.x private network.
    Returns a summary string for notifications.
    """
    if len(instances) <= 1:
        return ""

    lines = [
        "",
        "Private network setup",
        "─" * 40,
        "All instances share Lambda's private 10.x.x.x network.",
        "Add the following to /etc/hosts on EACH instance:",
        "",
    ]

    hosts_entries = []
    for i, inst in enumerate(instances):
        private_ip = inst.get("private_ip") or inst.get("ip", "N/A")
        hostname = f"{name}-{i}"
        hosts_entries.append(f"{private_ip}  {hostname}")
        lines.append(f"  {private_ip}  {hostname}")

    lines += [
        "",
        "Run this one-liner on each instance to populate /etc/hosts:",
        "",
        "  sudo tee -a /etc/hosts << 'EOF'",
    ]
    for entry in hosts_entries:
        lines.append(f"  {entry}")
    lines += ["  EOF", ""]

    lines.append("Verify connectivity (from any instance):")
    for i in range(len(instances)):
        lines.append(f"  ping -c1 {name}-{i}")

    print("\n".join(lines))
    return "\n".join(hosts_entries)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def cmd_list_types(args) -> None:
    instance_types = list_instance_types()
    rows = []
    for type_name, info in instance_types.items():
        specs = info.get("instance_type", {})
        gpus = specs.get("gpus", 0)
        vcpus = specs.get("vcpus", "?")
        mem = specs.get("memory_gib", "?")
        regions = [r.get("name", "") for r in info.get("regions_with_capacity_available", [])]
        rows.append((type_name, gpus, vcpus, mem, regions))

    # Sort by GPU count descending, then name
    rows.sort(key=lambda r: (-r[1], r[0]))

    col_w = max(len(r[0]) for r in rows) + 2
    print(f"\n{'INSTANCE TYPE':<{col_w}} {'GPUs':>4}  {'vCPUs':>5}  {'RAM (GiB)':>9}  AVAILABLE REGIONS")
    print("─" * (col_w + 50))
    for type_name, gpus, vcpus, mem, regions in rows:
        avail = ", ".join(regions) if regions else "—"
        print(f"{type_name:<{col_w}} {gpus:>4}  {vcpus:>5}  {mem:>9}  {avail}")
    print()


def poll_and_launch(args) -> None:
    wanted = args.instance_types
    count = args.count
    name = args.name or f"exp-{int(time.time())}"

    print(f"[{_ts()}] Polling Lambda Cloud for {count}× one of: {wanted}")
    if args.region:
        print(f"  Preferred region: {args.region}")
    print(f"  Poll interval: {args.poll_interval}s")
    if args.dry_run:
        print("  DRY RUN — no instances will actually be launched.")
    print()

    all_instances: list[dict] = []
    locked_type: str | None = None
    locked_region: str | None = None

    for instance_num in range(1, count + 1):
        print(f"[{_ts()}] Acquiring instance {instance_num}/{count}…")
        attempt = 0
        while True:
            attempt += 1
            try:
                instance_types = list_instance_types()

                if locked_type and locked_region:
                    # Stay on the same type+region for consistency
                    info = instance_types.get(locked_type)
                    regions = [r.get("name", "") for r in info.get("regions_with_capacity_available", [])] if info else []
                    match = (locked_type, locked_region) if locked_region in regions else None
                    if not match:
                        print(f"[{_ts()}] Attempt {attempt}: {locked_type} in {locked_region} no longer has capacity. Retrying in {args.poll_interval}s…")
                else:
                    match = find_candidate(instance_types, wanted, args.region)
                    if not match:
                        print(f"[{_ts()}] Attempt {attempt}: no capacity for {wanted}. Retrying in {args.poll_interval}s…")

                if match:
                    type_name, region = match
                    if locked_type is None:
                        locked_type = type_name
                        locked_region = region

                    print(f"[{_ts()}] CAPACITY FOUND: {type_name} in {region}")
                    print(f"Launching instance {instance_num}/{count}…")

                    instance_ids = launch_instances(
                        instance_type=type_name,
                        region=region,
                        count=1,
                        ssh_key=args.ssh_key,
                        name=name,
                        filesystem_ids=args.filesystems or [],
                        dry_run=args.dry_run,
                    )

                    if args.dry_run:
                        print(f"  [dry-run] Launch call complete for instance {instance_num}/{count}.")
                        break

                    print(f"  Launched instance ID: {instance_ids}")

                    instances = wait_for_instances(instance_ids)
                    inst = instances[0]
                    all_instances.append(inst)

                    # Inject extra SSH keys fetched by name from Lambda
                    if getattr(args, "extra_ssh_keys", None):
                        extra_public_keys = get_public_keys_by_name(args.extra_ssh_keys)
                        inject_extra_ssh_keys(
                            instances,
                            extra_public_keys,
                            ssh_key_path=getattr(args, "ssh_key_path", None),
                        )

                    ip = inst.get("ip", "N/A")
                    priv = inst.get("private_ip", "N/A")
                    iid = inst.get("id", "?")
                    notify(
                        f"Grabbed instance {instance_num}/{count}: {type_name} in {region}\n"
                        f"  ID: {iid}  public={ip}  private={priv}\n"
                        f"  SSH: ssh ubuntu@{ip}"
                    )
                    break

            except KeyboardInterrupt:
                print("\nInterrupted by user. Exiting.")
                if all_instances:
                    print(f"Grabbed {len(all_instances)}/{count} instance(s) before interruption.")
                return
            except Exception as exc:
                print(f"[{_ts()}] Error (will retry): {exc}")

            time.sleep(args.poll_interval)

    if args.dry_run:
        print(f"  [dry-run] All {count} launch call(s) complete. Exiting.")
        return

    if count > 1 and all_instances:
        net_info = print_network_setup(all_instances, name)
        if net_info:
            notify(f"Private network — add to /etc/hosts on each node:\n{net_info}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Poll Lambda Cloud and auto-grab instances when capacity appears.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command")

    # --- list ---
    sub.add_parser("list", help="List all instance types and their current availability.")

    # --- grab ---
    grab = sub.add_parser("grab", help="Poll for capacity and launch instances.")
    grab.add_argument(
        "--instance-types", nargs="+", required=True, metavar="TYPE",
        help="Ordered list of acceptable instance types. First with capacity wins.",
    )
    grab.add_argument(
        "--count", type=int, required=True,
        help="Number of instances to launch.",
    )
    grab.add_argument(
        "--ssh-key", required=True, metavar="KEY",
        help="SSH key name registered on Lambda Cloud used for the launch request (Lambda only supports one key at launch).",
    )
    grab.add_argument(
        "--extra-ssh-keys", nargs="+", default=[], metavar="KEY",
        help="Additional SSH key names registered on Lambda Cloud to inject into each instance after launch.",
    )
    grab.add_argument(
        "--ssh-key-path", default=None, metavar="PATH",
        help="Path to the private key file for --ssh-key (used to connect when injecting extra keys). Defaults to your SSH agent.",
    )
    grab.add_argument(
        "--region", default=None,
        help="Preferred region (e.g. us-west-1). Falls back to any region if unavailable.",
    )
    grab.add_argument(
        "--poll-interval", type=int, default=30, metavar="SECONDS",
        help="How often to poll the API (default: 30s).",
    )
    grab.add_argument(
        "--name", default=None,
        help="Base name for the instances (default: exp-<timestamp>).",
    )
    grab.add_argument(
        "--filesystems", nargs="*", default=[],
        help="Lambda filesystem names to attach (optional).",
    )
    grab.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without actually launching instances.",
    )

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.command == "list":
        cmd_list_types(args)
    elif args.command == "grab":
        poll_and_launch(args)
