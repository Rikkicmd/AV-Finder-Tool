"""Minimal async ONVIF client.

Talks raw SOAP 1.2 to ONVIF devices so we don't depend on WSDL downloads.
Supports WS-Security UsernameToken (PasswordDigest) and falls back to HTTP
Digest auth, which some firmware (including some Avigilon models) wants.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

import httpx
from lxml import etree

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
}
PW_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
NONCE_B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"


class OnvifError(Exception):
    def __init__(self, message: str, *, auth: bool = False, status: int | None = None):
        super().__init__(message)
        self.auth = auth
        self.status = status


def x(el, path: str, default: str | None = None) -> str | None:
    """Namespace-agnostic text lookup: x(el, 'Foo/Bar')."""
    if el is None:
        return default
    q = "./" + "/".join(f"*[local-name()='{p}']" for p in path.split("/"))
    found = el.xpath(q)
    if not found:
        return default
    t = found[0].text
    return t.strip() if t else default


def xs(el, path: str) -> list:
    if el is None:
        return []
    q = "./" + "/".join(f"*[local-name()='{p}']" for p in path.split("/"))
    return el.xpath(q)


def xe(el, path: str):
    r = xs(el, path)
    return r[0] if r else None


def wsse_header(username: str, password: str, offset: dt.timedelta) -> str:
    nonce = os.urandom(16)
    created = (dt.datetime.now(dt.timezone.utc) + offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    return (
        f'<s:Header><wsse:Security s:mustUnderstand="1" xmlns:wsse="{NS["wsse"]}" xmlns:wsu="{NS["wsu"]}">'
        f"<wsse:UsernameToken><wsse:Username>{escape(username)}</wsse:Username>"
        f'<wsse:Password Type="{PW_DIGEST}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{NONCE_B64}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>"
    )


def envelope(body: str, header: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" xmlns:tt="{NS["tt"]}">'
        f"{header}<s:Body>{body}</s:Body></s:Envelope>"
    )


def b(v: bool) -> str:
    return "true" if v else "false"


@dataclass
class OnvifDevice:
    xaddr: str  # device service URL, e.g. http://169.254.1.20/onvif/device_service
    username: str | None = None
    password: str | None = None
    timeout: float = 6.0
    time_offset: dt.timedelta = field(default_factory=dt.timedelta)
    media_xaddr: str | None = None
    _use_http_digest: bool = False

    # ------------------------------------------------------------------ transport
    async def call(self, body: str, url: str | None = None, auth: bool = True):
        url = url or self.xaddr
        header = ""
        if auth and self.username:
            header = wsse_header(self.username, self.password or "", self.time_offset)
        data = envelope(body, header).encode()
        headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
        http_auth = None
        if auth and self.username and self._use_http_digest:
            http_auth = httpx.DigestAuth(self.username, self.password or "")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False, trust_env=False) as c:
                r = await c.post(url, content=data, headers=headers, auth=http_auth)
                if r.status_code == 401 and auth and self.username and not self._use_http_digest:
                    self._use_http_digest = True
                    data = envelope(body, wsse_header(self.username, self.password or "", self.time_offset)).encode()
                    r = await c.post(url, content=data, headers=headers,
                                     auth=httpx.DigestAuth(self.username, self.password or ""))
        except httpx.HTTPError as e:
            raise OnvifError(f"Cannot reach device: {e.__class__.__name__}") from e
        return self._parse(r)

    @staticmethod
    def _parse(r: httpx.Response):
        try:
            root = etree.fromstring(r.content)
        except etree.XMLSyntaxError:
            if r.status_code == 401:
                raise OnvifError("Not authorized", auth=True, status=401)
            raise OnvifError(f"Bad response (HTTP {r.status_code})", status=r.status_code)
        body = xe(root, "Body")
        fault = xe(body, "Fault")
        if fault is not None:
            reason = x(fault, "Reason/Text") or x(fault, "faultstring") or "SOAP fault"
            subcodes = " ".join(fault.xpath(".//*[local-name()='Value']/text()"))
            is_auth = r.status_code in (400, 401) and (
                "NotAuthorized" in subcodes or "FailedAuthentication" in subcodes
                or "authoriz" in reason.lower() or "Sender not" in reason)
            raise OnvifError(reason, auth=is_auth or r.status_code == 401, status=r.status_code)
        if r.status_code == 401:
            raise OnvifError("Not authorized", auth=True, status=401)
        return body[0] if body is not None and len(body) else None

    # ------------------------------------------------------------------ device service
    async def sync_time(self) -> dict:
        """GetSystemDateAndTime works unauthenticated and lets us correct for clock drift."""
        resp = await self.call("<tds:GetSystemDateAndTime/>", auth=False)
        info = xe(resp, "SystemDateAndTime")
        utc = xe(info, "UTCDateTime")
        out = {
            "type": x(info, "DateTimeType"),
            "ntp": x(info, "NTP") ,
            "dst": x(info, "DaylightSavings") == "true",
            "timezone": x(info, "TimeZone/TZ"),
            "utc": None,
        }
        if utc is not None:
            try:
                d = dt.datetime(
                    int(x(utc, "Date/Year")), int(x(utc, "Date/Month")), int(x(utc, "Date/Day")),
                    int(x(utc, "Time/Hour")), int(x(utc, "Time/Minute")), int(x(utc, "Time/Second")),
                    tzinfo=dt.timezone.utc)
                self.time_offset = d - dt.datetime.now(dt.timezone.utc)
                out["utc"] = d.isoformat()
            except (TypeError, ValueError):
                pass
        return out

    async def get_device_information(self, auth: bool = True) -> dict:
        r = await self.call("<tds:GetDeviceInformation/>", auth=auth)
        return {k: x(r, k) for k in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId")}

    async def get_services(self) -> dict:
        r = await self.call("<tds:GetServices><tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>")
        services = {}
        for s in xs(r, "Service"):
            services[x(s, "Namespace")] = x(s, "XAddr")
        self.media_xaddr = services.get(NS["trt"]) or self.media_xaddr
        return services

    async def ensure_media(self):
        if self.media_xaddr:
            return
        try:
            await self.get_services()
        except OnvifError as e:
            if e.auth:
                raise
        if not self.media_xaddr:
            r = await self.call("<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>")
            self.media_xaddr = x(r, "Capabilities/Media/XAddr") or self.xaddr

    async def get_scopes(self, auth: bool = True) -> list[str]:
        r = await self.call("<tds:GetScopes/>", auth=auth)
        return [x(s, "ScopeItem") for s in xs(r, "Scopes")]

    async def set_scopes(self, scopes: list[str]):
        items = "".join(f"<tds:Scopes>{escape(s)}</tds:Scopes>" for s in scopes)
        await self.call(f"<tds:SetScopes>{items}</tds:SetScopes>")

    async def get_hostname(self) -> dict:
        r = await self.call("<tds:GetHostname/>")
        h = xe(r, "HostnameInformation")
        return {"name": x(h, "Name"), "from_dhcp": x(h, "FromDHCP") == "true"}

    async def set_hostname(self, name: str):
        await self.call(f"<tds:SetHostname><tds:Name>{escape(name)}</tds:Name></tds:SetHostname>")

    async def get_users(self, auth: bool = True) -> list[dict]:
        r = await self.call("<tds:GetUsers/>", auth=auth)
        return [{"username": x(u, "Username"), "level": x(u, "UserLevel")} for u in xs(r, "User")]

    async def create_user(self, username: str, password: str, level: str = "Administrator", auth: bool = True):
        await self.call(
            f"<tds:CreateUsers><tds:User><tt:Username>{escape(username)}</tt:Username>"
            f"<tt:Password>{escape(password)}</tt:Password><tt:UserLevel>{level}</tt:UserLevel>"
            f"</tds:User></tds:CreateUsers>", auth=auth)

    async def set_user(self, username: str, password: str, level: str = "Administrator"):
        await self.call(
            f"<tds:SetUser><tds:User><tt:Username>{escape(username)}</tt:Username>"
            f"<tt:Password>{escape(password)}</tt:Password><tt:UserLevel>{level}</tt:UserLevel>"
            f"</tds:User></tds:SetUser>")

    async def delete_user(self, username: str):
        await self.call(f"<tds:DeleteUsers><tds:Username>{escape(username)}</tds:Username></tds:DeleteUsers>")

    async def get_network(self) -> dict:
        r = await self.call("<tds:GetNetworkInterfaces/>")
        ifaces = []
        for i in xs(r, "NetworkInterfaces"):
            v4 = xe(i, "IPv4")
            cfg = xe(v4, "Config")
            manual = xe(cfg, "Manual")
            ll = xe(cfg, "LinkLocal")
            from_dhcp = xe(cfg, "FromDHCP")
            cur = manual if manual is not None else from_dhcp
            ifaces.append({
                "token": i.get("token"),
                "name": x(i, "Info/Name"),
                "mac": x(i, "Info/HwAddress"),
                "enabled": x(i, "Enabled") == "true",
                "dhcp": x(cfg, "DHCP") == "true",
                "address": x(cur, "Address") if cur is not None else None,
                "prefix": int(x(cur, "PrefixLength") or 0) if cur is not None else None,
                "dhcp_address": x(from_dhcp, "Address"),
                "link_local": x(ll, "Address"),
            })
        gw = dns = None
        try:
            g = await self.call("<tds:GetNetworkDefaultGateway/>")
            gw = x(g, "NetworkGateway/IPv4Address")
        except OnvifError:
            pass
        try:
            d = await self.call("<tds:GetDNS/>")
            info = xe(d, "DNSInformation")
            dns = {
                "from_dhcp": x(info, "FromDHCP") == "true",
                "servers": [x(s, "IPv4Address") for s in xs(info, "DNSManual")] or
                           [x(s, "IPv4Address") for s in xs(info, "DNSFromDHCP")],
            }
        except OnvifError:
            pass
        return {"interfaces": ifaces, "gateway": gw, "dns": dns}

    async def set_network(self, token: str, dhcp: bool, address: str | None = None, prefix: int | None = None) -> bool:
        if dhcp:
            ipv4 = "<tt:Enabled>true</tt:Enabled><tt:DHCP>true</tt:DHCP>"
        else:
            ipv4 = (f"<tt:Enabled>true</tt:Enabled><tt:Manual><tt:Address>{escape(address)}</tt:Address>"
                    f"<tt:PrefixLength>{int(prefix)}</tt:PrefixLength></tt:Manual><tt:DHCP>false</tt:DHCP>")
        r = await self.call(
            f"<tds:SetNetworkInterfaces><tds:InterfaceToken>{escape(token)}</tds:InterfaceToken>"
            f"<tds:NetworkInterface><tt:Enabled>true</tt:Enabled><tt:IPv4>{ipv4}</tt:IPv4></tds:NetworkInterface>"
            f"</tds:SetNetworkInterfaces>")
        return x(r, "RebootNeeded") == "true"

    async def set_gateway(self, gateway: str):
        await self.call(
            f"<tds:SetNetworkDefaultGateway><tds:IPv4Address>{escape(gateway)}</tds:IPv4Address>"
            f"</tds:SetNetworkDefaultGateway>")

    async def set_dns(self, from_dhcp: bool, servers: list[str]):
        manual = "".join(f"<tds:DNSManual><tt:Type>IPv4</tt:Type><tt:IPv4Address>{escape(s)}</tt:IPv4Address></tds:DNSManual>"
                         for s in servers if s)
        await self.call(f"<tds:SetDNS><tds:FromDHCP>{b(from_dhcp)}</tds:FromDHCP>{'' if from_dhcp else manual}</tds:SetDNS>")

    async def get_ntp(self) -> dict:
        r = await self.call("<tds:GetNTP/>")
        info = xe(r, "NTPInformation")
        servers = []
        for n in xs(info, "NTPManual") or xs(info, "NTPFromDHCP"):
            servers.append(x(n, "DNSname") or x(n, "IPv4Address"))
        return {"from_dhcp": x(info, "FromDHCP") == "true", "servers": [s for s in servers if s]}

    async def set_ntp(self, from_dhcp: bool, servers: list[str]):
        def entry(s: str) -> str:
            is_ip = all(p.isdigit() for p in s.split(".")) and s.count(".") == 3
            if is_ip:
                return f"<tds:NTPManual><tt:Type>IPv4</tt:Type><tt:IPv4Address>{escape(s)}</tt:IPv4Address></tds:NTPManual>"
            return f"<tds:NTPManual><tt:Type>DNS</tt:Type><tt:DNSname>{escape(s)}</tt:DNSname></tds:NTPManual>"
        manual = "" if from_dhcp else "".join(entry(s) for s in servers if s)
        await self.call(f"<tds:SetNTP><tds:FromDHCP>{b(from_dhcp)}</tds:FromDHCP>{manual}</tds:SetNTP>")

    async def set_date_time(self, mode: str, timezone: str | None, dst: bool = False, utc: dt.datetime | None = None):
        """mode: 'NTP' or 'Manual'. Manual uses utc (defaults to this computer's clock)."""
        tz = f"<tds:TimeZone><tt:TZ>{escape(timezone)}</tt:TZ></tds:TimeZone>" if timezone else ""
        utc_xml = ""
        if mode == "Manual":
            u = utc or dt.datetime.now(dt.timezone.utc)
            utc_xml = (f"<tds:UTCDateTime><tt:Time><tt:Hour>{u.hour}</tt:Hour><tt:Minute>{u.minute}</tt:Minute>"
                       f"<tt:Second>{u.second}</tt:Second></tt:Time><tt:Date><tt:Year>{u.year}</tt:Year>"
                       f"<tt:Month>{u.month}</tt:Month><tt:Day>{u.day}</tt:Day></tt:Date></tds:UTCDateTime>")
        await self.call(
            f"<tds:SetSystemDateAndTime><tds:DateTimeType>{mode}</tds:DateTimeType>"
            f"<tds:DaylightSavings>{b(dst)}</tds:DaylightSavings>{tz}{utc_xml}</tds:SetSystemDateAndTime>")

    async def reboot(self) -> str | None:
        r = await self.call("<tds:SystemReboot/>")
        return x(r, "Message")

    async def factory_default(self, hard: bool = False):
        await self.call(f"<tds:SetSystemFactoryDefault><tds:FactoryDefault>{'Hard' if hard else 'Soft'}"
                        f"</tds:FactoryDefault></tds:SetSystemFactoryDefault>")

    # ------------------------------------------------------------------ media service
    async def get_profiles(self) -> list[dict]:
        await self.ensure_media()
        r = await self.call("<trt:GetProfiles/>", url=self.media_xaddr)
        out = []
        for p in xs(r, "Profiles"):
            vec = xe(p, "VideoEncoderConfiguration")
            out.append({
                "token": p.get("token"),
                "name": x(p, "Name"),
                "encoder": _parse_vec(vec) if vec is not None else None,
            })
        return out

    async def get_encoder_options(self, config_token: str) -> dict:
        await self.ensure_media()
        r = await self.call(
            f"<trt:GetVideoEncoderConfigurationOptions><trt:ConfigurationToken>{escape(config_token)}"
            f"</trt:ConfigurationToken></trt:GetVideoEncoderConfigurationOptions>", url=self.media_xaddr)
        o = xe(r, "Options")
        opts: dict[str, Any] = {"encodings": [], "quality": None}
        q = xe(o, "QualityRange")
        if q is not None:
            opts["quality"] = [float(x(q, "Min") or 0), float(x(q, "Max") or 100)]
        for enc in ("H264", "H265", "JPEG"):
            e = xe(o, enc)
            if e is None:
                ext = xe(o, f"Extension/{enc}")
                e = ext
            if e is None:
                continue
            opts["encodings"].append({
                "encoding": enc,
                "resolutions": [{"w": int(x(rr, "Width")), "h": int(x(rr, "Height"))}
                                for rr in xs(e, "ResolutionsAvailable")],
                "fps": [int(x(e, "FrameRateRange/Min") or 1), int(x(e, "FrameRateRange/Max") or 30)],
                "gov": [int(x(e, "GovLengthRange/Min") or 1), int(x(e, "GovLengthRange/Max") or 300)],
            })
        return opts

    async def get_encoder_config(self, token: str):
        await self.ensure_media()
        r = await self.call(
            f"<trt:GetVideoEncoderConfiguration><trt:ConfigurationToken>{escape(token)}</trt:ConfigurationToken>"
            f"</trt:GetVideoEncoderConfiguration>", url=self.media_xaddr)
        return xe(r, "Configuration")

    async def set_encoder(self, token: str, encoding: str, width: int, height: int, fps: int,
                          bitrate_kbps: int, gov: int | None, quality: float | None):
        """Read-modify-write so we keep fields we don't expose (multicast, session timeout...)."""
        cfg = await self.get_encoder_config(token)
        if cfg is None:
            raise OnvifError("Encoder configuration not found")
        name = x(cfg, "Name") or token
        use_count = x(cfg, "UseCount") or "1"
        q = quality if quality is not None else float(x(cfg, "Quality") or 50)
        session = x(cfg, "SessionTimeout") or "PT60S"
        mc = xe(cfg, "Multicast")
        mc_xml = ""
        if mc is not None:
            mc_xml = (f"<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>"
                      f"{x(mc, 'Address/IPv4Address') or '0.0.0.0'}</tt:IPv4Address></tt:Address>"
                      f"<tt:Port>{x(mc, 'Port') or 0}</tt:Port><tt:TTL>{x(mc, 'TTL') or 1}</tt:TTL>"
                      f"<tt:AutoStart>{x(mc, 'AutoStart') or 'false'}</tt:AutoStart></tt:Multicast>")
        enc_wire = "H264" if encoding == "H264" else ("JPEG" if encoding == "JPEG" else "H265")
        profile_xml = ""
        if enc_wire == "H264":
            profile = x(cfg, "H264/H264Profile") or "Main"
            profile_xml = (f"<tt:H264><tt:GovLength>{gov or int(x(cfg, 'H264/GovLength') or fps)}</tt:GovLength>"
                           f"<tt:H264Profile>{profile}</tt:H264Profile></tt:H264>")
        body = (
            f"<trt:SetVideoEncoderConfiguration><trt:Configuration token=\"{escape(token)}\">"
            f"<tt:Name>{escape(name)}</tt:Name><tt:UseCount>{use_count}</tt:UseCount>"
            f"<tt:Encoding>{enc_wire}</tt:Encoding>"
            f"<tt:Resolution><tt:Width>{int(width)}</tt:Width><tt:Height>{int(height)}</tt:Height></tt:Resolution>"
            f"<tt:Quality>{q}</tt:Quality>"
            f"<tt:RateControl><tt:FrameRateLimit>{int(fps)}</tt:FrameRateLimit>"
            f"<tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>{int(bitrate_kbps)}</tt:BitrateLimit>"
            f"</tt:RateControl>{profile_xml}{mc_xml}<tt:SessionTimeout>{session}</tt:SessionTimeout>"
            f"</trt:Configuration><trt:ForcePersistence>true</trt:ForcePersistence>"
            f"</trt:SetVideoEncoderConfiguration>")
        await self.call(body, url=self.media_xaddr)

    async def get_stream_uri(self, profile_token: str) -> str | None:
        await self.ensure_media()
        r = await self.call(
            f"<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>"
            f"<tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
            f"<trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken></trt:GetStreamUri>", url=self.media_xaddr)
        return x(r, "MediaUri/Uri")

    async def get_snapshot_uri(self, profile_token: str) -> str | None:
        await self.ensure_media()
        r = await self.call(
            f"<trt:GetSnapshotUri><trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken></trt:GetSnapshotUri>",
            url=self.media_xaddr)
        return x(r, "MediaUri/Uri")

    async def fetch_snapshot(self, uri: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=self.timeout, verify=False, trust_env=False) as c:
            r = await c.get(uri)
            if r.status_code == 401 and self.username:
                r = await c.get(uri, auth=httpx.DigestAuth(self.username, self.password or ""))
                if r.status_code == 401:
                    r = await c.get(uri, auth=(self.username, self.password or ""))
            if r.status_code != 200:
                raise OnvifError(f"Snapshot failed (HTTP {r.status_code})", status=r.status_code)
            return r.content, r.headers.get("content-type", "image/jpeg")


def _parse_vec(vec) -> dict:
    return {
        "token": vec.get("token"),
        "name": x(vec, "Name"),
        "encoding": x(vec, "Encoding"),
        "width": int(x(vec, "Resolution/Width") or 0),
        "height": int(x(vec, "Resolution/Height") or 0),
        "quality": float(x(vec, "Quality") or 0),
        "fps": int(x(vec, "RateControl/FrameRateLimit") or 0),
        "bitrate": int(x(vec, "RateControl/BitrateLimit") or 0),
        "gov": int(x(vec, "H264/GovLength") or 0) or None,
        "h264_profile": x(vec, "H264/H264Profile"),
    }
