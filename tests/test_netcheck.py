from pathlib import Path
import pytest
from cxpe import netcheck


def test_skipped_connection_probe_is_unknown(monkeypatch):
    monkeypatch.setenv("CXPE_SKIP_CONNECT_PROBE", "1")
    assert netcheck.connect_blocked() is None


def test_interface_read_error_is_not_empty_network(monkeypatch):
    monkeypatch.setattr(netcheck, "_IS_LINUX", True)
    def denied(self): raise PermissionError("denied")
    monkeypatch.setattr(Path, "iterdir", denied)
    assert netcheck.external_ifaces() is None


def test_unknown_interface_and_counters_cannot_claim_offline(monkeypatch):
    monkeypatch.setattr(netcheck, "_IS_LINUX", True)
    for name,value in [("dns_blocked",True),("connect_blocked",True),("default_route_present",False),("external_ifaces",None),("wan_iface",None)]:
        monkeypatch.setattr(netcheck, name, lambda v=value: v)
    result=netcheck.status()
    assert not result["no_external_iface"] and not result["is_offline"]
    assert result["tx_delta_bytes"] is None


@pytest.mark.parametrize("flags,expected", [("00000001", True), ("00200200", False)])
def test_ipv6_default_route_is_checked(monkeypatch, flags, expected):
    monkeypatch.setattr(netcheck, "_IS_LINUX", True)
    def read(self, *args, **kwargs):
        if self.name == "route":return "Iface Destination\n"
        return "0"*32 + " 00 " + "0"*32 + " 00 " + "0"*32 + " 00000001 00000000 00000000 " + flags + " lo\n"
    monkeypatch.setattr(Path, "read_text", read)
    assert netcheck.default_route_present() is expected
