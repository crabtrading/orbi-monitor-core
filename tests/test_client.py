from __future__ import annotations

import json

from orbi_monitor_core.client import OrbiClient
from orbi_monitor_core.models import RouterSnapshot


SOAP_ATTACH_DEVICE2 = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <m:GetAttachDevice2Response xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1">
      <NewAttachDevice>
        <Device>
          <IP>192.168.50.10</IP>
          <Name>media-speaker</Name>
          <MAC>AA:AA:AA:AA:AA:10</MAC>
          <ConnectionType>5GHz</ConnectionType>
          <SSID>HOME_WIFI_5G</SSID>
          <Linkspeed>1201</Linkspeed>
          <SignalStrength>62</SignalStrength>
          <ConnAPMAC>BB:BB:BB:BB:BB:01</ConnAPMAC>
        </Device>
      </NewAttachDevice>
    </m:GetAttachDevice2Response>
    <ResponseCode>000</ResponseCode>
  </soap-env:Body>
</soap-env:Envelope>
"""

SOAP_ALL_SATELLITES = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <m:GetAllSatellitesResponse xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1">
      <CurrentSatellites>
        <satellite>
          <IP>192.168.50.2</IP>
          <MAC>BB:BB:BB:BB:BB:01</MAC>
          <DeviceName>Satellite A</DeviceName>
          <ModelName>RBS750</ModelName>
          <SignalStrength>36</SignalStrength>
          <Hop>0</Hop>
          <ParentMac>CC:CC:CC:CC:CC:01</ParentMac>
          <BHConnType>5GHz</BHConnType>
        </satellite>
      </CurrentSatellites>
    </m:GetAllSatellitesResponse>
    <ResponseCode>000</ResponseCode>
  </soap-env:Body>
</soap-env:Envelope>
"""

SOAP_GET_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <m:GetInfoResponse xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1">
      <ModelName>RBR750</ModelName>
      <DeviceName>RBR750</DeviceName>
      <Firmwareversion>V7.2.8.2</Firmwareversion>
      <DeviceNameUserSet>false</DeviceNameUserSet>
    </m:GetInfoResponse>
    <ResponseCode>000</ResponseCode>
  </soap-env:Body>
</soap-env:Envelope>
"""

SOAP_SUPPORT_FEATURES = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <m:GetSupportFeatureListXMLResponse xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1">
      <newFeatureList>
        <features>
          <AttachedDevice>3.0</AttachedDevice>
          <SatelliteInfo>2.0</SatelliteInfo>
          <DeviceTypeIcon>2.5</DeviceTypeIcon>
        </features>
      </newFeatureList>
    </m:GetSupportFeatureListXMLResponse>
    <ResponseCode>000</ResponseCode>
  </soap-env:Body>
</soap-env:Envelope>
"""

AJAX_BASIC = {
    "internet": 0,
    "internet_head": "STATUS",
    "internet_text": "GOOD",
}

AJAX_ATTACHED = {
    "devices": [
        {
            "ConnectedOrbi": "Satellite A",
            "ConnectedOrbiMAC": "CC:CC:CC:CC:CC:10",
            "connectionType": "5 GHz",
            "ip": "192.168.50.101",
            "mac": "AA:AA:AA:AA:AA:01",
            "model": "iPhone 16",
            "name": "Mobile Client",
            "status": "1",
            "type": "phone",
            "typeName": "Smart Phone",
        }
    ],
    "satellites": [
        {
            "ConnectedOrbi": "Orbi Router",
            "ConnectedOrbiMAC": "CC:CC:CC:CC:CC:01",
            "backhaulStatus": "Good",
            "connectionType": "5 GHz",
            "ip": "192.168.50.2",
            "mac": "BB:BB:BB:BB:BB:01",
            "model": "RBS750",
            "name": "Satellite A",
            "status": "1",
        }
    ],
}


def test_find_collection_parses_attach_device2() -> None:
    client = OrbiClient("http://192.168.1.1", "admin", "secret")
    items = client._find_collection(
        SOAP_ATTACH_DEVICE2,
        container_tag="NewAttachDevice",
        item_tag="Device",
    )
    assert items[0]["SSID"] == "HOME_WIFI_5G"
    assert items[0]["SignalStrength"] == "62"


def test_find_collection_parses_satellites() -> None:
    client = OrbiClient("http://192.168.1.1", "admin", "secret")
    items = client._find_collection(
        SOAP_ALL_SATELLITES,
        container_tag="CurrentSatellites",
        item_tag="satellite",
    )
    assert items[0]["DeviceName"] == "Satellite A"
    assert items[0]["BHConnType"] == "5GHz"


def test_find_response_payload_parses_structured_response() -> None:
    client = OrbiClient("http://192.168.1.1", "admin", "secret")
    info_payload = client._find_response_payload(SOAP_GET_INFO)
    features_payload = client._find_response_payload(SOAP_SUPPORT_FEATURES)

    assert info_payload["ModelName"] == "RBR750"
    assert features_payload["newFeatureList"]["features"]["AttachedDevice"] == "3.0"


def test_snapshot_merges_ajax_and_soap_fields() -> None:
    snapshot = RouterSnapshot.from_payloads(
        AJAX_BASIC,
        AJAX_ATTACHED,
        target_satellite_name="Satellite A",
        expected_connection="Wired",
        soap_devices=[
            {
                "MAC": "AA:AA:AA:AA:AA:01",
                "Name": "Mobile Client",
                "NameUserSet": "true",
                "IP": "192.168.50.101",
                "ConnectionType": "5GHz",
                "SSID": "HOME_WIFI_5G",
                "Linkspeed": "1201",
                "SignalStrength": "62",
                "ConnAPMAC": "BB:BB:BB:BB:BB:01",
                "AllowOrBlock": "Allow",
                "Schedule": "false",
                "DeviceType": "24",
                "DeviceTypeUserSet": "false",
                "DeviceTypeV2": "MOBILE",
                "DeviceTypeNameV2": "Mobile",
                "Upload": "0",
                "Download": "0",
                "QosPriority": "4",
                "DeviceModelUserSet": "false",
                "DeviceBrand": "Apple",
                "DeviceModel": "iPhone",
                "Grouping": "0",
                "SchedulePeriod": "0",
            }
        ],
        soap_satellites=[
            {
                "DeviceName": "Satellite A",
                "DeviceNameUserSet": "true",
                "IP": "192.168.50.2",
                "MAC": "BB:BB:BB:BB:BB:01",
                "ModelName": "RBS750",
                "SerialNumber": "ABC123",
                "SignalStrength": "36",
                "FWVersion": "V7.2.8.2",
                "Hop": "0",
                "ParentMac": "CC:CC:CC:CC:CC:01",
                "BHConnType": "5GHz",
                "BHConnStatus": "2",
                "IsLightingLEDSupported": "0",
                "LightingLEDOnOffStatus": "0",
                "LightingLEDBrightnessStatus": "0",
                "AvsSupport": "na",
            }
        ],
        current_setting={"SOAP_HTTPs_Port": "443", "LoginMethod": "2.0"},
        router_info={"ModelName": "RBR750", "Firmwareversion": "V7.2.8.2"},
        support_features={"AttachedDevice": "3.0", "SatelliteInfo": "2.0"},
        sources={"soap": {"get_info": {"ModelName": "RBR750"}}},
    )
    payload = snapshot.to_dict()
    assert payload["devices"][0]["ssid"] == "HOME_WIFI_5G"
    assert payload["devices"][0]["linkspeed_mbps"] == 1201
    assert payload["devices"][0]["signal_strength"] == 62
    assert payload["devices"][0]["name_user_set"] is True
    assert payload["devices"][0]["device_type_v2"] == "MOBILE"
    assert payload["satellites"][0]["signal_strength"] == 36
    assert payload["satellites"][0]["serial_number"] == "ABC123"
    assert payload["satellites"][0]["backhaul_conn_status"] == 2
    assert payload["current_setting"]["SOAP_HTTPs_Port"] == "443"
    assert payload["router_info"]["ModelName"] == "RBR750"
    assert payload["support_features"]["AttachedDevice"] == "3.0"
    assert payload["sources"]["soap"]["get_info"]["ModelName"] == "RBR750"
