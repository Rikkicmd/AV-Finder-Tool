"""ONVIF WS-Discovery (multicast + optional unicast sweep)."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from urllib.parse import unquote, urlparse

from lxml import etree

from .onvif import x, xe

MCAST = ("239.255.255.250", 3702)

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<s:Header>
<a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
<a:MessageID>uuid:{mid}</a:MessageID>
<a:ReplyTo><a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address></a:ReplyTo>
<a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
</s:Header>
<s:Body><d:Probe>{types}</d:Probe></s:Body>
</s:Envelope>"""


def probe_message(types: str | None = None) -> bytes:
    t = f"<d:Types>{types}</d:Types>" if types else ""
    return PROBE.format(mid=uuid.uuid4(), types=t).encode()


def local_ipv4s() -> list[str]:
    """IPv4 addresses of this computer, so we can probe out of every NIC
    (important when a factory-fresh camera is on a separate link-local segment)."""
    ips: set[str] = set()
    try:
        import psutil  # optional
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    ips.add(a.address)
    except Exception:
        pass
    if not ips:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.add(info[4][0])
        except OSError:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return sorted(i for i in ips if not i.startswith("127."))


def parse_scopes(scopes: list[str]) -> dict:
    out: dict = {"name": None, "hardware": None, "location": None, "scope_types": [], "mac": None, "profiles": []}
    for s in scopes:
        if not s.startswith("onvif://www.onvif.org/"):
            continue
        parts = s[len("onvif://www.onvif.org/"):].split("/", 1)
        if len(parts) != 2:
            continue
        key, val = parts[0].lower(), unquote(parts[1])
        if key == "name":
            out["name"] = val
        elif key == "hardware":
            out["hardware"] = val
        elif key == "location":
            out["location"] = (out["location"] + " / " if out["location"] else "") + val
        elif key == "type":
            out["scope_types"].append(val)
        elif key == "mac":
            out["mac"] = val
        elif key == "profile":
            out["profiles"].append(val)
    return out


def parse_probe_match(data: bytes, src_ip: str) -> list[dict]:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return []
    found = []
    for m in root.xpath("//*[local-name()='ProbeMatch']"):
        xaddrs = (x(m, "XAddrs") or "").split()
        scopes = (x(m, "Scopes") or "").split()
        # Prefer the XAddr that matches the address the reply came from, then IPv4.
        xaddrs.sort(key=lambda u: (urlparse(u).hostname != src_ip, ":" in (urlparse(u).hostname or "")))
        found.append({
            "urn": x(m, "EndpointReference/Address") or f"ip:{src_ip}",
            "types": (x(m, "Types") or "").split(),
            "scopes": scopes,
            "xaddrs": xaddrs,
            "source_ip": src_ip,
            **parse_scopes(scopes),
        })
    return found


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.results: dict[str, dict] = {}

    def datagram_received(self, data, addr):
        for d in parse_probe_match(data, addr[0]):
            self.results.setdefault(d["urn"], d)


async def discover(timeout: float = 3.0, targets: list[str] | None = None, port: int = 3702) -> list[dict]:
    """Multicast probe out of every local NIC, plus unicast probes to `targets`
    (IPs or CIDRs, e.g. '192.168.0.0/24'). Returns unique devices."""
    loop = asyncio.get_running_loop()
    collector = _Collector()
    transports = []
    msg_all = probe_message("dn:NetworkVideoTransmitter")
    msg_any = probe_message()

    def make_sock(bind_ip: str):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
        except OSError:
            pass
        s.bind((bind_ip, 0))
        s.setblocking(False)
        return s

    for ip in local_ipv4s() or ["0.0.0.0"]:
        try:
            sock = make_sock(ip)
        except OSError:
            continue
        t, _ = await loop.create_datagram_endpoint(lambda: collector, sock=sock)
        transports.append(t)
        for m in (msg_all, msg_any):
            try:
                t.sendto(m, (MCAST[0], port))
            except OSError:
                pass

    # Unicast sweep
    if targets:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        sock.setblocking(False)
        t, _ = await loop.create_datagram_endpoint(lambda: collector, sock=sock)
        transports.append(t)
        hosts: list[str] = []
        for tgt in targets:
            tgt = tgt.strip()
            if not tgt:
                continue
            try:
                net = ipaddress.ip_network(tgt, strict=False)
            except ValueError:
                continue
            if net.num_addresses > 4096:
                continue  # refuse huge sweeps; use multicast instead
            hosts += [str(h) for h in (net.hosts() if net.num_addresses > 1 else [net.network_address])]
        for i, h in enumerate(hosts):
            try:
                t.sendto(msg_any, (h, port))
            except OSError:
                pass
            if i % 64 == 63:
                await asyncio.sleep(0.01)

    await asyncio.sleep(timeout)
    for t in transports:
        t.close()
    return list(collector.results.values())
