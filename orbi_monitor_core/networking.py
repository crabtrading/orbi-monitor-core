from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class DeviceConnection:
    connection_name: str
    interface: str
    state: str
    device_type: str = ""

    @property
    def available(self) -> bool:
        return self.state.lower().startswith("connected")


def _run_command(command: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def active_connections() -> dict[str, DeviceConnection]:
    result = _run_command(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    if result.returncode != 0:
        return {}

    connections: dict[str, DeviceConnection] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        interface, device_type, state, connection_name = parts
        if not connection_name or connection_name == "--":
            continue
        connections[connection_name] = DeviceConnection(
            connection_name=connection_name,
            interface=interface,
            state=state,
            device_type=device_type,
        )
    return connections


def default_gateway_for_interface(interface: str) -> str:
    if not interface:
        return ""
    result = _run_command(["ip", "route", "show", "default", "dev", interface], timeout=10)
    if result.returncode != 0:
        return ""
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    return ""


def _prefer_failover_candidate(
    candidate: DeviceConnection,
    *,
    primary_interface: str,
    primary_gateway: str,
) -> tuple[int, int, int, int, str]:
    gateway = default_gateway_for_interface(candidate.interface)
    return (
        1 if gateway and gateway != primary_gateway else 0,
        1 if candidate.interface.startswith("enx") else 0,
        1 if candidate.device_type == "ethernet" else 0,
        1 if candidate.interface != primary_interface else 0,
        candidate.connection_name,
    )


def auto_discover_failover_connection(
    connections: dict[str, DeviceConnection],
    *,
    primary_connection_name: str,
    primary_interface: str = "",
    primary_gateway: str = "",
) -> DeviceConnection | None:
    candidates: list[DeviceConnection] = []
    for candidate in connections.values():
        if not candidate.available or not candidate.interface:
            continue
        if candidate.connection_name == primary_connection_name:
            continue
        if candidate.device_type != "ethernet":
            continue
        if candidate.interface in {primary_interface, "lo"}:
            continue
        gateway = default_gateway_for_interface(candidate.interface)
        if not gateway:
            continue
        if primary_gateway and gateway == primary_gateway:
            continue
        candidates.append(candidate)

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: _prefer_failover_candidate(
            candidate,
            primary_interface=primary_interface,
            primary_gateway=primary_gateway,
        ),
        reverse=True,
    )
    return candidates[0]


def resolve_wan_connections(
    *,
    primary_connection_name: str,
    failover_connection_name: str,
    connections: dict[str, DeviceConnection] | None = None,
) -> tuple[DeviceConnection | None, DeviceConnection | None]:
    resolved_connections = connections or active_connections()
    primary = resolved_connections.get(primary_connection_name)
    primary_interface = primary.interface if primary else ""
    primary_gateway = default_gateway_for_interface(primary_interface) if primary_interface else ""

    failover = resolved_connections.get(failover_connection_name)
    if failover and failover.available and failover.interface:
        return primary, failover

    discovered = auto_discover_failover_connection(
        resolved_connections,
        primary_connection_name=primary_connection_name,
        primary_interface=primary_interface,
        primary_gateway=primary_gateway,
    )
    return primary, discovered
