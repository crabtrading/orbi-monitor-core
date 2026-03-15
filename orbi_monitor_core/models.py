from __future__ import annotations

from dataclasses import asdict, dataclass
from html import unescape
import re


MAC_ADDRESS_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return unescape(str(value)).strip()


def clean_int(value: object) -> int | None:
    text = clean_text(value)
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def clean_bool(value: object) -> bool | None:
    text = clean_text(value).lower()
    if text == "":
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def clean_mac(value: object) -> str:
    text = clean_text(value).upper()
    if MAC_ADDRESS_RE.fullmatch(text):
        return text
    return ""


def normalize_connection_type(value: object) -> str:
    text = clean_text(value)
    normalized = text.lower().replace("_", " ").replace("-", " ")
    normalized = " ".join(normalized.split())
    compact = normalized.replace(" ", "")

    if "wired" in normalized or "ethernet" in normalized:
        return "Wired"
    if "primary" in normalized:
        return "Primary"
    if "2.4" in normalized or compact.startswith("24") or "2g" in normalized:
        if "iot" in normalized or "iothz" in compact:
            return "2.4 GHz - IoT"
        return "2.4 GHz"
    if "5" in normalized and ("ghz" in normalized or "wireless" in normalized or "wifi" in normalized):
        return "5 GHz"
    if "6" in normalized and ("ghz" in normalized or "wireless" in normalized or "wifi" in normalized):
        return "6 GHz"
    return text or "Unknown"


@dataclass
class InternetStatus:
    code: int
    heading: str
    text: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class DeviceState:
    mac: str
    name: str
    ip: str
    name_user_set: bool | None
    type_name: str
    model: str
    model_user_set: bool | None
    brand: str
    connected_orbi: str
    connected_orbi_mac: str
    ap_mac: str
    connection_type: str
    ssid: str
    signal_strength: int | None
    linkspeed_mbps: int | None
    allow_or_block: str
    schedule_enabled: bool | None
    device_type: int | None
    device_type_user_set: bool | None
    device_type_v2: str
    device_type_name_v2: str
    upload: int | None
    download: int | None
    qos_priority: int | None
    grouping: int | None
    schedule_period: int | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SatelliteState:
    name: str
    ip: str
    mac: str
    model: str
    serial_number: str
    firmware_version: str
    device_name_user_set: bool | None
    connected_orbi: str
    connected_orbi_mac: str
    connection_type: str
    backhaul_status: str
    backhaul_conn_status: int | None
    signal_strength: int | None
    hop: int | None
    parent_mac: str
    lighting_led_supported: int | None
    lighting_led_on_off_status: int | None
    lighting_led_brightness_status: int | None
    avs_support: str
    status: str

    @property
    def healthy(self) -> bool:
        return self.status.lower() not in {"", "0", "down", "false", "offline"}

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["healthy"] = self.healthy
        return payload


@dataclass
class RouterSnapshot:
    internet: InternetStatus
    devices: list[DeviceState]
    satellites: list[SatelliteState]
    target_satellite_name: str
    expected_connection: str
    current_setting: dict[str, str]
    router_info: dict[str, object]
    support_features: dict[str, object]
    sources: dict[str, object]

    @property
    def target_satellite(self) -> SatelliteState | None:
        target = self.target_satellite_name.lower()
        for satellite in self.satellites:
            if satellite.name.lower() == target:
                return satellite
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "internet": self.internet.to_dict(),
            "devices": [device.to_dict() for device in self.devices],
            "satellites": [satellite.to_dict() for satellite in self.satellites],
            "target_satellite_name": self.target_satellite_name,
            "expected_connection": self.expected_connection,
            "current_setting": self.current_setting,
            "router_info": self.router_info,
            "support_features": self.support_features,
            "sources": self.sources,
        }

    @classmethod
    def from_payloads(
        cls,
        basic_payload: dict[str, object],
        attached_payload: dict[str, object],
        *,
        target_satellite_name: str,
        expected_connection: str,
        current_setting: dict[str, str] | None = None,
        router_info: dict[str, object] | None = None,
        support_features: dict[str, object] | None = None,
        sources: dict[str, object] | None = None,
        soap_devices: list[dict[str, object]] | None = None,
        soap_satellites: list[dict[str, object]] | None = None,
    ) -> "RouterSnapshot":
        internet = InternetStatus(
            code=int(basic_payload.get("internet", 0)),
            heading=clean_text(basic_payload.get("internet_head")),
            text=clean_text(basic_payload.get("internet_text")) or "Unknown",
        )

        soap_devices_by_mac: dict[str, dict[str, object]] = {}
        for raw in soap_devices or []:
            mac = clean_mac(raw.get("MAC"))
            if mac:
                soap_devices_by_mac[mac] = raw

        devices: list[DeviceState] = []
        for raw in attached_payload.get("devices") or []:
            mac = clean_text(raw.get("mac"))
            if not mac:
                continue
            soap_raw = soap_devices_by_mac.get(mac.upper(), {})
            devices.append(
                DeviceState(
                    mac=clean_mac(mac) or mac,
                    name=clean_text(soap_raw.get("Name")) or clean_text(raw.get("name")) or mac,
                    ip=clean_text(soap_raw.get("IP")) or clean_text(raw.get("ip")),
                    name_user_set=clean_bool(soap_raw.get("NameUserSet")),
                    type_name=clean_text(raw.get("typeName")) or clean_text(raw.get("type")),
                    model=clean_text(soap_raw.get("DeviceModel")) or clean_text(raw.get("model")),
                    model_user_set=clean_bool(soap_raw.get("DeviceModelUserSet")),
                    brand=clean_text(soap_raw.get("DeviceBrand")),
                    connected_orbi=clean_text(raw.get("ConnectedOrbi")) or "Unknown",
                    connected_orbi_mac=clean_mac(raw.get("ConnectedOrbiMAC"))
                    or clean_mac(soap_raw.get("ConnAPMAC")),
                    ap_mac=clean_mac(soap_raw.get("ConnAPMAC")),
                    connection_type=normalize_connection_type(
                        soap_raw.get("ConnectionType") or raw.get("connectionType")
                    ),
                    ssid=clean_text(soap_raw.get("SSID")),
                    signal_strength=clean_int(soap_raw.get("SignalStrength")),
                    linkspeed_mbps=clean_int(soap_raw.get("Linkspeed")),
                    allow_or_block=clean_text(soap_raw.get("AllowOrBlock")),
                    schedule_enabled=clean_bool(soap_raw.get("Schedule")),
                    device_type=clean_int(soap_raw.get("DeviceType")),
                    device_type_user_set=clean_bool(soap_raw.get("DeviceTypeUserSet")),
                    device_type_v2=clean_text(soap_raw.get("DeviceTypeV2")),
                    device_type_name_v2=clean_text(soap_raw.get("DeviceTypeNameV2")),
                    upload=clean_int(soap_raw.get("Upload")),
                    download=clean_int(soap_raw.get("Download")),
                    qos_priority=clean_int(soap_raw.get("QosPriority")),
                    grouping=clean_int(soap_raw.get("Grouping")),
                    schedule_period=clean_int(soap_raw.get("SchedulePeriod")),
                    status=clean_text(raw.get("status")) or "0",
                )
            )

        soap_satellites_by_name: dict[str, dict[str, object]] = {}
        for raw in soap_satellites or []:
            name = clean_text(raw.get("DeviceName") or raw.get("name")).lower()
            if name:
                soap_satellites_by_name[name] = raw

        satellites: list[SatelliteState] = []
        for raw in attached_payload.get("satellites") or []:
            soap_raw = soap_satellites_by_name.get((clean_text(raw.get("name")) or "Unknown").lower(), {})
            satellites.append(
                SatelliteState(
                    name=clean_text(soap_raw.get("DeviceName")) or clean_text(raw.get("name")) or "Unknown",
                    ip=clean_text(soap_raw.get("IP")) or clean_text(raw.get("ip")),
                    mac=clean_mac(soap_raw.get("MAC")) or clean_mac(raw.get("mac")),
                    model=clean_text(soap_raw.get("ModelName")) or clean_text(raw.get("model")),
                    serial_number=clean_text(soap_raw.get("SerialNumber")),
                    firmware_version=clean_text(soap_raw.get("FWVersion")),
                    device_name_user_set=clean_bool(soap_raw.get("DeviceNameUserSet")),
                    connected_orbi=clean_text(raw.get("ConnectedOrbi")) or "Unknown",
                    connected_orbi_mac=clean_mac(soap_raw.get("ParentMac"))
                    or clean_mac(raw.get("ConnectedOrbiMAC")),
                    connection_type=normalize_connection_type(
                        soap_raw.get("BHConnType") or raw.get("connectionType")
                    ),
                    backhaul_status=clean_text(raw.get("backhaulStatus")) or "Unknown",
                    backhaul_conn_status=clean_int(soap_raw.get("BHConnStatus")),
                    signal_strength=clean_int(soap_raw.get("SignalStrength")),
                    hop=clean_int(soap_raw.get("Hop")),
                    parent_mac=clean_mac(soap_raw.get("ParentMac")),
                    lighting_led_supported=clean_int(soap_raw.get("IsLightingLEDSupported")),
                    lighting_led_on_off_status=clean_int(soap_raw.get("LightingLEDOnOffStatus")),
                    lighting_led_brightness_status=clean_int(soap_raw.get("LightingLEDBrightnessStatus")),
                    avs_support=clean_text(soap_raw.get("AvsSupport")),
                    status=clean_text(raw.get("status")) or "0",
                )
            )

        devices.sort(key=lambda item: (item.connected_orbi.lower(), item.name.lower(), item.mac))
        satellites.sort(key=lambda item: item.name.lower())
        return cls(
            internet=internet,
            devices=devices,
            satellites=satellites,
            target_satellite_name=target_satellite_name,
            expected_connection=expected_connection,
            current_setting=current_setting or {},
            router_info=router_info or {},
            support_features=support_features or {},
            sources=sources or {},
        )
