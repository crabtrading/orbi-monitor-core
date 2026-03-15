from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from orbi_monitor_core.models import RouterSnapshot


SOAP_SESSION_ID = "A7D88AE69687E58D9A00"
SETTING_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


class OrbiClient:
    def __init__(self, router_url: str, username: str, password: str, timeout: int = 20) -> None:
        self.router_url = router_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._ssl_context = ssl._create_unverified_context()
        parsed = urllib.parse.urlparse(self.router_url)
        self._host = parsed.hostname or self.router_url.replace("http://", "").replace("https://", "")

    def _build_ajax_opener(self) -> tuple[urllib.request.OpenerDirector, str]:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPSHandler(context=self._ssl_context),
        )
        opener.addheaders = [("User-Agent", "Mozilla/5.0")]

        try:
            opener.open(f"{self.router_url}/start.htm", timeout=5)
        except Exception:
            pass

        token = next((cookie.value for cookie in jar if cookie.name == "XSRF_TOKEN"), "")
        return opener, token

    def _build_soap_opener(self) -> urllib.request.OpenerDirector:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPSHandler(context=self._ssl_context),
        )
        opener.addheaders = [("User-Agent", "orbi-monitor-core")]
        return opener

    def _post_json(
        self, opener: urllib.request.OpenerDirector, token: str, path: str
    ) -> dict[str, object]:
        request = urllib.request.Request(f"{self.router_url}{path}", data=b"")
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        request.add_header("Authorization", f"Basic {auth}")
        if token:
            request.add_header("X-XSRF-TOKEN", token)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def _fetch_current_setting(self) -> dict[str, str]:
        request = urllib.request.Request(
            f"{self.router_url}/currentsetting.htm",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read().decode("utf-8", "replace")

        settings: dict[str, str] = {}
        for line in payload.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            if not SETTING_KEY_RE.fullmatch(normalized_key):
                continue
            settings[normalized_key] = value.strip()
        return settings

    def _soap_envelope(self, body: str) -> bytes:
        return (
            f'<!--?xml version="1.0" encoding= "UTF-8" ?-->'
            f'<v:Envelope xmlns:v="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<v:Header><SessionID>{SOAP_SESSION_ID}</SessionID></v:Header>"
            f"<v:Body>{body}</v:Body>"
            f"</v:Envelope>"
        ).encode("utf-8")

    def _soap_request(
        self,
        opener: urllib.request.OpenerDirector,
        *,
        port: int,
        tls: bool,
        action: str,
        body: str,
    ) -> str:
        scheme = "https" if tls else "http"
        request = urllib.request.Request(
            f"{scheme}://{self._host}:{port}/soap/server_sa/",
            data=self._soap_envelope(body),
            method="POST",
            headers={
                "soapaction": action,
                "cache-control": "no-cache",
                "user-agent": "orbi-monitor-core",
                "content-type": "multipart/form-data",
            },
        )
        with opener.open(request, timeout=self.timeout) as response:
            payload = response.read().decode("utf-8", "replace")

        response_code = self._find_text(ET.fromstring(payload), "ResponseCode") or ""
        if response_code not in {"0", "000"}:
            raise RuntimeError(f"SOAP action failed: {action} response={response_code or 'missing'}")
        return payload

    def _soap_login(self, opener: urllib.request.OpenerDirector, *, port: int, tls: bool) -> None:
        body = (
            '<M1:SOAPLogin xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceConfig:1">'
            f"<Username>{self.username}</Username>"
            f"<Password>{self.password}</Password>"
            "</M1:SOAPLogin>"
        )
        self._soap_request(
            opener,
            port=port,
            tls=tls,
            action="urn:NETGEAR-ROUTER:service:DeviceConfig:1#SOAPLogin",
            body=body,
        )

    def _find_text(self, root: ET.Element, tag: str) -> str:
        for element in root.iter():
            if element.tag.split("}", 1)[-1] == tag:
                return (element.text or "").strip()
        return ""

    def _element_to_data(self, element: ET.Element) -> object:
        children = list(element)
        if not children:
            return (element.text or "").strip()

        result: dict[str, object] = {}
        for child in children:
            key = child.tag.split("}", 1)[-1]
            value = self._element_to_data(child)
            existing = result.get(key)
            if existing is None:
                result[key] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                result[key] = [existing, value]
        return result

    def _find_response_payload(self, xml_payload: str) -> dict[str, object]:
        root = ET.fromstring(xml_payload)
        for element in root.iter():
            if element.tag.split("}", 1)[-1] != "Body":
                continue
            for child in list(element):
                if child.tag.split("}", 1)[-1] == "ResponseCode":
                    continue
                payload = self._element_to_data(child)
                if isinstance(payload, dict):
                    return payload
                return {"value": payload}
        return {}

    def _find_collection(
        self, xml_payload: str, *, container_tag: str, item_tag: str
    ) -> list[dict[str, object]]:
        root = ET.fromstring(xml_payload)
        items: list[dict[str, object]] = []

        for container in root.iter():
            if container.tag.split("}", 1)[-1] != container_tag:
                continue
            for child in list(container):
                if child.tag.split("}", 1)[-1] != item_tag:
                    continue
                payload: dict[str, object] = {}
                for field in list(child):
                    payload[field.tag.split("}", 1)[-1]] = (field.text or "").strip()
                if payload:
                    items.append(payload)
        return items

    def _as_list(self, value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    def _fetch_soap_state(
        self,
    ) -> tuple[
        dict[str, str],
        dict[str, object],
        dict[str, object],
        dict[str, object],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        current = self._fetch_current_setting()
        port = int(current.get("SOAP_HTTPs_Port") or current.get("SOAP_Port") or "5000")
        tls = bool(current.get("SOAP_HTTPs_Port")) or port in {443, 5043, 5555}

        opener = self._build_soap_opener()
        self._soap_login(opener, port=port, tls=tls)

        actions = {
            "get_info": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetInfo",
                '<M1:GetInfo xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_support_feature_list_xml": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetSupportFeatureListXML",
                '<M1:GetSupportFeatureListXML xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_attach_device2": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetAttachDevice2",
                '<M1:GetAttachDevice2 xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_current_satellites": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetCurrentSatellites",
                '<M1:GetCurrentSatellites xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_all_satellites": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetAllSatellites",
                '<M1:GetAllSatellites xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_missing_satellites": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetMissingSatellites",
                '<M1:GetMissingSatellites xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
            "get_current_satellites_wifi_info": (
                "urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetCurrentSatellitesWIFIinfo",
                '<M1:GetCurrentSatellitesWIFIinfo xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />',
            ),
        }

        soap_payloads: dict[str, object] = {}
        soap_errors: dict[str, str] = {}
        for name, (action, body) in actions.items():
            try:
                xml = self._soap_request(opener, port=port, tls=tls, action=action, body=body)
                soap_payloads[name] = self._find_response_payload(xml)
            except Exception as error:
                soap_errors[name] = str(error)

        support_features = {}
        support_root = soap_payloads.get("get_support_feature_list_xml")
        if isinstance(support_root, dict):
            feature_list = support_root.get("newFeatureList")
            if isinstance(feature_list, dict):
                features = feature_list.get("features")
                if isinstance(features, dict):
                    support_features = features

        devices = self._as_list(
            ((soap_payloads.get("get_attach_device2") or {}).get("NewAttachDevice") or {}).get("Device")
            if isinstance(soap_payloads.get("get_attach_device2"), dict)
            else None
        )
        satellites = self._as_list(
            ((soap_payloads.get("get_all_satellites") or {}).get("CurrentSatellites") or {}).get("satellite")
            if isinstance(soap_payloads.get("get_all_satellites"), dict)
            else None
        )

        sources = {"soap": soap_payloads}
        if soap_errors:
            sources["soap_errors"] = soap_errors

        router_info = soap_payloads.get("get_info") if isinstance(soap_payloads.get("get_info"), dict) else {}
        return current, router_info, support_features, sources, devices, satellites

    def fetch_snapshot(
        self,
        *,
        target_satellite_name: str = "",
        expected_connection: str = "Wired",
    ) -> RouterSnapshot:
        opener, token = self._build_ajax_opener()
        basic_payload = self._post_json(opener, token, "/ajax/basicStatus.cgi")
        attached_payload = self._post_json(opener, token, "/ajax/get_attached_devices")

        current_setting: dict[str, str] = {}
        router_info: dict[str, object] = {}
        support_features: dict[str, object] = {}
        sources: dict[str, object] = {
            "ajax": {
                "basic_status": basic_payload,
                "attached_devices": attached_payload,
            }
        }
        soap_devices: list[dict[str, object]] = []
        soap_satellites: list[dict[str, object]] = []
        try:
            (
                current_setting,
                router_info,
                support_features,
                soap_sources,
                soap_devices,
                soap_satellites,
            ) = self._fetch_soap_state()
            sources["ajax"]["current_setting"] = current_setting
            if isinstance(soap_sources.get("soap"), dict):
                sources["soap"] = soap_sources["soap"]
            if isinstance(soap_sources.get("soap_errors"), dict):
                sources["soap_errors"] = soap_sources["soap_errors"]
        except Exception as error:
            sources["soap_errors"] = {"session": str(error)}

        return RouterSnapshot.from_payloads(
            basic_payload,
            attached_payload,
            target_satellite_name=target_satellite_name,
            expected_connection=expected_connection,
            current_setting=current_setting,
            router_info=router_info,
            support_features=support_features,
            sources=sources,
            soap_devices=soap_devices,
            soap_satellites=soap_satellites,
        )
