"""client/sigcheck.py — Ed25519 signature check for app updates (2026-10-15).

Why: an update is code that runs on every tester's PC. Until 0.1.18 the app
trusted whatever our server's latest.json said (plus the zip's sha256 from
that same file), so someone who broke into the server could ship a fake
update to everyone. Now latest.json carries an Ed25519 signature made with a
key that never leaves the developer's PC (tools/release_signing.py), and the
updater refuses any release whose signature doesn't verify against
RELEASE_PUBLIC_KEY (client/updater.py).

Pure Python on purpose: the app's runtime has no crypto library and adding
one grows the installer; verification runs once per update check (~20 ms).
Written from RFC 8032 section 5.1 (cofactorless verification, as the RFC's
reference code); eval/release_signing_harness.py checks it against the RFC's
test vectors and against the `cryptography` package.
"""
from __future__ import annotations

import hashlib
import json

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493  # the group order L
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a: tuple, b: tuple) -> tuple:
    # extended coordinates (X, Y, Z, T), RFC 8032 5.1.4
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % _P
    bb = (y1 + x1) * (y2 + x2) % _P
    cc = t1 * 2 * _D * t2 % _P
    dd = z1 * 2 * z2 % _P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: tuple) -> tuple:
    q = (0, 1, 1, 0)  # neutral element
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(a: tuple, b: tuple) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(s: bytes) -> tuple | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _compress(p: tuple) -> bytes:
    x, y, z, _ = p
    zi = _inv(z)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


_GY = 4 * _inv(5) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def _h(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid Ed25519 signature of `message` by `public_key`."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r_bytes = signature[:32]
    r = _decompress(r_bytes)
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    k = _h(r_bytes, public_key, message) % _Q
    return _equal(_mul(s, _G), _add(r, _mul(k, a)))


def public_key_from_seed(seed: bytes) -> bytes:
    a, _prefix = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """Ed25519 signature (RFC 8032 5.1.6). Used by tools/release_signing.py on
    the developer's PC - the app itself only ever verifies."""
    a, prefix = _expand(seed)
    public = _compress(_mul(a, _G))
    r = _h(prefix, message) % _Q
    r_bytes = _compress(_mul(r, _G))
    k = _h(r_bytes, public, message) % _Q
    return r_bytes + int.to_bytes((r + k * a) % _Q, 32, "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def canonical(release: dict) -> bytes:
    """The bytes a release's signature covers: every field except
    "signature", keys sorted, no spaces, ASCII - the same on the developer's
    PC and in the app, whatever the server's JSON encoder does to spacing."""
    body = {k: v for k, v in release.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def release_is_signed(release: dict, public_key_hex: str) -> bool:
    """True if `release` (a parsed latest.json) carries a valid signature by
    the key `public_key_hex`. Never raises."""
    try:
        sig = bytes.fromhex(str(release.get("signature", "")))
        return verify(bytes.fromhex(public_key_hex), canonical(release), sig)
    except (ValueError, TypeError):
        return False
