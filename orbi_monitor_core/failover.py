from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from orbi_monitor_core.networking import (
    DeviceConnection,
    active_connections,
    default_gateway_for_interface,
    resolve_wan_connections,
)


DEVICE_RE = re.compile(r"\bdev\s+(?P<value>\S+)")
GATEWAY_RE = re.compile(r"\bvia\s+(?P<value>\S+)")
SOURCE_RE = re.compile(r"\bsrc\s+(?P<value>\S+)")


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _run_command(command: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@dataclass
class FailoverSettings:
    primary_connection_name: str
    failover_connection_name: str
    primary_label: str = "Primary WAN"
    failover_label: str = "Secondary WAN"
    check_targets: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")
    ping_count: int = 2
    ping_timeout_seconds: int = 2
    failure_threshold: int = 2
    recovery_threshold: int = 2
    primary_metric: int = 100
    failover_metric: int = 50
    standby_metric: int = 20500
    state_path: Path = Path("/var/lib/orbi-monitor-core/failover-state.json")


@dataclass
class FailoverState:
    active_mode: str = "primary"
    failure_streak: int = 0
    recovery_streak: int = 0
    last_reason: str = ""
    last_checked_at: str = ""
    last_switch_at: str = ""
    primary_interface: str = ""
    failover_interface: str = ""


@dataclass
class ActiveRoute:
    interface: str | None
    gateway: str
    source_ip: str


def _ping_target(
    *,
    interface: str | None,
    target: str,
    count: int,
    timeout_seconds: int,
) -> bool:
    command = ["ping"]
    if interface:
        command.extend(["-I", interface])
    command.extend(["-c", str(count), "-W", str(timeout_seconds), target])
    result = _run_command(command, timeout=max(10, count * timeout_seconds + 5))
    return result.returncode == 0


def _interface_healthy(settings: FailoverSettings, interface: str | None) -> tuple[bool, str]:
    if not interface:
        return False, "missing interface"
    for target in settings.check_targets:
        if _ping_target(
            interface=interface,
            target=target,
            count=settings.ping_count,
            timeout_seconds=settings.ping_timeout_seconds,
        ):
            return True, f"{interface} reached {target}"
    return False, f"{interface} failed {', '.join(settings.check_targets)}"


def load_state(path: Path) -> FailoverState:
    if not path.is_file():
        return FailoverState()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return FailoverState()
    return FailoverState(
        active_mode=str(payload.get("active_mode") or "primary"),
        failure_streak=int(payload.get("failure_streak") or 0),
        recovery_streak=int(payload.get("recovery_streak") or 0),
        last_reason=str(payload.get("last_reason") or ""),
        last_checked_at=str(payload.get("last_checked_at") or ""),
        last_switch_at=str(payload.get("last_switch_at") or ""),
        primary_interface=str(payload.get("primary_interface") or ""),
        failover_interface=str(payload.get("failover_interface") or ""),
    )


def save_state(path: Path, state: FailoverState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))


def _clear_default_routes(interface: str) -> None:
    if not interface:
        return
    while True:
        result = _run_command(["ip", "route", "del", "default", "dev", interface], timeout=10)
        if result.returncode != 0:
            break


def _route_replace(interface: str, gateway: str, metric: int) -> None:
    if not interface or not gateway:
        return
    _clear_default_routes(interface)
    _run_command(
        [
            "ip",
            "route",
            "replace",
            "default",
            "via",
            gateway,
            "dev",
            interface,
            "metric",
            str(metric),
        ],
        timeout=10,
    )


def _apply_primary_preferred(
    *,
    primary_interface: str,
    primary_gateway: str,
    failover_interface: str,
    failover_gateway: str,
    settings: FailoverSettings,
) -> None:
    _route_replace(primary_interface, primary_gateway, settings.primary_metric)
    _route_replace(failover_interface, failover_gateway, settings.standby_metric)


def _apply_failover_preferred(
    *,
    primary_interface: str,
    primary_gateway: str,
    failover_interface: str,
    failover_gateway: str,
    settings: FailoverSettings,
) -> None:
    _route_replace(primary_interface, primary_gateway, settings.primary_metric)
    _route_replace(failover_interface, failover_gateway, settings.failover_metric)


def decide_active_wan(
    *,
    state: FailoverState,
    primary_healthy: bool,
    failover_healthy: bool,
    settings: FailoverSettings,
) -> tuple[FailoverState, str]:
    now_text = utc_now().isoformat()
    next_state = FailoverState(**asdict(state))
    next_state.last_checked_at = now_text

    if state.active_mode == "failover":
        if primary_healthy:
            next_state.recovery_streak += 1
            next_state.failure_streak = 0
            next_state.last_reason = "primary healthy"
            if next_state.recovery_streak >= settings.recovery_threshold:
                next_state.active_mode = "primary"
                next_state.recovery_streak = 0
                next_state.last_switch_at = now_text
                next_state.last_reason = "recovered primary WAN"
                return next_state, "switch_to_primary"
            return next_state, "stay_failover"
        next_state.failure_streak += 1
        next_state.recovery_streak = 0
        next_state.last_reason = "primary still unhealthy"
        return next_state, "stay_failover"

    if primary_healthy:
        next_state.failure_streak = 0
        next_state.recovery_streak = 0
        next_state.last_reason = "primary healthy"
        return next_state, "stay_primary"

    next_state.failure_streak += 1
    next_state.recovery_streak = 0
    next_state.last_reason = "primary WAN unhealthy"
    if next_state.failure_streak >= settings.failure_threshold:
        if failover_healthy:
            next_state.active_mode = "failover"
            next_state.failure_streak = 0
            next_state.last_switch_at = now_text
            next_state.last_reason = "switched to failover WAN"
            return next_state, "switch_to_failover"
        return next_state, "no_failover_available"
    return next_state, "stay_primary"


def evaluate_transition(
    *,
    state: FailoverState,
    primary_healthy: bool,
    failover_available: bool,
    settings: FailoverSettings,
) -> tuple[FailoverState, str]:
    return decide_active_wan(
        state=state,
        primary_healthy=primary_healthy,
        failover_healthy=failover_available,
        settings=settings,
    )


def _detect_active_route(target: str = "8.8.8.8") -> ActiveRoute:
    result = _run_command(["ip", "route", "get", target], timeout=10)
    if result.returncode != 0:
        return ActiveRoute(interface=None, gateway="", source_ip="")

    output = result.stdout
    return ActiveRoute(
        interface=DEVICE_RE.search(output).group("value") if DEVICE_RE.search(output) else None,
        gateway=GATEWAY_RE.search(output).group("value") if GATEWAY_RE.search(output) else "",
        source_ip=SOURCE_RE.search(output).group("value") if SOURCE_RE.search(output) else "",
    )


def _build_source(
    *,
    source_id: str,
    label: str,
    role: str,
    connection_name: str,
    device: DeviceConnection | None,
    active_route: ActiveRoute,
) -> dict[str, object]:
    interface = device.interface if device else ""
    available = device.available if device else False
    active = bool(interface and active_route.interface and interface == active_route.interface)

    if active:
        status = "Active"
    elif available:
        status = "Standby"
    else:
        status = "Unavailable"

    return {
        "id": source_id,
        "label": label,
        "role": role,
        "mode": source_id,
        "connection_name": connection_name,
        "interface": interface,
        "device_type": device.device_type if device else "",
        "state": device.state if device else "disconnected",
        "available": available,
        "active": active,
        "status": status,
        "gateway": active_route.gateway if active else "",
        "source_ip": active_route.source_ip if active else "",
    }


def upstream_snapshot(settings: FailoverSettings, *, state: FailoverState | None = None) -> dict[str, object]:
    connections = active_connections()
    primary_device, failover_device = resolve_wan_connections(
        primary_connection_name=settings.primary_connection_name,
        failover_connection_name=settings.failover_connection_name,
        connections=connections,
    )
    active_route = _detect_active_route()

    primary = _build_source(
        source_id="primary_wan",
        label=settings.primary_label,
        role="Primary WAN",
        connection_name=primary_device.connection_name if primary_device else settings.primary_connection_name,
        device=primary_device,
        active_route=active_route,
    )
    failover = _build_source(
        source_id="failover_wan",
        label=settings.failover_label,
        role="Failover WAN",
        connection_name=failover_device.connection_name if failover_device else settings.failover_connection_name,
        device=failover_device,
        active_route=active_route,
    )
    sources = [primary, failover]
    active_source = next((source for source in sources if source["active"]), None)

    if active_source:
        active_label = str(active_source["label"])
        mode = str(active_source["mode"])
    elif active_route.interface:
        active_label = active_route.interface
        mode = "other_uplink"
    else:
        active_label = "No upstream"
        mode = "down"

    snapshot_state = state or load_state(settings.state_path)

    return {
        "checked_at": utc_now().isoformat(),
        "mode": mode,
        "active_label": active_label,
        "sources": sources,
        "failover": {
            "active_mode": snapshot_state.active_mode,
            "failure_streak": snapshot_state.failure_streak,
            "recovery_streak": snapshot_state.recovery_streak,
            "last_checked_at": snapshot_state.last_checked_at,
            "last_switch_at": snapshot_state.last_switch_at,
            "last_reason": snapshot_state.last_reason,
            "primary_interface": snapshot_state.primary_interface,
            "failover_interface": snapshot_state.failover_interface,
        },
    }


def run_failover_once(settings: FailoverSettings) -> dict[str, object]:
    connections = active_connections()
    primary, failover = resolve_wan_connections(
        primary_connection_name=settings.primary_connection_name,
        failover_connection_name=settings.failover_connection_name,
        connections=connections,
    )
    state = load_state(settings.state_path)

    state.primary_interface = primary.interface if primary and primary.interface else state.primary_interface
    state.failover_interface = (
        failover.interface if failover and failover.interface else state.failover_interface
    )

    primary_available = bool(primary and primary.available and primary.interface)
    failover_available = bool(failover and failover.available and failover.interface)
    primary_gateway = default_gateway_for_interface(primary.interface) if primary_available else ""
    failover_gateway = default_gateway_for_interface(failover.interface) if failover_available else ""

    if not primary_available:
        primary_healthy = False
        reason = "primary connection unavailable"
    else:
        primary_healthy, reason = _interface_healthy(settings, primary.interface)

    failover_healthy = False
    if failover_available:
        failover_healthy, _ = _interface_healthy(settings, failover.interface)

    next_state, action = decide_active_wan(
        state=state,
        primary_healthy=primary_healthy,
        failover_healthy=failover_healthy,
        settings=settings,
    )
    next_state.primary_interface = state.primary_interface
    next_state.failover_interface = state.failover_interface

    if action == "no_failover_available":
        next_state.last_reason = "primary WAN unhealthy and failover unavailable"
    elif action.startswith("stay_"):
        next_state.last_reason = reason

    if action in {"switch_to_primary", "stay_primary"}:
        _apply_primary_preferred(
            primary_interface=next_state.primary_interface,
            primary_gateway=primary_gateway,
            failover_interface=next_state.failover_interface,
            failover_gateway=failover_gateway,
            settings=settings,
        )
    elif action in {"switch_to_failover", "stay_failover"}:
        _apply_failover_preferred(
            primary_interface=next_state.primary_interface,
            primary_gateway=primary_gateway,
            failover_interface=next_state.failover_interface,
            failover_gateway=failover_gateway,
            settings=settings,
        )

    save_state(settings.state_path, next_state)
    payload = {
        "checked_at": utc_now().isoformat(),
        "action": action,
        "active_mode": next_state.active_mode,
        "primary_available": primary_available,
        "primary_healthy": primary_healthy,
        "failover_available": failover_available,
        "failover_healthy": failover_healthy,
        "primary_interface": next_state.primary_interface,
        "failover_interface": next_state.failover_interface,
        "primary_connection_name": primary.connection_name if primary else "",
        "failover_connection_name": failover.connection_name if failover else "",
        "reason": next_state.last_reason,
        "last_switch_at": next_state.last_switch_at,
    }
    payload.update(upstream_snapshot(settings, state=next_state))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a generic Linux primary/failover WAN policy check.")
    parser.add_argument("--primary-connection", required=True, help="Primary NetworkManager connection name")
    parser.add_argument("--failover-connection", required=True, help="Failover NetworkManager connection name")
    parser.add_argument("--primary-label", default="Primary WAN", help="Display label for the primary WAN")
    parser.add_argument("--failover-label", default="Secondary WAN", help="Display label for the failover WAN")
    parser.add_argument(
        "--check-target",
        action="append",
        dest="check_targets",
        default=[],
        help="ICMP target used for reachability checks; may be passed multiple times",
    )
    parser.add_argument("--ping-count", type=int, default=2, help="Ping count per health check target")
    parser.add_argument("--ping-timeout-seconds", type=int, default=2, help="Ping timeout per probe")
    parser.add_argument("--failure-threshold", type=int, default=2, help="Primary failures before failover")
    parser.add_argument("--recovery-threshold", type=int, default=2, help="Primary recoveries before fallback")
    parser.add_argument("--primary-metric", type=int, default=100, help="Metric when primary is preferred")
    parser.add_argument("--failover-metric", type=int, default=50, help="Metric when failover is active")
    parser.add_argument("--standby-metric", type=int, default=20500, help="Metric when failover is standby")
    parser.add_argument(
        "--state-path",
        default="/var/lib/orbi-monitor-core/failover-state.json",
        help="State file used to persist failover decisions",
    )
    parser.add_argument(
        "--mode",
        choices=("run", "status"),
        default="run",
        help="`run` applies the policy and updates the state; `status` only emits the current upstream view",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = FailoverSettings(
        primary_connection_name=args.primary_connection,
        failover_connection_name=args.failover_connection,
        primary_label=args.primary_label,
        failover_label=args.failover_label,
        check_targets=tuple(args.check_targets or ("1.1.1.1", "8.8.8.8")),
        ping_count=args.ping_count,
        ping_timeout_seconds=args.ping_timeout_seconds,
        failure_threshold=args.failure_threshold,
        recovery_threshold=args.recovery_threshold,
        primary_metric=args.primary_metric,
        failover_metric=args.failover_metric,
        standby_metric=args.standby_metric,
        state_path=Path(args.state_path),
    )

    if args.mode == "status":
        payload = upstream_snapshot(settings)
    else:
        payload = run_failover_once(settings)

    dump_kwargs = {"ensure_ascii": False}
    if args.pretty:
        dump_kwargs["indent"] = 2
    json.dump(payload, sys.stdout, **dump_kwargs)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
