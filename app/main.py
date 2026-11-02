"""Avigilon ONVIF Dashboard: FastAPI backend."""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .discovery import discover
from .onvif import OnvifDevice, OnvifError

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Avigilon ONVIF Dashboard")

RECORDER_RE = re.compile(r"(nvr|nvs|hdva|appliance|recorder|storage|acc[- ]?es|\bes\b|workstation)", re.I)


# ============================================================== helpers
def _public(d: dict) -> dict:
    """Device record without the password."""
    out = {k: v for k, v in d.items() if k != "password"}
    out["has_password"] = bool(d.get("password")) or d.get("password") == ""
    return out


def _host(xaddr: str) -> str | None:
    return urlparse(xaddr).hostname


def _client(d: dict) -> OnvifDevice:
    dev = OnvifDevice(d["xaddr"], d.get("username"), d.get("password"))
    if d.get("media_xaddr"):
        dev.media_xaddr = d["media_xaddr"]
    if d.get("time_offset"):
        dev.time_offset = dt.timedelta(seconds=d["time_offset"])
    return dev


def _get(dev_id: str) -> dict:
    d = store.load_devices().get(dev_id)
    if not d:
        raise HTTPException(404, "Unknown device")
    return d


def _classify(d: dict) -> dict:
    blob = " ".join(str(v) for v in (d.get("manufacturer"), d.get("model"), d.get("hardware"),
                                      d.get("name"), " ".join(d.get("types") or []),
                                      " ".join(d.get("scope_types") or [])) if v)
    is_rec = bool(re.search(r"NetworkVideoStorage|NetworkVideoRecorder|Recorder", blob)) or \
        bool(RECORDER_RE.search(f"{d.get('model') or ''} {d.get('hardware') or ''}"))
    ip = d.get("ip")
    ll = False
    try:
        ll = ipaddress.ip_address(ip).is_link_local if ip else False
    except ValueError:
        pass
    return {
        "kind": "recorder" if is_rec else "camera",
        # 00:18:85 is Avigilon's MAC prefix, useful when a locked device won't tell us its manufacturer.
        "is_avigilon": "avigilon" in blob.lower() or (d.get("mac") or "").lower().replace("-", ":").startswith("00:18:85"),
        "link_local": ll,
        "factory_fresh": d.get("state") in ("factory", "default_creds"),
    }


async def assess(dev_id: str) -> dict:
    """Work out how a device can be reached and whether it's factory-fresh.

    state:
      offline       - no ONVIF response
      factory       - ONVIF answers without auth and has no users (factory default state)
      default_creds - a known default password works
      ready         - our saved credentials work
      auth_failed   - saved credentials rejected
      locked        - password protected, we don't know it
    """
    d = store.load_devices()[dev_id]
    dev = _client(d)
    upd: dict[str, Any] = {"last_checked": time.time()}
    try:
        t = await dev.sync_time()
        upd["time_offset"] = dev.time_offset.total_seconds()
        upd["device_time"] = t.get("utc")
    except OnvifError as e:
        if not e.status:
            upd.update(state="offline", error=str(e))
            return _finish(dev_id, upd)

    # 1. Factory default state: unauthenticated access + no users.
    unauth_users = None
    try:
        unauth_users = await dev.get_users(auth=False)
    except OnvifError:
        pass

    async def try_creds(u, p) -> dict | None:
        c = OnvifDevice(d["xaddr"], u, p, time_offset=dev.time_offset)
        try:
            info = await c.get_device_information()
            return info
        except OnvifError:
            return None

    info = None
    if unauth_users is not None and len(unauth_users) == 0:
        upd["state"] = "factory"
        try:
            info = await dev.get_device_information(auth=False)
        except OnvifError:
            pass
    elif d.get("username") is not None and d.get("password") is not None:
        info = await try_creds(d["username"], d["password"])
        upd["state"] = "ready" if info else "auth_failed"
        if info and d.get("is_default_creds"):
            upd["state"] = "default_creds"

    if not info and upd.get("state") != "factory":
        for cred in store.load_settings()["default_credentials"]:
            info = await try_creds(cred["username"], cred["password"])
            if info:
                upd.update(state="default_creds", username=cred["username"], password=cred["password"],
                           is_default_creds=True)
                break
        else:
            if upd.get("state") != "auth_failed":
                upd["state"] = "locked"
            # Unauthenticated device info is still worth a try for display purposes.
            try:
                info = await dev.get_device_information(auth=False)
            except OnvifError:
                pass

    if info:
        upd.update(manufacturer=info.get("Manufacturer"), model=info.get("Model"),
                   firmware=info.get("FirmwareVersion"), serial=info.get("SerialNumber"),
                   hardware_id=info.get("HardwareId"))
    if not d.get("mac") and upd.get("state") in ("ready", "factory", "default_creds"):
        c = OnvifDevice(d["xaddr"], upd.get("username", d.get("username")), upd.get("password", d.get("password")),
                        time_offset=dev.time_offset)
        if upd["state"] == "factory":
            c.username = None
        try:
            net = await c.get_network()
            if net["interfaces"]:
                upd["mac"] = net["interfaces"][0]["mac"]
        except OnvifError:
            pass
    upd["error"] = None
    return _finish(dev_id, upd)


def _finish(dev_id: str, upd: dict) -> dict:
    d = store.update_device(dev_id, **upd)
    d = store.update_device(dev_id, **_classify(d))
    return d


def _upsert_discovered(item: dict) -> str:
    dev_id = store.device_id(item["urn"])
    existing = store.load_devices().get(dev_id, {})
    xaddr = item["xaddrs"][0] if item["xaddrs"] else f"http://{item['source_ip']}/onvif/device_service"
    store.update_device(
        dev_id,
        urn=item["urn"], xaddr=xaddr, xaddrs=item["xaddrs"], ip=_host(xaddr) or item["source_ip"],
        name=item.get("name") or existing.get("name"), hardware=item.get("hardware") or existing.get("hardware"),
        location=item.get("location"), mac=item.get("mac") or existing.get("mac"),
        types=item.get("types"), scope_types=item.get("scope_types") or [], scopes=item.get("scopes"),
        profiles_supported=item.get("profiles"), last_seen=time.time(),
        label=existing.get("label"),
    )
    return dev_id


async def _gather_limited(coros, limit=16):
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            try:
                return await c
            except Exception as e:  # never let one device break the scan
                return e
    return await asyncio.gather(*(run(c) for c in coros))


def _err(e: Exception) -> HTTPException:
    if isinstance(e, OnvifError):
        return HTTPException(401 if e.auth else 502, str(e))
    return HTTPException(500, str(e))


# ============================================================== models
class DiscoverReq(BaseModel):
    timeout: float | None = None
    targets: list[str] | None = None


class ManualReq(BaseModel):
    address: str
    username: str | None = None
    password: str | None = None


class CredReq(BaseModel):
    username: str
    password: str


class IdentityReq(BaseModel):
    hostname: str | None = None
    name: str | None = None
    location: str | None = None
    label: str | None = None


class NetworkReq(BaseModel):
    token: str
    dhcp: bool
    address: str | None = None
    prefix: int | None = 24
    gateway: str | None = None
    dns_dhcp: bool = True
    dns: list[str] = []


class TimeReq(BaseModel):
    mode: str = "NTP"  # NTP | Manual (Manual = sync to this computer)
    ntp_dhcp: bool = False
    ntp_servers: list[str] = []
    timezone: str | None = None
    dst: bool = False


class UserReq(BaseModel):
    username: str
    password: str
    level: str = "Administrator"


class EncoderReq(BaseModel):
    token: str
    encoding: str = "H264"
    width: int
    height: int
    fps: int
    bitrate: int
    gov: int | None = None
    quality: float | None = None


class SetupReq(BaseModel):
    admin_username: str = "admin"
    admin_password: str
    hostname: str | None = None
    name: str | None = None
    location: str | None = None
    time: TimeReq | None = None
    video: list[EncoderReq] = []
    video_preset: dict | None = None  # {"width","height","fps","bitrate"} applied to the main profile
    network: NetworkReq | None = None
    reboot: bool = False


class BatchReq(BaseModel):
    ids: list[str]
    template: SetupReq
    ip_start: str | None = None  # static IP for first device, incremented per device
    hostname_prefix: str | None = None


class FactoryReq(BaseModel):
    hard: bool = False


# ============================================================== routes: inventory
@app.get("/api/devices")
def list_devices():
    devs = sorted(store.load_devices().values(),
                  key=lambda d: (not d.get("factory_fresh"), d.get("kind") != "camera",
                                 [int(p) if p.isdigit() else p for p in (d.get("ip") or "").split(".")]))
    return [_public(d) for d in devs]


@app.post("/api/discover")
async def api_discover(req: DiscoverReq):
    s = store.load_settings()
    targets = req.targets if req.targets is not None else s.get("scan_targets", [])
    found = await discover(timeout=req.timeout or s.get("discovery_timeout", 3), targets=targets)
    ids = [_upsert_discovered(f) for f in found]
    await _gather_limited([assess(i) for i in ids])
    devs = store.load_devices()
    return {"found": len(ids), "devices": [_public(devs[i]) for i in ids if i in devs]}


@app.post("/api/devices/manual")
async def add_manual(req: ManualReq):
    a = req.address.strip()
    if not a.startswith("http"):
        a = f"http://{a}/onvif/device_service"
    dev_id = store.device_id(f"manual:{_host(a)}")
    # If discovery already knows this IP, reuse that record.
    for d in store.load_devices().values():
        if d.get("ip") == _host(a):
            dev_id = d["id"]
            break
    upd: dict[str, Any] = {"xaddr": a, "ip": _host(a), "last_seen": time.time()}
    if req.username is not None:
        upd.update(username=req.username, password=req.password or "", is_default_creds=False)
    store.update_device(dev_id, urn=store.load_devices().get(dev_id, {}).get("urn") or f"manual:{_host(a)}", **upd)
    return _public(await assess(dev_id))


@app.post("/api/devices/{dev_id}/refresh")
async def refresh(dev_id: str):
    _get(dev_id)
    return _public(await assess(dev_id))


@app.delete("/api/devices/{dev_id}")
def forget(dev_id: str):
    store.delete_device(dev_id)
    return {"ok": True}


@app.post("/api/devices/{dev_id}/credentials")
async def set_credentials(dev_id: str, req: CredReq):
    d = _get(dev_id)
    c = OnvifDevice(d["xaddr"], req.username, req.password,
                    time_offset=dt.timedelta(seconds=d.get("time_offset") or 0))
    try:
        await c.get_device_information()
    except OnvifError as e:
        raise HTTPException(401, f"Login failed: {e}")
    store.update_device(dev_id, username=req.username, password=req.password, is_default_creds=False)
    return _public(await assess(dev_id))


# ============================================================== routes: config
@app.get("/api/devices/{dev_id}/config")
async def get_config(dev_id: str):
    d = _get(dev_id)
    dev = _client(d)
    unauth = d.get("state") == "factory"

    async def section(fn):
        try:
            return {"ok": True, "data": await fn()}
        except OnvifError as e:
            return {"ok": False, "error": str(e), "auth": e.auth}
        except Exception as e:  # pragma: no cover
            return {"ok": False, "error": str(e)}

    if unauth:
        dev.username = None
    results = await asyncio.gather(
        section(lambda: dev.get_device_information(auth=not unauth)),
        section(dev.get_hostname), section(dev.get_network), section(dev.get_ntp),
        section(dev.sync_time), section(lambda: dev.get_users(auth=not unauth)),
        section(lambda: dev.get_scopes(auth=not unauth)),
    )
    profiles = await section(dev.get_profiles)
    if dev.media_xaddr and dev.media_xaddr != d.get("media_xaddr"):
        store.update_device(dev_id, media_xaddr=dev.media_xaddr)
    keys = ["info", "hostname", "network", "ntp", "time", "users", "scopes"]
    out = dict(zip(keys, results))
    if out["network"]["ok"] and out["network"]["data"]["interfaces"] and not d.get("mac"):
        store.update_device(dev_id, mac=out["network"]["data"]["interfaces"][0]["mac"])
    if out["hostname"]["ok"]:
        store.update_device(dev_id, hostname=out["hostname"]["data"]["name"])
    out["profiles"] = profiles
    out["device"] = _public(store.load_devices()[dev_id])
    return out


@app.get("/api/devices/{dev_id}/encoder-options/{token}")
async def encoder_options(dev_id: str, token: str):
    try:
        return await _client(_get(dev_id)).get_encoder_options(token)
    except Exception as e:
        raise _err(e)


@app.put("/api/devices/{dev_id}/identity")
async def set_identity(dev_id: str, req: IdentityReq):
    d = _get(dev_id)
    dev = _client(d)
    try:
        if req.hostname:
            await dev.set_hostname(req.hostname)
        if req.name is not None or req.location is not None:
            await _set_name_location(dev, req.name, req.location)
    except Exception as e:
        raise _err(e)
    upd: dict[str, Any] = {}
    if req.name is not None:
        upd["name"] = req.name
    if req.location is not None:
        upd["location"] = req.location
    if req.label is not None:
        upd["label"] = req.label
    store.update_device(dev_id, **upd)
    return {"ok": True}


async def _set_name_location(dev: OnvifDevice, name: str | None, location: str | None):
    from urllib.parse import quote
    current = []
    try:
        current = await dev.get_scopes()
    except OnvifError:
        pass
    scopes = []
    old_name = next((s for s in current if "/name/" in s), None)
    old_loc = [s for s in current if "/location/" in s]
    if name is not None:
        scopes.append(f"onvif://www.onvif.org/name/{quote(name)}")
    elif old_name:
        scopes.append(old_name)
    if location is not None:
        if location:
            scopes.append(f"onvif://www.onvif.org/location/{quote(location)}")
    else:
        scopes += old_loc
    await dev.set_scopes(scopes)


@app.put("/api/devices/{dev_id}/network")
async def set_network(dev_id: str, req: NetworkReq):
    d = _get(dev_id)
    try:
        return await _apply_network(dev_id, d, _client(d), req)
    except Exception as e:
        raise _err(e)


async def _apply_network(dev_id: str, d: dict, dev: OnvifDevice, req: NetworkReq) -> dict:
    if not req.dhcp:
        ipaddress.ip_address(req.address)
    # Gateway and DNS first: once the IP changes we lose the connection.
    if not req.dhcp and req.gateway:
        await dev.set_gateway(req.gateway)
    try:
        await dev.set_dns(req.dns_dhcp, req.dns)
    except OnvifError:
        pass
    reboot = await dev.set_network(req.token, req.dhcp, req.address, req.prefix)
    new_ip = None
    if not req.dhcp and req.address and req.address != d.get("ip"):
        new_ip = req.address
        u = urlparse(d["xaddr"])
        netloc = new_ip + (f":{u.port}" if u.port else "")
        store.update_device(dev_id, ip=new_ip, xaddr=urlunparse(u._replace(netloc=netloc)), media_xaddr=None)
    return {"ok": True, "reboot_needed": reboot, "new_ip": new_ip,
            "note": "Device IP changed. Make sure this computer can reach the new subnet." if new_ip else None}


@app.put("/api/devices/{dev_id}/time")
async def set_time(dev_id: str, req: TimeReq):
    try:
        await _apply_time(_client(_get(dev_id)), req)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


async def _apply_time(dev: OnvifDevice, req: TimeReq):
    if req.mode == "NTP":
        await dev.set_ntp(req.ntp_dhcp, req.ntp_servers)
    await dev.set_date_time(req.mode, req.timezone, req.dst)


@app.post("/api/devices/{dev_id}/users")
async def add_user(dev_id: str, req: UserReq):
    d = _get(dev_id)
    try:
        await _client(d).create_user(req.username, req.password, req.level, auth=d.get("state") != "factory")
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.put("/api/devices/{dev_id}/users")
async def update_user(dev_id: str, req: UserReq):
    d = _get(dev_id)
    try:
        await _client(d).set_user(req.username, req.password, req.level)
    except Exception as e:
        raise _err(e)
    if req.username == d.get("username"):
        store.update_device(dev_id, password=req.password, is_default_creds=False)
    return {"ok": True}


@app.delete("/api/devices/{dev_id}/users/{username}")
async def remove_user(dev_id: str, username: str):
    d = _get(dev_id)
    if username == d.get("username"):
        raise HTTPException(400, "Refusing to delete the account this dashboard uses to log in.")
    try:
        await _client(d).delete_user(username)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.put("/api/devices/{dev_id}/encoder")
async def set_encoder(dev_id: str, req: EncoderReq):
    try:
        await _client(_get(dev_id)).set_encoder(req.token, req.encoding, req.width, req.height, req.fps,
                                                req.bitrate, req.gov, req.quality)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.get("/api/devices/{dev_id}/stream-uri")
async def stream_uri(dev_id: str, profile: str):
    try:
        return {"uri": await _client(_get(dev_id)).get_stream_uri(profile)}
    except Exception as e:
        raise _err(e)


@app.get("/api/devices/{dev_id}/snapshot")
async def snapshot(dev_id: str, profile: str | None = None):
    d = _get(dev_id)
    dev = _client(d)
    if d.get("state") == "factory":
        dev.username = None
    try:
        if not profile:
            profile = d.get("main_profile")
            if not profile:
                profiles = await dev.get_profiles()
                if not profiles:
                    raise OnvifError("Device has no media profiles")
                # Use the lowest-resolution profile for thumbnails, it is lighter on the device.
                profiles.sort(key=lambda p: (p["encoder"] or {}).get("width", 0))
                profile = profiles[0]["token"]
                store.update_device(dev_id, main_profile=profile, media_xaddr=dev.media_xaddr)
        uri = await dev.get_snapshot_uri(profile)
        if not uri:
            raise OnvifError("Device has no snapshot URI")
        img, ctype = await dev.fetch_snapshot(uri)
    except Exception as e:
        raise _err(e)
    return Response(img, media_type=ctype, headers={"Cache-Control": "no-store"})


@app.post("/api/devices/{dev_id}/reboot")
async def reboot(dev_id: str):
    try:
        msg = await _client(_get(dev_id)).reboot()
    except Exception as e:
        raise _err(e)
    store.update_device(dev_id, state="offline", error="Rebooting")
    return {"ok": True, "message": msg}


@app.post("/api/devices/{dev_id}/factory-reset")
async def factory_reset(dev_id: str, req: FactoryReq):
    try:
        await _client(_get(dev_id)).factory_default(req.hard)
    except Exception as e:
        raise _err(e)
    store.update_device(dev_id, state="offline", error="Factory reset in progress",
                        username=None, password=None, is_default_creds=False)
    return {"ok": True}


# ============================================================== routes: first-time setup
async def run_setup(dev_id: str, req: SetupReq) -> dict:
    d = _get(dev_id)
    dev = _client(d)
    steps: list[dict] = []

    async def step(name: str, fn):
        try:
            res = await fn()
            steps.append({"step": name, "ok": True, **(res if isinstance(res, dict) else {})})
            return True
        except Exception as e:
            steps.append({"step": name, "ok": False, "error": str(e)})
            return False

    # 1. Secure the device -------------------------------------------------
    async def secure():
        if d.get("state") == "factory":
            dev.username = None
            await dev.create_user(req.admin_username, req.admin_password, "Administrator", auth=False)
        elif d.get("username") == req.admin_username:
            await dev.set_user(req.admin_username, req.admin_password, "Administrator")
        else:
            await dev.create_user(req.admin_username, req.admin_password, "Administrator")
        dev.username, dev.password = req.admin_username, req.admin_password
        dev._use_http_digest = False
        await dev.get_device_information()  # verify new login works
        store.update_device(dev_id, username=req.admin_username, password=req.admin_password,
                            is_default_creds=False)
        return {"detail": f"Administrator '{req.admin_username}' set"}

    if not await step("Set admin password", secure):
        return {"ok": False, "steps": steps, "device": _public(store.load_devices()[dev_id])}

    # 2. Identity ----------------------------------------------------------
    if req.hostname:
        await step("Hostname", lambda: dev.set_hostname(req.hostname))
    if req.name or req.location:
        async def ident():
            await _set_name_location(dev, req.name, req.location)
            store.update_device(dev_id, **{k: v for k, v in (("name", req.name), ("location", req.location)) if v})
        await step("Device name / location", ident)

    # 3. Time --------------------------------------------------------------
    if req.time:
        await step("Date, time & NTP", lambda: _apply_time(dev, req.time))

    # 4. Video -------------------------------------------------------------
    encoders = list(req.video)
    if req.video_preset and not encoders:
        try:
            profiles = await dev.get_profiles()
            main = next((p for p in profiles if p.get("encoder")), None)
            if main:
                e = main["encoder"]
                p = req.video_preset
                encoders.append(EncoderReq(token=e["token"], encoding=p.get("encoding", e["encoding"] or "H264"),
                                           width=p.get("width", e["width"]), height=p.get("height", e["height"]),
                                           fps=p.get("fps", e["fps"]), bitrate=p.get("bitrate", e["bitrate"]),
                                           gov=p.get("gov")))
        except OnvifError as ex:
            steps.append({"step": "Video profiles", "ok": False, "error": str(ex)})
    for enc in encoders:
        await step(f"Video encoder {enc.token}", lambda enc=enc: dev.set_encoder(
            enc.token, enc.encoding, enc.width, enc.height, enc.fps, enc.bitrate, enc.gov, enc.quality))

    # 5. Network (last, because the address may change) ---------------------
    if req.network:
        d = store.load_devices()[dev_id]
        await step("Network", lambda: _apply_network(dev_id, d, dev, req.network))

    if req.reboot:
        await step("Reboot", dev.reboot)

    await asyncio.sleep(0.5)
    try:
        await assess(dev_id)
    except Exception:
        pass
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "device": _public(store.load_devices()[dev_id])}


@app.post("/api/devices/{dev_id}/setup")
async def setup(dev_id: str, req: SetupReq):
    return await run_setup(dev_id, req)


@app.post("/api/setup/batch")
async def batch_setup(req: BatchReq):
    results = []
    next_ip = ipaddress.ip_address(req.ip_start) if req.ip_start else None
    for i, dev_id in enumerate(req.ids):
        t = req.template.model_copy(deep=True)
        d = _get(dev_id)
        if req.hostname_prefix:
            t.hostname = f"{req.hostname_prefix}{i + 1:02d}"
            t.name = t.name or t.hostname
        if t.network and next_ip is not None and not t.network.dhcp:
            t.network.address = str(next_ip)
            next_ip += 1
        if t.network and not t.network.token:
            try:
                net = await _client(d).get_network()
                t.network.token = net["interfaces"][0]["token"]
            except Exception:
                pass
        r = await run_setup(dev_id, t)
        results.append({"id": dev_id, "ip": d.get("ip"), **r})
    return {"results": results}


# ============================================================== routes: settings
@app.get("/api/settings")
def get_settings():
    return store.load_settings()


@app.put("/api/settings")
def put_settings(s: dict):
    cur = store.load_settings()
    cur.update({k: v for k, v in s.items() if k in store.DEFAULT_SETTINGS})
    store.save_settings(cur)
    return cur


# ============================================================== static GUI
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
