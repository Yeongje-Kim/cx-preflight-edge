"""폐쇄망 판정과 보드 자원. 외부로 데이터를 보내지 않는다.

is_offline 조건 4가지를 모두 만족할 때만 OFF 배지:
  dns_blocked      : 공개 호스트 이름 해석 실패
  connect_blocked  : 1.1.1.1:443 TCP 연결 실패 (SYN 1개가 나갈 수 있으나 데이터는 없다)
  no_default_route : 기본 경로 없음 (Linux /proc/net/route; 그 외 OS는 None=미확인)
  tx_delta_zero    : 세션 중 WAN 인터페이스 송신 바이트 증가 0 (Linux sysfs; 인터페이스는 CXPE_WAN_IF)

루프백 외 인터페이스가 하나도 없으면(no_external_iface) 송신 통로 자체가 없으므로 tx_delta_zero 를
0으로 본다. 네트워크 네임스페이스 안에서 실행할 때가 이 경우이며, 폐쇄망 증거로는 가장 강하다.
"""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

_IS_LINUX = platform.system() == "Linux"


def dns_blocked(host: str = "example.com", timeout: float = 1.0) -> bool:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(host, 443)
        return False
    except Exception:
        return True
    finally:
        socket.setdefaulttimeout(old)


def connect_blocked(addr: tuple[str, int] = ("1.1.1.1", 443), timeout: float = 1.0) -> Optional[bool]:
    if os.environ.get("CXPE_SKIP_CONNECT_PROBE") == "1":
        return None  # A skipped probe is unknown, not evidence of isolation.
    try:
        with socket.create_connection(addr, timeout=timeout):
            return False
    except Exception:
        return True


def default_route_present() -> Optional[bool]:
    if _IS_LINUX:
        try:
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return True
            # IPv6 defaults count too; ignore the kernel's unreachable placeholder.
            for line in Path("/proc/net/ipv6_route").read_text().splitlines():
                parts = line.split()
                if (len(parts) >= 10 and parts[0] == "0" * 32 and parts[1] == "00"
                        and int(parts[8], 16) & 1 and not int(parts[8], 16) & 0x200):
                    return True
            return False
        except Exception:
            return None
    if platform.system() == "Windows":
        try:
            out = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True, text=True, timeout=5).stdout
            return any(l.strip().startswith("0.0.0.0") for l in out.splitlines())
        except Exception:
            return None
    return None


def wan_iface() -> Optional[str]:
    env = os.environ.get("CXPE_WAN_IF")
    if env:
        return env
    if _IS_LINUX:
        try:
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
        except Exception:
            return None
        # 기본 경로가 없으면 wlan/eth 후보 중 존재하는 첫 번째
        for cand in ("wlan0", "eth0", "end0", "enp1s0"):
            if Path(f"/sys/class/net/{cand}").exists():
                return cand
    return None


def external_ifaces() -> Optional[list[str]]:
    """루프백을 뺀 네트워크 인터페이스 목록. 비어 있으면 외부로 나갈 통로 자체가 없다."""
    if not _IS_LINUX:
        return None
    try:
        return sorted(p.name for p in Path("/sys/class/net").iterdir() if p.name != "lo")
    except Exception:
        return None


def tx_bytes(iface: Optional[str]) -> Optional[int]:
    if not iface or not _IS_LINUX:
        return None
    try:
        return int(Path(f"/sys/class/net/{iface}/statistics/tx_bytes").read_text().strip())
    except Exception:
        return None


def cpu_mem() -> dict:
    out: dict = {"cpu_load1": None, "mem_used_mb": None, "mem_total_mb": None, "proc_rss_mb": None}
    if _IS_LINUX:
        try:
            out["cpu_load1"] = float(Path("/proc/loadavg").read_text().split()[0])
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])
            out["mem_total_mb"] = round(info["MemTotal"] / 1024)
            out["mem_used_mb"] = round((info["MemTotal"] - info["MemAvailable"]) / 1024)
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    out["proc_rss_mb"] = round(int(line.split()[1]) / 1024)
        except Exception:
            pass
    else:
        try:
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS(); ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))  # type: ignore[attr-defined]
            out["mem_total_mb"] = round(ms.ullTotalPhys / 2**20)
            out["mem_used_mb"] = round((ms.ullTotalPhys - ms.ullAvailPhys) / 2**20)
        except Exception:
            pass
    return out


class UplinkMeter:
    """세션 동안 WAN 송신 바이트 증가량."""

    def __init__(self) -> None:
        self.iface = wan_iface()
        self.t0 = time.time()
        self.tx0 = tx_bytes(self.iface)

    def delta(self) -> Optional[int]:
        now = tx_bytes(self.iface)
        if now is None or self.tx0 is None:
            return None
        return max(0, now - self.tx0)


def status(meter: Optional[UplinkMeter] = None) -> dict:
    d = {
        "dns_blocked": dns_blocked(),
        "connect_blocked": connect_blocked(),
        "default_route": default_route_present(),
        "wan_iface": meter.iface if meter else wan_iface(),
        "tx_delta_bytes": meter.delta() if meter else None,
        "platform": platform.system(),
    }
    # 루프백 말고 인터페이스가 하나도 없으면 송신할 통로 자체가 없다. 가장 강한 형태의 폐쇄망이다.
    d["external_ifaces"] = external_ifaces()
    d["no_external_iface"] = _IS_LINUX and d["external_ifaces"] == []
    if d["no_external_iface"]:
        d["wan_iface"] = None
        d["tx_delta_bytes"] = 0
    checks = [d["dns_blocked"], d["connect_blocked"], d["default_route"] is False,
              d["tx_delta_bytes"] == 0]
    d["is_offline"] = all(checks)
    d["offline_score"] = sum(1 for c in checks if c)
    d.update(cpu_mem())
    return d
