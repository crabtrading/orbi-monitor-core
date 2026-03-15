from __future__ import annotations

import argparse
import json
import sys

from orbi_monitor_core.client import OrbiClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Orbi AJAX + SOAP state as JSON.")
    parser.add_argument("--host", default="http://192.168.1.1", help="Router base URL")
    parser.add_argument("--username", default="admin", help="Router admin username")
    parser.add_argument("--password", required=True, help="Router admin password")
    parser.add_argument(
        "--target-satellite-name",
        default="",
        help="Optional satellite name to track as the preferred wired node",
    )
    parser.add_argument(
        "--expected-connection",
        default="Wired",
        help="Expected backhaul type for the tracked satellite",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    client = OrbiClient(args.host, args.username, args.password)
    snapshot = client.fetch_snapshot(
        target_satellite_name=args.target_satellite_name,
        expected_connection=args.expected_connection,
    )

    payload = snapshot.to_dict()
    dump_kwargs = {"ensure_ascii": False}
    if args.pretty:
        dump_kwargs["indent"] = 2
    json.dump(payload, sys.stdout, **dump_kwargs)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
