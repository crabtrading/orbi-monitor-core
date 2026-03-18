from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from orbi_monitor_core.models import clean_mac


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _run_command(command: list[str], *, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 127, stdout="", stderr="command unavailable")


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _blank_item(mac: str, ip: str = "") -> dict[str, object]:
    return {
        "mac": mac,
        "ip": ip,
        "download_bps": 0.0,
        "upload_bps": 0.0,
        "download_bytes_today": 0,
        "upload_bytes_today": 0,
        "total_bytes_today": 0,
        "active": False,
    }


def read_device_traffic_socket(socket_path: str | Path) -> dict[str, object] | None:
    path = Path(socket_path)
    if not path.exists():
        return None

    payload = bytearray()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(path))
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                payload.extend(chunk)
    except OSError:
        return None

    if not payload:
        return None

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _read_dnsmasq_leases(leases_path: str | Path | None) -> dict[str, str]:
    if not leases_path:
        return {}

    path = Path(leases_path)
    if not path.is_file():
        return {}

    leases: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        mac = clean_mac(parts[1])
        ip = parts[2].strip()
        if mac and ip:
            leases[ip] = mac
    return leases


def _read_ip_neighbors(lan_interface: str = "") -> dict[str, str]:
    command = ["ip", "neigh", "show"]
    if lan_interface:
        command.extend(["dev", lan_interface])
    result = _run_command(command)
    if result.returncode != 0:
        return {}

    neighbors: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        ip = parts[0].strip()
        try:
            lladdr_index = parts.index("lladdr")
        except ValueError:
            continue
        if lladdr_index + 1 >= len(parts):
            continue
        mac = clean_mac(parts[lladdr_index + 1])
        if mac:
            neighbors[ip] = mac
    return neighbors


def _local_interface_macs() -> set[str]:
    macs: set[str] = set()
    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return macs
    for address_file in net_dir.glob("*/address"):
        try:
            mac = clean_mac(address_file.read_text(encoding="utf-8"))
        except OSError:
            continue
        if mac:
            macs.add(mac)
    return macs


def _collect_macs_from_object(value: object) -> set[str]:
    macs: set[str] = set()
    if isinstance(value, dict):
        for nested in value.values():
            macs.update(_collect_macs_from_object(nested))
        return macs
    if isinstance(value, list):
        for nested in value:
            macs.update(_collect_macs_from_object(nested))
        return macs
    mac = clean_mac(value)
    if mac:
        macs.add(mac)
    return macs


def _dashboard_infra_macs(dashboard: dict[str, object]) -> set[str]:
    infra_macs: set[str] = set()
    for section_name in ("satellites", "router_info", "current_setting", "router", "nodes"):
        infra_macs.update(_collect_macs_from_object(dashboard.get(section_name)))
    return infra_macs


def _build_ip_to_mac_index(
    dashboard: dict[str, object], *, leases_path: str | Path | None, lan_interface: str
) -> dict[str, str]:
    ip_to_mac: dict[str, str] = {}

    for device in dashboard.get("devices") or []:
        if not isinstance(device, dict):
            continue
        ip = str(device.get("ip") or "").strip()
        mac = clean_mac(device.get("mac"))
        if ip and mac:
            ip_to_mac[ip] = mac

    for source in (_read_dnsmasq_leases(leases_path), _read_ip_neighbors(lan_interface)):
        for ip, mac in source.items():
            ip_to_mac.setdefault(ip, mac)

    return ip_to_mac


def _normalize_mac_item(item: dict[str, object]) -> dict[str, object]:
    return {
        "mac": clean_mac(item.get("mac")),
        "download_bps": _as_float(item.get("download_bps")),
        "upload_bps": _as_float(item.get("upload_bps")),
        "download_bytes_today": _as_int(item.get("download_bytes_today")),
        "upload_bytes_today": _as_int(item.get("upload_bytes_today")),
        "total_bytes_today": _as_int(item.get("total_bytes_today")),
        "last_seen_at": str(item.get("last_seen_at") or ""),
        "active": bool(item.get("active")),
    }


def _normalize_ip_item(item: dict[str, object]) -> dict[str, object]:
    return {
        "ip": str(item.get("ip") or "").strip(),
        "family": str(item.get("family") or "").strip().lower(),
        "observed_mac": clean_mac(item.get("observed_mac")),
        "download_bps": _as_float(item.get("download_bps")),
        "upload_bps": _as_float(item.get("upload_bps")),
        "download_bytes_today": _as_int(item.get("download_bytes_today")),
        "upload_bytes_today": _as_int(item.get("upload_bytes_today")),
        "total_bytes_today": _as_int(item.get("total_bytes_today")),
        "last_seen_at": str(item.get("last_seen_at") or ""),
        "active": bool(item.get("active")),
    }


def _merge_item(target: dict[str, object], source: dict[str, object]) -> None:
    target["download_bps"] = float(target["download_bps"]) + float(source["download_bps"])
    target["upload_bps"] = float(target["upload_bps"]) + float(source["upload_bps"])
    target["download_bytes_today"] = int(target["download_bytes_today"]) + int(
        source["download_bytes_today"]
    )
    target["upload_bytes_today"] = int(target["upload_bytes_today"]) + int(
        source["upload_bytes_today"]
    )
    target["total_bytes_today"] = int(target["total_bytes_today"]) + int(
        source["total_bytes_today"]
    )
    target["active"] = bool(target["active"] or source["active"])


def _detect_suspect_macs(
    mac_items: dict[str, dict[str, object]],
    ip_items: list[dict[str, object]],
    infra_macs: set[str],
    poll_seconds: int,
    checked_at: datetime,
) -> set[str]:
    suspect = set(infra_macs)
    active_ips: dict[str, set[str]] = defaultdict(set)
    window_seconds = max(2, poll_seconds * 2)

    for item in ip_items:
        mac = item["observed_mac"]
        ip = item["ip"]
        if not mac or not ip:
            continue
        seen_at = _parse_timestamp(item["last_seen_at"]) or checked_at
        age = (checked_at - seen_at).total_seconds()
        if age > window_seconds:
            continue
        active_ips[mac].add(ip)

    for mac, ips in active_ips.items():
        if len(ips) > 1:
            suspect.add(mac)

    for mac in mac_items:
        if mac in _local_interface_macs():
            suspect.add(mac)

    return suspect


def build_device_traffic_payload(
    raw_payload: dict[str, object] | None,
    dashboard: dict[str, object] | None = None,
    *,
    upstream: dict[str, object] | None = None,
    leases_path: str | Path | None = None,
    lan_interface: str = "",
    extra_infra_macs: set[str] | None = None,
) -> dict[str, object]:
    dashboard = dashboard or {}
    upstream = upstream or {}
    checked_at = str(upstream.get("checked_at") or _now().isoformat())
    poll_seconds = _as_int((raw_payload or {}).get("poll_interval_seconds") or 3)

    if raw_payload is None:
        return {
            "checked_at": checked_at,
            "poll_interval_seconds": poll_seconds,
            "upstream": upstream,
            "items": [],
            "unattributed": _blank_item("UNATTRIBUTED"),
            "error": "device traffic collector unavailable",
        }

    raw_checked_at = _parse_timestamp(raw_payload.get("checked_at")) or _now()
    mac_items = {
        item["mac"]: item
        for item in (
            _normalize_mac_item(raw)
            for raw in raw_payload.get("mac_items") or []
            if isinstance(raw, dict)
        )
        if item["mac"]
    }
    ip_items = [
        item
        for item in (
            _normalize_ip_item(raw)
            for raw in raw_payload.get("ip_items") or []
            if isinstance(raw, dict)
        )
        if item["ip"]
    ]

    ip_to_mac = _build_ip_to_mac_index(
        dashboard,
        leases_path=leases_path,
        lan_interface=lan_interface,
    )
    infra_macs = _dashboard_infra_macs(dashboard) | (extra_infra_macs or set())
    suspect_macs = _detect_suspect_macs(
        mac_items,
        ip_items,
        infra_macs,
        poll_seconds,
        raw_checked_at,
    )

    result: dict[str, dict[str, object]] = {}
    for device in dashboard.get("devices") or []:
        if not isinstance(device, dict):
            continue
        mac = clean_mac(device.get("mac"))
        if not mac:
            continue
        result[mac] = _blank_item(mac, str(device.get("ip") or ""))

    unattributed = _blank_item("UNATTRIBUTED")

    for mac, item in mac_items.items():
        if mac in suspect_macs:
            continue
        entry = result.setdefault(mac, _blank_item(mac))
        _merge_item(entry, item)

    for item in ip_items:
        resolved_mac = ip_to_mac.get(item["ip"], "")
        observed_mac = item["observed_mac"]

        if not resolved_mac:
            _merge_item(unattributed, item)
            continue

        if resolved_mac in mac_items and resolved_mac not in suspect_macs:
            continue

        entry = result.setdefault(resolved_mac, _blank_item(resolved_mac, item["ip"]))
        if not entry["ip"]:
            entry["ip"] = item["ip"]
        if observed_mac and observed_mac not in suspect_macs and observed_mac == resolved_mac:
            continue
        _merge_item(entry, item)

    for mac in suspect_macs:
        if mac in mac_items:
            _merge_item(unattributed, mac_items[mac])

    items = [
        {
            **item,
            "download_bps": round(float(item["download_bps"]), 2),
            "upload_bps": round(float(item["upload_bps"]), 2),
            "active": bool(item["active"] or item["download_bps"] or item["upload_bps"]),
        }
        for item in result.values()
        if item["total_bytes_today"] > 0 or item["download_bps"] > 0 or item["upload_bps"] > 0
    ]
    items.sort(key=lambda item: (-int(item["total_bytes_today"]), item["mac"]))

    return {
        "checked_at": str(raw_payload.get("checked_at") or checked_at),
        "poll_interval_seconds": poll_seconds,
        "upstream": upstream,
        "items": items,
        "unattributed": {
            **unattributed,
            "download_bps": round(float(unattributed["download_bps"]), 2),
            "upload_bps": round(float(unattributed["upload_bps"]), 2),
        },
        "error": str(raw_payload.get("error") or ""),
    }


def _load_json_file(path: str | Path | None) -> dict[str, object]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and normalize device traffic from a Unix socket.")
    parser.add_argument(
        "--socket-path",
        default="/run/orbi-monitor-core/device-traffic.sock",
        help="Unix socket exposed by the native collector",
    )
    parser.add_argument(
        "--dashboard-json",
        default="",
        help="Optional router snapshot JSON used for MAC/IP attribution",
    )
    parser.add_argument(
        "--upstream-json",
        default="",
        help="Optional upstream/failover metadata JSON to inline in the output",
    )
    parser.add_argument(
        "--leases-path",
        default="",
        help="Optional dnsmasq leases file used as an IP->MAC fallback",
    )
    parser.add_argument(
        "--lan-interface",
        default="",
        help="Optional LAN interface for querying `ip neigh` as a fallback",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    raw_payload = read_device_traffic_socket(args.socket_path)
    dashboard = _load_json_file(args.dashboard_json)
    upstream = _load_json_file(args.upstream_json)
    payload = build_device_traffic_payload(
        raw_payload,
        dashboard,
        upstream=upstream,
        leases_path=args.leases_path or None,
        lan_interface=args.lan_interface,
    )

    dump_kwargs = {"ensure_ascii": False}
    if args.pretty:
        dump_kwargs["indent"] = 2
    json.dump(payload, sys.stdout, **dump_kwargs)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
