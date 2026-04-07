#!/usr/bin/env python3
"""
lambda_terminate.py — Terminate Lambda Cloud instances.

Usage:
    # List currently running instances
    python lambda_terminate.py list

    # Terminate specific instances by ID
    python lambda_terminate.py terminate --ids i-abc123 i-def456

    # Terminate ALL running instances (prompts for confirmation)
    python lambda_terminate.py terminate --all [--yes]

    # Preview without actually terminating
    python lambda_terminate.py terminate --all --dry-run

Auth:
    LAMBDA_API_KEY      — Lambda Cloud API key (required)
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error


BASE_URL = "https://cloud.lambda.ai/api/v1"


def _api_key() -> str:
    key = os.environ.get("LAMBDA_API_KEY", "").strip()
    if not key:
        sys.exit("ERROR: LAMBDA_API_KEY environment variable is not set.")
    return key


def _auth_header() -> dict:
    token = base64.b64encode(f"{_api_key()}:".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "lambda-terminate/1.0",
    }


def _request(method: str, path: str, body: dict | None = None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_auth_header(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {body_text}") from exc


def list_instances() -> list[dict]:
    return _request("GET", "/instances").get("data", [])


def terminate_instances(instance_ids: list[str]) -> list[dict]:
    body = {"instance_ids": instance_ids}
    return _request("POST", "/instance-operations/terminate", body).get("data", {}).get("terminated_instances", [])


def _print_instances(instances: list[dict]) -> None:
    if not instances:
        print("(no instances)")
        return
    for inst in instances:
        iid = inst.get("id", "?")
        name = inst.get("name", "")
        itype = (inst.get("instance_type") or {}).get("name", "?")
        region = (inst.get("region") or {}).get("name", "?")
        ip = inst.get("ip", "N/A")
        status = inst.get("status", "?")
        print(f"  {iid}  {itype:<22} {region:<12} {status:<10} {ip:<16} {name}")


def cmd_list(_args) -> None:
    instances = list_instances()
    print(f"\n{len(instances)} instance(s):")
    _print_instances(instances)
    print()


def cmd_terminate(args) -> None:
    if args.all:
        instances = list_instances()
        ids = [i["id"] for i in instances if i.get("id")]
        if not ids:
            print("No running instances to terminate.")
            return
        print(f"Found {len(ids)} instance(s):")
        _print_instances(instances)
    else:
        ids = args.ids or []
        if not ids:
            sys.exit("ERROR: provide --ids or --all")

    if args.dry_run:
        print(f"\n[dry-run] Would terminate: {ids}")
        return

    if not args.yes:
        prompt = f"\nTerminate {len(ids)} instance(s)? [y/N] "
        if input(prompt).strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    print(f"Terminating {len(ids)} instance(s)…")
    terminated = terminate_instances(ids)
    print(f"OK — {len(terminated)} instance(s) terminated.")
    for t in terminated:
        print(f"  {t.get('id', '?')}  {t.get('name', '')}")


def parse_args():
    p = argparse.ArgumentParser(
        description="Terminate Lambda Cloud instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("list", help="List currently running instances.")

    term = sub.add_parser("terminate", help="Terminate one or more instances.")
    g = term.add_mutually_exclusive_group()
    g.add_argument("--ids", nargs="+", metavar="ID", help="Instance IDs to terminate.")
    g.add_argument("--all", action="store_true", help="Terminate ALL running instances.")
    term.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    term.add_argument("--dry-run", action="store_true", help="Show what would be terminated without doing it.")

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "terminate":
        cmd_terminate(args)
