"""Gree / Tosot air conditioners on the LAN.

Nothing to do with the assistant.  It is here because the server is already on
the same network as the units and already reachable from his phone, so the
Action Button works from anywhere rather than only on home WiFi.

UDP on port 7000, AES inside, JSON inside that.  Every unit accepts a published
factory key until you *bind* to it, which hands back a key of its own; from then
on that key encrypts everything.  Binding is additive — the Tosot app keeps
working alongside this.

ponytail: ECB for the v1 handshake because that is what the protocol specifies,
not a choice.  V2 firmware negotiates GCM and is detected per unit.
"""

from __future__ import annotations

import base64
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

GENERIC_KEY = b"a3K8Bx%2r8Y7#xDh"
GCM_KEY = b"{yxAHAY_Lm6pbC/<"
GCM_IV = bytes.fromhex("5440784889c0d92e")
GCM_AAD = b"qualcomm-test"
PORT = 7000

#: Where to look for units. Broadcast alone found one of three on his LAN — a
#: /22 with client isolation on part of it — so the addresses of units already
#: known are probed directly as well. Cheap: three extra UDP datagrams.
BROADCASTS = ("255.255.255.255", "192.168.68.255", "192.168.71.255")

#: Fields a unit accepts. Kept short: these are the ones he uses.
FIELDS = {"power": "Pow", "mode": "Mod", "temp": "SetTem", "fan": "WdSpd",
          "swing": "SwUpDn", "light": "Lig", "turbo": "Tur", "quiet": "Quiet"}
MODES = {"auto": 0, "cool": 1, "dry": 2, "fan": 3, "heat": 4}


class ACError(RuntimeError):
    pass


def _ecb(key: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.ECB())  # noqa: S305 - protocol-mandated


def _pad(raw: bytes) -> bytes:
    n = 16 - len(raw) % 16
    return raw + bytes([n]) * n


def encrypt(payload: dict, key: bytes = GENERIC_KEY) -> str:
    e = _ecb(key).encryptor()
    return base64.b64encode(e.update(_pad(json.dumps(payload).encode())) + e.finalize()).decode()


def decrypt(blob: str, key: bytes = GENERIC_KEY) -> dict:
    d = _ecb(key).decryptor()
    raw = d.update(base64.b64decode(blob)) + d.finalize()
    return json.loads(raw[: -raw[-1]].decode(errors="ignore"))


def encrypt_gcm(payload: dict, key: bytes = GCM_KEY) -> tuple[str, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    out = AESGCM(key).encrypt(GCM_IV, json.dumps(payload).encode(), GCM_AAD)
    return base64.b64encode(out[:-16]).decode(), base64.b64encode(out[-16:]).decode()


def decrypt_gcm(blob: str, tag: str, key: bytes = GCM_KEY) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = AESGCM(key).decrypt(GCM_IV, base64.b64decode(blob) + base64.b64decode(tag), GCM_AAD)
    return json.loads(raw.decode())


@dataclass
class Unit:
    mac: str
    host: str
    key: str = ""
    name: str = ""
    gcm: bool = False

    def as_dict(self, *, with_key: bool = False) -> dict:
        """The unit as data. The key is left out unless asked for: this feeds
        /v1/ac and the model's tool output, and neither needs the secret that
        controls the appliance."""
        out = {"mac": self.mac, "host": self.host, "name": self.name,
               "bound": bool(self.key), "gcm": self.gcm}
        if with_key:
            out["key"] = self.key
        return out


@dataclass
class Gree:
    """Units he has bound, kept by the runtime."""

    units: dict[str, Unit] = field(default_factory=dict)
    timeout: float = 2.0

    def _send(self, host: str, packet: dict) -> dict:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.timeout)
        try:
            s.sendto(json.dumps(packet).encode(), (host, PORT))
            data, _ = s.recvfrom(65535)
        except (TimeoutError, OSError) as e:
            raise ACError(f"{host} did not answer: {e}") from e
        finally:
            s.close()
        return json.loads(data.decode())

    def scan(self, broadcast: str | None = None) -> list[Unit]:
        """Find units. Returns unbound entries; bind before commanding."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(self.timeout)
        found: list[Unit] = []
        # Every broadcast address, plus the last known address of each unit.
        # A unit whose lease moved answers on neither the old address nor a
        # broadcast that does not reach its segment, and asking both is the
        # difference between "it moved" and "it is broken".
        targets = [broadcast] if broadcast else list(BROADCASTS)
        targets += [u.host for u in self.units.values() if u.host]
        try:
            for target in targets:
                try:
                    s.sendto(json.dumps({"t": "scan"}).encode(), (target, PORT))
                except OSError:
                    continue
            while True:
                try:
                    data, addr = s.recvfrom(65535)
                except TimeoutError:
                    break
                raw = json.loads(data.decode())
                gcm = raw.get("tag") is not None
                pack = (decrypt_gcm(raw["pack"], raw["tag"]) if gcm
                        else decrypt(raw["pack"]))
                found.append(Unit(mac=pack.get("mac") or raw.get("cid", ""),
                                  host=addr[0], name=pack.get("name", ""), gcm=gcm))
        finally:
            s.close()
        return found

    def bind(self, unit: Unit) -> Unit:
        """Ask a unit for its own key. Additive — the vendor app keeps working."""
        body = {"mac": unit.mac, "t": "bind", "uid": 0}
        if unit.gcm:
            pack, tag = encrypt_gcm(body)
            packet = {"cid": "app", "i": 1, "t": "pack", "uid": 0, "pack": pack, "tag": tag}
        else:
            packet = {"cid": "app", "i": 1, "t": "pack", "uid": 0, "pack": encrypt(body)}
        reply = self._send(unit.host, packet)
        pack = (decrypt_gcm(reply["pack"], reply["tag"]) if unit.gcm
                else decrypt(reply["pack"]))
        if not pack.get("key"):
            raise ACError(f"{unit.mac} refused to bind")
        unit.key = pack["key"]
        self.units[unit.mac] = unit
        return unit

    def set(self, mac: str, **changes) -> dict:
        """Apply settings. Unknown names are rejected rather than silently dropped."""
        unit = self.units.get(mac)
        if unit is None or not unit.key:
            raise ACError(f"{mac} is not bound")
        opt, val = [], []
        for name, value in changes.items():
            if name not in FIELDS:
                raise ACError(f"unknown setting {name!r}; known: {', '.join(sorted(FIELDS))}")
            if name == "mode":
                value = MODES.get(str(value).lower(), value)
            opt.append(FIELDS[name])
            val.append(int(value))
        if not opt:
            raise ACError("nothing to set")
        body = {"opt": opt, "p": val, "t": "cmd"}
        key = unit.key.encode()
        packet = {"cid": "app", "i": 0, "t": "pack", "uid": 0,
                  "tcid": mac, "pack": encrypt(body, key)}
        try:
            reply = self._send(unit.host, packet)
        except ACError:
            # The address is a DHCP lease and does move; the MAC is the
            # identity. A unit that stops answering is re-found rather than
            # declared broken — otherwise every lease renewal looks like a
            # dead air conditioner and needs a human to re-pair it.
            if not self.relocate(mac):
                raise
            packet["tcid"] = mac
            reply = self._send(self.units[mac].host, packet)
        return decrypt(reply["pack"], key)

    def relocate(self, mac: str) -> bool:
        """Find a known unit at its new address. True if it moved and was found.

        The bind key survives the move, so this is a lookup rather than a
        re-pair: nothing the vendor app holds is disturbed.
        """
        unit = self.units.get(mac)
        if unit is None:
            return False
        for found in self.scan():
            if found.mac == mac and found.host != unit.host:
                unit.host = found.host
                self.on_change()
                return True
        return False

    #: Set by the runtime to persist units after a bind or a relocation.
    #: A no-op default keeps a bare Gree usable in a test.
    on_change: Callable[[], None] = lambda: None


def _selftest() -> None:
    # Round-trips only; a real unit needs the LAN.
    payload = {"t": "bind", "mac": "c039371028bd", "uid": 0}
    assert decrypt(encrypt(payload)) == payload
    key = b"294z2l4IAryTaJ9R"
    assert decrypt(encrypt(payload, key), key) == payload
    blob, tag = encrypt_gcm(payload)
    assert decrypt_gcm(blob, tag) == payload

    # Padding must survive every length boundary.
    for n in (0, 1, 15, 16, 17, 31, 32):
        body = {"x": "y" * n}
        assert decrypt(encrypt(body)) == body, n

    g = Gree()
    try:
        g.set("nope", power=1)
        raise AssertionError("should have raised")
    except ACError as e:
        assert "not bound" in str(e)

    g.units["m"] = Unit(mac="m", host="127.0.0.1", key="0123456789abcdef")
    try:
        g.set("m", nonsense=1)
        raise AssertionError("should have raised")
    except ACError as e:
        assert "unknown setting" in str(e), str(e)
    try:
        g.set("m")
        raise AssertionError("should have raised")
    except ACError as e:
        assert "nothing to set" in str(e)

    assert Unit(mac="m", host="h").as_dict()["bound"] is False
    print("ac selftest ok")


if __name__ == "__main__":
    _selftest()
