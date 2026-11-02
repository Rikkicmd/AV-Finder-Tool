"""Simulated Avigilon ONVIF devices for testing the dashboard without hardware.

Runs several virtual devices, each on its own HTTP port, and answers
WS-Discovery probes (multicast and unicast on UDP 3702) for all of them.

    python -m simulator.fake_devices            # 4 devices on ports 8081-8084

Devices:
  1. H5A bullet, factory fresh (no ONVIF users, unauthenticated access)
  2. H5SL dome, factory fresh with default admin/admin
  3. H4 fisheye, already configured (admin / Secure123!)
  4. NVR5 recorder, already configured (admin / Secure123!)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import socket
import struct
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from lxml import etree

S = "http://www.w3.org/2003/05/soap-envelope"


def q(el, name):
    r = el.xpath(f".//*[local-name()='{name}']")
    return r[0] if r else None


def t(el, name, default=None):
    r = q(el, name) if el is not None else None
    return r.text.strip() if r is not None and r.text else default


def soap(body: str) -> Response:
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{S}" '
           f'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
           f'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{body}</s:Body></s:Envelope>')
    return Response(xml, media_type="application/soap+xml")


def fault(reason: str, sub="ter:InvalidArgVal", status=400) -> Response:
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{S}" xmlns:ter="http://www.onvif.org/ver10/error">'
           f'<s:Body><s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>{sub}</s:Value></s:Subcode>'
           f'</s:Code><s:Reason><s:Text xml:lang="en">{reason}</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>')
    return Response(xml, status_code=status, media_type="application/soap+xml")


class FakeDevice:
    def __init__(self, port, ip, model, serial, kind="camera", users=None, name=None):
        self.port, self.ip, self.model, self.serial, self.kind = port, ip, model, serial, kind
        self.urn = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_DNS, serial)}"
        self.users = users if users is not None else {}  # name -> (password, level)
        self.hostname = f"{model.split()[0]}-{serial[-4:]}"
        self.name = name or f"Avigilon {model}"
        self.location = ""
        self.dhcp = True
        self.address, self.prefix = ip, 16 if ip.startswith("169.254") else 24
        self.gateway = "0.0.0.0"
        self.dns = []
        self.ntp_dhcp, self.ntp = False, []
        self.dt_type, self.tz = "Manual", "GMT0"
        self.mac = "00:18:85:" + ":".join(serial[-6:][i:i + 2] for i in range(0, 6, 2)).lower()
        self.encoders = {
            "VEC_1": dict(name="Primary", enc="H264", w=2592, h=1944, fps=30, br=8000, gov=30, q=70),
            "VEC_2": dict(name="Secondary", enc="H264", w=640, h=480, fps=15, br=1024, gov=15, q=50),
        }
        self.app = self._make_app()

    @property
    def xaddr(self):
        return f"http://{self.ip}:{self.port}/onvif/device_service"

    def scopes(self):
        typ = "NetworkVideoStorage" if self.kind == "recorder" else "video_encoder"
        base = [f"onvif://www.onvif.org/type/{typ}", "onvif://www.onvif.org/Profile/Streaming",
                f"onvif://www.onvif.org/hardware/{self.model.replace(' ', '-')}",
                f"onvif://www.onvif.org/name/{self.name.replace(' ', '%20')}"]
        if self.location:
            base.append(f"onvif://www.onvif.org/location/{self.location.replace(' ', '%20')}")
        return base

    # -------------------------------------------------------------- auth
    def authorized(self, root) -> bool:
        if not self.users:
            return True  # ONVIF factory default state: no users -> no auth
        tok = q(root, "UsernameToken")
        if tok is None:
            return False
        user, pw = t(tok, "Username"), t(tok, "Password")
        nonce, created = t(tok, "Nonce"), t(tok, "Created")
        if user not in self.users or not all((pw, nonce, created)):
            return False
        expect = base64.b64encode(hashlib.sha1(
            base64.b64decode(nonce) + created.encode() + self.users[user][0].encode()).digest()).decode()
        return expect == pw

    # -------------------------------------------------------------- app
    def _make_app(self):
        app = FastAPI()
        dev = self

        @app.get("/snapshot/{token}")
        def snap(token: str):
            e = dev.encoders.get(token, dev.encoders["VEC_1"])
            now = dt.datetime.now().strftime("%H:%M:%S")
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1b2a3a"/><stop offset="1" stop-color="#0b1118"/></linearGradient></defs>
<rect width="640" height="360" fill="url(#g)"/>
<g stroke="#2c4157" stroke-width="1">{''.join(f'<line x1="{i*40}" y1="0" x2="{i*40}" y2="360"/>' for i in range(17))}{''.join(f'<line x1="0" y1="{i*40}" x2="640" y2="{i*40}"/>' for i in range(10))}</g>
<circle cx="{(int(now[-2:]) * 10) % 640}" cy="220" r="26" fill="#e53935" opacity=".85"/>
<text x="20" y="34" fill="#fff" font-family="monospace" font-size="18">{dev.name}</text>
<text x="20" y="58" fill="#8fb3d9" font-family="monospace" font-size="14">{dev.model} · {e['w']}x{e['h']} @ {e['fps']}fps · {e['enc']}</text>
<text x="620" y="340" fill="#fff" font-family="monospace" font-size="16" text-anchor="end">{now}</text></svg>"""
            return Response(svg, media_type="image/svg+xml")

        @app.post("/onvif/{svc}")
        async def handle(svc: str, request: Request):
            root = etree.fromstring(await request.body())
            body = root.xpath("//*[local-name()='Body']")[0]
            op = etree.QName(body[0]).localname
            public = {"GetSystemDateAndTime"}
            if op not in public and not dev.authorized(root):
                return fault("Sender not Authorized", "ter:NotAuthorized", 400)
            fn = getattr(dev, f"op_{op}", None)
            if not fn:
                return fault(f"Action {op} not supported", "ter:ActionNotSupported")
            r = fn(body[0])
            return r if isinstance(r, Response) else soap(r)

        return app

    # -------------------------------------------------------------- device ops
    def op_GetSystemDateAndTime(self, _):
        u = dt.datetime.now(dt.timezone.utc)
        return (f"<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:DateTimeType>{self.dt_type}</tt:DateTimeType>"
                f"<tt:DaylightSavings>false</tt:DaylightSavings><tt:TimeZone><tt:TZ>{self.tz}</tt:TZ></tt:TimeZone>"
                f"<tt:UTCDateTime><tt:Time><tt:Hour>{u.hour}</tt:Hour><tt:Minute>{u.minute}</tt:Minute><tt:Second>{u.second}</tt:Second></tt:Time>"
                f"<tt:Date><tt:Year>{u.year}</tt:Year><tt:Month>{u.month}</tt:Month><tt:Day>{u.day}</tt:Day></tt:Date></tt:UTCDateTime>"
                f"</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>")

    def op_SetSystemDateAndTime(self, b):
        self.dt_type = t(b, "DateTimeType", "Manual")
        self.tz = t(b, "TZ", self.tz)
        return "<tds:SetSystemDateAndTimeResponse/>"

    def op_GetDeviceInformation(self, _):
        return (f"<tds:GetDeviceInformationResponse><tds:Manufacturer>Avigilon</tds:Manufacturer>"
                f"<tds:Model>{self.model}</tds:Model><tds:FirmwareVersion>4.38.0.12</tds:FirmwareVersion>"
                f"<tds:SerialNumber>{self.serial}</tds:SerialNumber><tds:HardwareId>{self.model}</tds:HardwareId>"
                f"</tds:GetDeviceInformationResponse>")

    def op_GetServices(self, _):
        base = f"http://{self.ip}:{self.port}/onvif"
        return (f"<tds:GetServicesResponse><tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
                f"<tds:XAddr>{base}/device_service</tds:XAddr></tds:Service><tds:Service>"
                f"<tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace><tds:XAddr>{base}/media_service</tds:XAddr>"
                f"</tds:Service></tds:GetServicesResponse>")

    def op_GetScopes(self, _):
        items = "".join(f"<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>{s}</tt:ScopeItem></tds:Scopes>"
                        for s in self.scopes())
        return f"<tds:GetScopesResponse>{items}</tds:GetScopesResponse>"

    def op_SetScopes(self, b):
        from urllib.parse import unquote
        for s in b.xpath(".//*[local-name()='Scopes']/text()"):
            if "/name/" in s:
                self.name = unquote(s.split("/name/", 1)[1])
            if "/location/" in s:
                self.location = unquote(s.split("/location/", 1)[1])
        return "<tds:SetScopesResponse/>"

    def op_GetHostname(self, _):
        return (f"<tds:GetHostnameResponse><tds:HostnameInformation><tt:FromDHCP>false</tt:FromDHCP>"
                f"<tt:Name>{self.hostname}</tt:Name></tds:HostnameInformation></tds:GetHostnameResponse>")

    def op_SetHostname(self, b):
        self.hostname = t(b, "Name")
        return "<tds:SetHostnameResponse/>"

    def op_GetUsers(self, _):
        u = "".join(f"<tds:User><tt:Username>{n}</tt:Username><tt:UserLevel>{lv}</tt:UserLevel></tds:User>"
                    for n, (_, lv) in self.users.items())
        return f"<tds:GetUsersResponse>{u}</tds:GetUsersResponse>"

    def op_CreateUsers(self, b):
        for u in b.xpath(".//*[local-name()='User']"):
            name = t(u, "Username")
            if name in self.users:
                return fault("Username already exists", "ter:UsernameClash")
            self.users[name] = (t(u, "Password", ""), t(u, "UserLevel", "User"))
        return "<tds:CreateUsersResponse/>"

    def op_SetUser(self, b):
        for u in b.xpath(".//*[local-name()='User']"):
            name = t(u, "Username")
            if name not in self.users:
                return fault("Username not recognized", "ter:UsernameMissing")
            self.users[name] = (t(u, "Password", ""), t(u, "UserLevel", "User"))
        return "<tds:SetUserResponse/>"

    def op_DeleteUsers(self, b):
        for n in b.xpath(".//*[local-name()='Username']/text()"):
            self.users.pop(n, None)
        return "<tds:DeleteUsersResponse/>"

    def op_GetNetworkInterfaces(self, _):
        cfg = (f"<tt:Manual><tt:Address>{self.address}</tt:Address><tt:PrefixLength>{self.prefix}</tt:PrefixLength></tt:Manual>"
               if not self.dhcp else
               f"<tt:FromDHCP><tt:Address>{self.address}</tt:Address><tt:PrefixLength>{self.prefix}</tt:PrefixLength></tt:FromDHCP>")
        ll = f"<tt:LinkLocal><tt:Address>{self.ip}</tt:Address><tt:PrefixLength>16</tt:PrefixLength></tt:LinkLocal>" \
            if self.ip.startswith("169.254") else ""
        return (f'<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token="eth0"><tt:Enabled>true</tt:Enabled>'
                f"<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>{self.mac}</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>"
                f"<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config>{cfg}{ll}<tt:DHCP>{str(self.dhcp).lower()}</tt:DHCP>"
                f"</tt:Config></tt:IPv4></tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>")

    def op_SetNetworkInterfaces(self, b):
        self.dhcp = t(b, "DHCP") == "true"
        if not self.dhcp:
            self.address = t(q(b, "Manual"), "Address")
            self.prefix = int(t(q(b, "Manual"), "PrefixLength", "24"))
            # Simulator keeps listening on its real address so testing can continue.
        return "<tds:SetNetworkInterfacesResponse><tds:RebootNeeded>false</tds:RebootNeeded></tds:SetNetworkInterfacesResponse>"

    def op_GetNetworkDefaultGateway(self, _):
        return (f"<tds:GetNetworkDefaultGatewayResponse><tds:NetworkGateway><tt:IPv4Address>{self.gateway}</tt:IPv4Address>"
                f"</tds:NetworkGateway></tds:GetNetworkDefaultGatewayResponse>")

    def op_SetNetworkDefaultGateway(self, b):
        self.gateway = t(b, "IPv4Address", self.gateway)
        return "<tds:SetNetworkDefaultGatewayResponse/>"

    def op_GetDNS(self, _):
        m = "".join(f"<tt:DNSManual><tt:Type>IPv4</tt:Type><tt:IPv4Address>{d}</tt:IPv4Address></tt:DNSManual>" for d in self.dns)
        return (f"<tds:GetDNSResponse><tds:DNSInformation><tt:FromDHCP>{str(not self.dns).lower()}</tt:FromDHCP>{m}"
                f"</tds:DNSInformation></tds:GetDNSResponse>")

    def op_SetDNS(self, b):
        self.dns = [] if t(b, "FromDHCP") == "true" else b.xpath(".//*[local-name()='IPv4Address']/text()")
        return "<tds:SetDNSResponse/>"

    def op_GetNTP(self, _):
        m = "".join(f"<tt:NTPManual><tt:Type>DNS</tt:Type><tt:DNSname>{n}</tt:DNSname></tt:NTPManual>" for n in self.ntp)
        return (f"<tds:GetNTPResponse><tds:NTPInformation><tt:FromDHCP>{str(self.ntp_dhcp).lower()}</tt:FromDHCP>{m}"
                f"</tds:NTPInformation></tds:GetNTPResponse>")

    def op_SetNTP(self, b):
        self.ntp_dhcp = t(b, "FromDHCP") == "true"
        self.ntp = [e.text for e in b.xpath(".//*[local-name()='DNSname' or local-name()='IPv4Address']")]
        return "<tds:SetNTPResponse/>"

    def op_SystemReboot(self, _):
        return "<tds:SystemRebootResponse><tds:Message>Rebooting in 30 seconds</tds:Message></tds:SystemRebootResponse>"

    def op_SetSystemFactoryDefault(self, _):
        self.users = {}
        return "<tds:SetSystemFactoryDefaultResponse/>"

    # -------------------------------------------------------------- media ops
    def _vec(self, tok, tag="tt:VideoEncoderConfiguration"):
        e = self.encoders[tok]
        return (f'<{tag} token="{tok}"><tt:Name>{e["name"]}</tt:Name><tt:UseCount>1</tt:UseCount>'
                f'<tt:Encoding>{e["enc"]}</tt:Encoding><tt:Resolution><tt:Width>{e["w"]}</tt:Width><tt:Height>{e["h"]}</tt:Height></tt:Resolution>'
                f'<tt:Quality>{e["q"]}</tt:Quality><tt:RateControl><tt:FrameRateLimit>{e["fps"]}</tt:FrameRateLimit>'
                f'<tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>{e["br"]}</tt:BitrateLimit></tt:RateControl>'
                f'<tt:H264><tt:GovLength>{e["gov"]}</tt:GovLength><tt:H264Profile>High</tt:H264Profile></tt:H264>'
                f'<tt:SessionTimeout>PT60S</tt:SessionTimeout></{tag}>')

    def op_GetProfiles(self, _):
        if self.kind == "recorder":
            return "<trt:GetProfilesResponse/>"
        p = "".join(f'<trt:Profiles token="Profile_{i}" fixed="true"><tt:Name>{n}</tt:Name>{self._vec(v)}</trt:Profiles>'
                    for i, (n, v) in enumerate((("High resolution", "VEC_1"), ("Low resolution", "VEC_2")), 1))
        return f"<trt:GetProfilesResponse>{p}</trt:GetProfilesResponse>"

    def op_GetVideoEncoderConfiguration(self, b):
        tok = t(b, "ConfigurationToken")
        if tok not in self.encoders:
            return fault("No such configuration", "ter:NoConfig")
        return f"<trt:GetVideoEncoderConfigurationResponse>{self._vec(tok, 'trt:Configuration')}</trt:GetVideoEncoderConfigurationResponse>"

    def op_GetVideoEncoderConfigurationOptions(self, _):
        res = "".join(f"<tt:ResolutionsAvailable><tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:ResolutionsAvailable>"
                      for w, h in ((2592, 1944), (2560, 1440), (1920, 1080), (1280, 720), (640, 480), (320, 240)))
        return (f"<trt:GetVideoEncoderConfigurationOptionsResponse><trt:Options><tt:QualityRange><tt:Min>1</tt:Min><tt:Max>100</tt:Max></tt:QualityRange>"
                f"<tt:JPEG>{res}<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>15</tt:Max></tt:FrameRateRange></tt:JPEG>"
                f"<tt:H264>{res}<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>255</tt:Max></tt:GovLengthRange>"
                f"<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>30</tt:Max></tt:FrameRateRange><tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
                f"<tt:H264ProfilesSupported>Main</tt:H264ProfilesSupported><tt:H264ProfilesSupported>High</tt:H264ProfilesSupported></tt:H264>"
                f"</trt:Options></trt:GetVideoEncoderConfigurationOptionsResponse>")

    def op_SetVideoEncoderConfiguration(self, b):
        tok = q(b, "Configuration").get("token")
        if tok not in self.encoders:
            return fault("No such configuration", "ter:NoConfig")
        e = self.encoders[tok]
        e.update(enc=t(b, "Encoding", e["enc"]), w=int(t(b, "Width")), h=int(t(b, "Height")),
                 fps=int(t(b, "FrameRateLimit")), br=int(t(b, "BitrateLimit")), q=float(t(b, "Quality", e["q"])),
                 gov=int(t(b, "GovLength", e["gov"])))
        return "<trt:SetVideoEncoderConfigurationResponse/>"

    def op_GetStreamUri(self, b):
        tok = t(b, "ProfileToken")
        return (f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://{self.ip}:554/defaultPrimary?streamType=u&amp;p={tok}</tt:Uri>"
                f"<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
                f"<tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>")

    def op_GetSnapshotUri(self, b):
        tok = "VEC_2" if t(b, "ProfileToken") == "Profile_2" else "VEC_1"
        return (f"<trt:GetSnapshotUriResponse><trt:MediaUri><tt:Uri>http://{self.ip}:{self.port}/snapshot/{tok}</tt:Uri>"
                f"</trt:MediaUri></trt:GetSnapshotUriResponse>")


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, devices):
        self.devices = devices

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            return
        if not root.xpath("//*[local-name()='Probe']"):
            return
        rel = (root.xpath("//*[local-name()='MessageID']/text()") or [""])[0]
        for d in self.devices:
            msg = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{S}" '
                   f'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
                   f'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><s:Header>'
                   f'<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>'
                   f'<a:MessageID>uuid:{uuid.uuid4()}</a:MessageID><a:RelatesTo>{rel}</a:RelatesTo></s:Header><s:Body>'
                   f'<d:ProbeMatches><d:ProbeMatch><a:EndpointReference><a:Address>{d.urn}</a:Address></a:EndpointReference>'
                   f'<d:Types>dn:{"NetworkVideoStorage" if d.kind == "recorder" else "NetworkVideoTransmitter"}</d:Types>'
                   f'<d:Scopes>{" ".join(d.scopes())}</d:Scopes><d:XAddrs>{d.xaddr}</d:XAddrs>'
                   f'<d:MetadataVersion>1</d:MetadataVersion></d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>')
            self.transport.sendto(msg.encode(), addr)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="127.0.0.1", help="address the fake devices advertise")
    ap.add_argument("--base-port", type=int, default=8081)
    args = ap.parse_args()
    p = args.base_port
    devices = [
        FakeDevice(p, args.ip, "4.0C-H5A-BO1-IR", "1903456781", name="Avigilon H5A Bullet"),
        FakeDevice(p + 1, args.ip, "3.0C-H5SL-D1", "2001122334", users={"admin": ("admin", "Administrator")},
                   name="Avigilon H5SL Dome"),
        FakeDevice(p + 2, args.ip, "12.0W-H4F-DO1", "1702233445", users={"admin": ("Secure123!", "Administrator")},
                   name="Lobby Fisheye"),
        FakeDevice(p + 3, args.ip, "NVR5-PRM-32TB", "2205566778", kind="recorder",
                   users={"admin": ("Secure123!", "Administrator")}, name="Avigilon NVR5 Premium"),
    ]
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 3702))
    try:
        mreq = struct.pack("4sl", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as e:
        print("multicast join failed (unicast probes still work):", e)
    await loop.create_datagram_endpoint(lambda: Discovery(devices), sock=sock)
    servers = [uvicorn.Server(uvicorn.Config(d.app, host="0.0.0.0", port=d.port, log_level="warning")) for d in devices]
    for d in devices:
        print(f"  {d.name:<24} {d.xaddr}  users={list(d.users) or 'none (factory)'}")
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
