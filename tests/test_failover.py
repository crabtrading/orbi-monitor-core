from __future__ import annotations

from pathlib import Path

from orbi_monitor_core.failover import (
    FailoverSettings,
    FailoverState,
    evaluate_transition,
    load_state,
    run_failover_once,
    upstream_snapshot,
)
from orbi_monitor_core.networking import DeviceConnection


def make_settings(tmp_path: Path) -> FailoverSettings:
    return FailoverSettings(
        primary_connection_name="Wired connection 1",
        failover_connection_name="Wired connection 2",
        primary_label="Primary WAN",
        failover_label="Secondary WAN",
        check_targets=("1.1.1.1", "8.8.8.8"),
        ping_count=2,
        ping_timeout_seconds=2,
        failure_threshold=2,
        recovery_threshold=2,
        primary_metric=100,
        failover_metric=50,
        standby_metric=20500,
        state_path=tmp_path / "failover-state.json",
    )


def test_switches_to_failover_after_threshold(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    state = FailoverState(active_mode="primary", failure_streak=1)

    next_state, action = evaluate_transition(
        state=state,
        primary_healthy=False,
        failover_available=True,
        settings=settings,
    )

    assert action == "switch_to_failover"
    assert next_state.active_mode == "failover"
    assert next_state.failure_streak == 0


def test_switches_back_to_primary_after_recovery_threshold(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    state = FailoverState(active_mode="failover", recovery_streak=1)

    next_state, action = evaluate_transition(
        state=state,
        primary_healthy=True,
        failover_available=True,
        settings=settings,
    )

    assert action == "switch_to_primary"
    assert next_state.active_mode == "primary"
    assert next_state.recovery_streak == 0


def test_run_failover_promotes_backup_when_primary_connection_disappears(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.failure_threshold = 1
    route_calls: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        "orbi_monitor_core.failover.active_connections",
        lambda: {
            settings.failover_connection_name: DeviceConnection(
                connection_name=settings.failover_connection_name,
                interface="enxbackup0",
                state="connected",
                device_type="ethernet",
            )
        },
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover.resolve_wan_connections",
        lambda **kwargs: (None, kwargs["connections"][settings.failover_connection_name]),
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover.default_gateway_for_interface",
        lambda interface: "10.127.52.232" if interface == "enxbackup0" else "",
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover._interface_healthy",
        lambda _settings, interface: (True, f"{interface} reached 1.1.1.1"),
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover._route_replace",
        lambda interface, gateway, metric: route_calls.append((interface, gateway, metric))
        if interface and gateway
        else None,
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover._detect_active_route",
        lambda *_: type("Route", (), {"interface": "enxbackup0", "gateway": "10.127.52.232", "source_ip": "10.127.52.5"})(),
    )

    payload = run_failover_once(settings)

    state = load_state(settings.state_path)
    assert payload["action"] == "switch_to_failover"
    assert state.active_mode == "failover"
    assert payload["active_label"] == "Secondary WAN"
    assert route_calls == [("enxbackup0", "10.127.52.232", settings.failover_metric)]


def test_upstream_snapshot_marks_failover_active(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    state = FailoverState(active_mode="failover", last_reason="switched to failover WAN")

    monkeypatch.setattr(
        "orbi_monitor_core.failover.active_connections",
        lambda: {
            settings.primary_connection_name: DeviceConnection(
                connection_name=settings.primary_connection_name,
                interface="enp10s0",
                state="connected",
                device_type="ethernet",
            ),
            settings.failover_connection_name: DeviceConnection(
                connection_name=settings.failover_connection_name,
                interface="enxbackup0",
                state="connected",
                device_type="ethernet",
            ),
        },
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover.resolve_wan_connections",
        lambda **kwargs: (
            kwargs["connections"][settings.primary_connection_name],
            kwargs["connections"][settings.failover_connection_name],
        ),
    )
    monkeypatch.setattr(
        "orbi_monitor_core.failover._detect_active_route",
        lambda *_: type("Route", (), {"interface": "enxbackup0", "gateway": "10.127.52.232", "source_ip": "10.127.52.5"})(),
    )

    payload = upstream_snapshot(settings, state=state)
    assert payload["mode"] == "failover_wan"
    assert payload["active_label"] == "Secondary WAN"
    assert payload["sources"][1]["active"] is True
    assert payload["failover"]["active_mode"] == "failover"
