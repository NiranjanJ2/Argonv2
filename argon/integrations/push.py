"""APNs push to his phone.

Two things cost real time before and are worth stating in the code.

**An APNs auth key scoped to Development at creation can never be widened.**
Production then fails on *auth* before it ever looks at the device token, which
returns ``BadEnvironmentKeyInToken``; meanwhile sandbox rejects a production
token with ``BadDeviceToken``.  Together those look exactly like a dead
registration when the token was fine all along.  ``probe`` exists to tell the
two apart: it sends a silent push to a syntactically valid but fake token and
reports what each environment says about *auth*, without touching the real one.

**A dead-token reason must be believed only from the right environment.**
``BadDeviceToken`` from the environment the app was not built for means
nothing, so ``send`` reports the reason and leaves deleting the registration to
the caller, which knows which build is installed.

ponytail: httpx for HTTP/2, which APNs requires and urllib cannot speak.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from argon import config

PRODUCTION_HOST = "https://api.push.apple.com"
SANDBOX_HOST = "https://api.sandbox.push.apple.com"

#: Apple rejects a token older than an hour and rate-limits minting under 20
#: minutes, so refresh comfortably between the two.
JWT_TTL_S = 45 * 60

#: Reasons that mean the registration is genuinely gone — but only when the
#: environment matches the installed build. See the module docstring.
DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


class PushError(RuntimeError):
    pass


@dataclass
class Result:
    ok: bool
    status: int
    reason: str = ""

    @property
    def dead_token(self) -> bool:
        return self.reason in DEAD_TOKEN_REASONS


def _b64(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class _Signer:
    """Caches the provider JWT; minting one per push gets you rate-limited."""

    def __init__(self, team_id: str, key_id: str, key_pem: bytes) -> None:
        self.team_id, self.key_id, self.key_pem = team_id, key_id, key_pem
        self._token, self._minted = "", 0.0

    def token(self) -> str:
        if self._token and time.time() - self._minted < JWT_TTL_S:
            return self._token
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        key = serialization.load_pem_private_key(self.key_pem, password=None)
        now = int(time.time())
        header = _b64(json.dumps({"alg": "ES256", "kid": self.key_id}).encode())
        claims = _b64(json.dumps({"iss": self.team_id, "iat": now}).encode())
        signing_input = f"{header}.{claims}".encode()
        der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # JWS wants R||S, not DER
        self._token = f"{header}.{claims}.{_b64(raw)}"
        self._minted = time.time()
        return self._token


class Push:
    def __init__(self, cfg: config.Apns) -> None:
        self.cfg = cfg
        self._signer: _Signer | None = None

    def _key(self) -> _Signer:
        if self._signer is None:
            p = config.path("apns", f"AuthKey_{self.cfg.key_id}.p8")
            if not p.exists():
                raise PushError(f"APNs key missing at {p}")
            self._signer = _Signer(self.cfg.team_id, self.cfg.key_id, p.read_bytes())
        return self._signer

    def _post(self, host: str, token: str, payload: dict, push_type: str,
              priority: str) -> Result:
        import httpx

        headers = {
            "authorization": f"bearer {self._key().token()}",
            "apns-topic": self.cfg.bundle_id,
            "apns-push-type": push_type,
            "apns-priority": priority,
        }
        try:
            with httpx.Client(http2=True, timeout=10.0) as client:
                r = client.post(f"{host}/3/device/{token}", json=payload, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise PushError(f"APNs unreachable: {e}") from e
        if r.status_code == 200:
            return Result(ok=True, status=200)
        reason = ""
        try:
            reason = r.json().get("reason", "")
        except Exception:  # noqa: BLE001
            reason = r.text[:120]
        return Result(ok=False, status=r.status_code, reason=reason)

    def alert(self, token: str, body: str, title: str = "Argon") -> Result:
        """A visible notification."""
        if not self.cfg.enabled:
            raise PushError("APNs is disabled in config")
        host = PRODUCTION_HOST if self.cfg.production else SANDBOX_HOST
        payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
        return self._post(host, token, payload, "alert", "10")

    def silent(self, token: str, **data: object) -> Result:
        """A background wake — tells the app to come and fetch state."""
        if not self.cfg.enabled:
            raise PushError("APNs is disabled in config")
        host = PRODUCTION_HOST if self.cfg.production else SANDBOX_HOST
        payload = {"aps": {"content-available": 1}, **data}
        return self._post(host, token, payload, "background", "5")

    def probe(self) -> str:
        """Test auth in both environments without touching his registration.

        Sends a silent push to a fake but well-formed token. ``BadDeviceToken``
        back means auth and topic were accepted and only the token was wrong —
        which is success, for this purpose.
        """
        fake = "0" * 64
        out = []
        for name, host in (("production", PRODUCTION_HOST), ("sandbox", SANDBOX_HOST)):
            try:
                r = self._post(host, fake, {"aps": {"content-available": 1}},
                               "background", "5")
                verdict = ("auth ok (token rejected, as expected)"
                           if r.reason == "BadDeviceToken" else f"{r.status} {r.reason}")
            except PushError as e:
                verdict = str(e)
            out.append(f"{name}: {verdict}")
        return "\n".join(out)


def _selftest() -> None:
    import os
    import tempfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp

        assert Result(False, 410, "Unregistered").dead_token
        assert Result(False, 400, "BadDeviceToken").dead_token
        assert not Result(False, 403, "BadEnvironmentKeyInToken").dead_token, \
            "a key-scope failure is not a dead token"
        assert not Result(True, 200).dead_token

        # A disabled config refuses rather than silently doing nothing.
        p = Push(config.Apns(enabled=False))
        try:
            p.alert("0" * 64, "hi")
            raise AssertionError("should have raised")
        except PushError as e:
            assert "disabled" in str(e)

        # Missing key file says where it should be.
        p = Push(config.Apns(enabled=True, team_id="T", key_id="K"))
        try:
            p.alert("0" * 64, "hi")
            raise AssertionError("should have raised")
        except PushError as e:
            assert "AuthKey_K.p8" in str(e), str(e)

        # The JWT is well formed, ES256, and cached between calls.
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        signer = _Signer("TEAM", "KEY", pem)
        tok = signer.token()
        assert tok.count(".") == 2 and signer.token() is tok, "JWT must be cached"
        import base64
        head = json.loads(base64.urlsafe_b64decode(tok.split(".")[0] + "=="))
        assert head == {"alg": "ES256", "kid": "KEY"}, head
        sig = base64.urlsafe_b64decode(tok.split(".")[2] + "==")
        assert len(sig) == 64, "JWS needs raw R||S, not DER"

        del os.environ["ARGON_HOME"]
    print("push selftest ok")


if __name__ == "__main__":
    _selftest()
