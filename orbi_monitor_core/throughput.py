from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass


PING_PACKET_RE = re.compile(
    r"(?P<transmitted>\d+)\s+packets transmitted,\s+"
    r"(?P<received>\d+)\s+(?:packets )?received(?:,|\s+)"
    r".*?(?P<loss>[\d.]+)%\s+packet loss",
    re.IGNORECASE,
)
PING_RTT_RE = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max/(?:stddev|mdev)\s*=\s*"
    r"(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)/(?P<extra>[\d.]+)\s+ms",
    re.IGNORECASE,
)


@dataclass
class ThroughputSnapshot:
    probe_host: str
    source_mode: str
    ping_avg_ms: float | None
    ping_max_ms: float | None
    ping_loss_pct: float | None
    lan_forward_mbps: float | None
    lan_reverse_mbps: float | None
    wan_download_mbps: float | None
    wan_upload_mbps: float | None
    status: str
    error_message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_ping_output(output: str) -> tuple[float, float, float]:
    packet_match = PING_PACKET_RE.search(output)
    rtt_match = PING_RTT_RE.search(output)
    if not packet_match or not rtt_match:
        raise ValueError("Unable to parse ping output")
    return (
        float(rtt_match.group("avg")),
        float(rtt_match.group("max")),
        float(packet_match.group("loss")),
    )


def parse_iperf_output(output: str) -> float:
    payload = json.loads(output)
    end = payload.get("end") or {}
    candidates = [
        ((end.get("sum_received") or {}).get("bits_per_second")),
        ((end.get("sum") or {}).get("bits_per_second")),
        ((end.get("sum_sent") or {}).get("bits_per_second")),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        return round(float(candidate) / 1_000_000, 2)
    raise ValueError("iperf3 output missing throughput summary")


def parse_speedtest_output(output: str) -> tuple[float, float]:
    payload = json.loads(output)
    download = payload.get("download")
    upload = payload.get("upload")
    if download is None or upload is None:
        raise ValueError("speedtest output missing download/upload")
    return round(float(download) / 1_000_000, 2), round(float(upload) / 1_000_000, 2)


def _run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def measure_throughput(
    *,
    probe_host: str,
    probe_port: int = 5201,
    ping_count: int = 10,
    iperf_duration_seconds: int = 5,
    iperf_streams: int = 4,
    speedtest_command: list[str] | None = None,
    source_mode: str = "wifi_estimate",
) -> ThroughputSnapshot:
    ping_avg_ms: float | None = None
    ping_max_ms: float | None = None
    ping_loss_pct: float | None = None
    lan_forward_mbps: float | None = None
    lan_reverse_mbps: float | None = None
    wan_download_mbps: float | None = None
    wan_upload_mbps: float | None = None
    errors: list[str] = []

    try:
        result = _run_command(["ping", "-c", str(ping_count), probe_host], timeout=max(20, ping_count + 10))
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(result.stderr.strip() or "ping failed")
        ping_avg_ms, ping_max_ms, ping_loss_pct = parse_ping_output(result.stdout)
    except Exception as exc:
        errors.append(f"ping: {exc}")

    base_iperf = [
        "iperf3",
        "-c",
        probe_host,
        "-p",
        str(probe_port),
        "-t",
        str(iperf_duration_seconds),
        "-P",
        str(iperf_streams),
        "-J",
    ]
    try:
        result = _run_command(base_iperf, timeout=max(30, iperf_duration_seconds + 25))
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "iperf3 forward failed")
        lan_forward_mbps = parse_iperf_output(result.stdout)
    except Exception as exc:
        errors.append(f"iperf forward: {exc}")

    try:
        result = _run_command(base_iperf + ["-R"], timeout=max(30, iperf_duration_seconds + 25))
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "iperf3 reverse failed")
        lan_reverse_mbps = parse_iperf_output(result.stdout)
    except Exception as exc:
        errors.append(f"iperf reverse: {exc}")

    try:
        command = speedtest_command or ["python3", "-m", "speedtest", "--json", "--secure"]
        result = _run_command(command, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "speedtest failed")
        wan_download_mbps, wan_upload_mbps = parse_speedtest_output(result.stdout)
    except Exception as exc:
        errors.append(f"speedtest: {exc}")

    if lan_forward_mbps is not None and lan_reverse_mbps is not None and wan_download_mbps is not None:
        status = "ok"
    elif lan_forward_mbps is None and lan_reverse_mbps is None:
        status = "probe_unavailable"
    elif wan_download_mbps is None:
        status = "wan_unavailable"
    else:
        status = "partial"

    return ThroughputSnapshot(
        probe_host=probe_host,
        source_mode=source_mode,
        ping_avg_ms=ping_avg_ms,
        ping_max_ms=ping_max_ms,
        ping_loss_pct=ping_loss_pct,
        lan_forward_mbps=lan_forward_mbps,
        lan_reverse_mbps=lan_reverse_mbps,
        wan_download_mbps=wan_download_mbps,
        wan_upload_mbps=wan_upload_mbps,
        status=status,
        error_message="; ".join(errors),
    )
