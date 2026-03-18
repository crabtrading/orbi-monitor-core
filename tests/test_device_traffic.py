from __future__ import annotations

from orbi_monitor_core.device_traffic import build_device_traffic_payload


def test_build_device_traffic_payload_prefers_ip_fallback_for_suspect_macs(monkeypatch) -> None:
    raw_payload = {
        "checked_at": "2026-03-17T20:00:00+00:00",
        "poll_interval_seconds": 3,
        "mac_items": [
            {
                "mac": "AA:AA:AA:AA:AA:01",
                "download_bps": 128000,
                "upload_bps": 64000,
                "download_bytes_today": 1_048_576,
                "upload_bytes_today": 524_288,
                "total_bytes_today": 1_572_864,
                "active": True,
                "last_seen_at": "2026-03-17T20:00:00+00:00",
            },
            {
                "mac": "BB:BB:BB:BB:BB:BB",
                "download_bps": 512000,
                "upload_bps": 256000,
                "download_bytes_today": 9_999_999,
                "upload_bytes_today": 4_444_444,
                "total_bytes_today": 14_444_443,
                "active": True,
                "last_seen_at": "2026-03-17T20:00:00+00:00",
            },
        ],
        "ip_items": [
            {
                "family": "ipv4",
                "ip": "192.168.50.20",
                "observed_mac": "BB:BB:BB:BB:BB:BB",
                "download_bps": 100000,
                "upload_bps": 50000,
                "download_bytes_today": 1000,
                "upload_bytes_today": 500,
                "total_bytes_today": 1500,
                "active": True,
                "last_seen_at": "2026-03-17T20:00:00+00:00",
            },
            {
                "family": "ipv4",
                "ip": "192.168.50.30",
                "observed_mac": "BB:BB:BB:BB:BB:BB",
                "download_bps": 120000,
                "upload_bps": 60000,
                "download_bytes_today": 2000,
                "upload_bytes_today": 800,
                "total_bytes_today": 2800,
                "active": True,
                "last_seen_at": "2026-03-17T20:00:00+00:00",
            },
        ],
    }
    dashboard = {
        "devices": [
            {"mac": "AA:AA:AA:AA:AA:01", "ip": "192.168.50.10"},
            {"mac": "CC:CC:CC:CC:CC:20", "ip": "192.168.50.20"},
            {"mac": "DD:DD:DD:DD:DD:30", "ip": "192.168.50.30"},
        ],
        "satellites": [],
        "router_info": {},
        "current_setting": {},
    }

    monkeypatch.setattr("orbi_monitor_core.device_traffic._local_interface_macs", lambda: set())

    payload = build_device_traffic_payload(
        raw_payload,
        dashboard,
        upstream={"checked_at": "2026-03-17T20:00:00+00:00", "active_label": "Primary"},
    )

    items = {item["mac"]: item for item in payload["items"]}
    assert items["AA:AA:AA:AA:AA:01"]["download_bps"] == 128000
    assert items["CC:CC:CC:CC:CC:20"]["download_bps"] == 100000
    assert items["DD:DD:DD:DD:DD:30"]["upload_bps"] == 60000
    assert payload["unattributed"]["download_bps"] >= 512000
