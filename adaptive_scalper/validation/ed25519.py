"""Ed25519 (RFC 8032) -- the reference algorithm of RFC 8032 section 6.

Why a pure-Python implementation: the runtime's virtual environment is shared
with the deployed DEMO runtime and has no `cryptography` / PyNaCl package;
installing one there would change the deployed environment. This module is
the RFC's own reference code (standard library `hashlib` only), checked
against the RFC 8032 section 7.1 test vectors in
tests/test_validation_ed25519.py.

Security properties relied on:
- VERIFY needs only the 32-byte public key. The trading runtime holds the
  public key and nothing else, so nothing available to it can produce a
  valid signature.
- SIGN needs the 32-byte private seed. Only the offline validation issuer may
  call `sign` (AST-enforced: no other production module calls it).

It is not constant-time. That matters for SIGNING (secret-dependent timing),
which must happen offline on the issuer's machine -- never in the runtime.
Verification handles only public data.
"""

from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % _Q


def _point_add(P, Q):
    a = (P[1] - P[0]) * (Q[1] - Q[0]) % _P
    b = (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    c = 2 * P[3] * Q[3] * _D % _P
    d = 2 * P[2] * Q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f, g * h, f * g, e * h)


def _point_mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_equal(P, Q) -> bool:
    if (P[0] * Q[2] - Q[0] * P[2]) % _P != 0:
        return False
    return (P[1] * Q[2] - Q[1] * P[2]) % _P == 0


def _recover_x(y: int, sign: int):
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


_G_Y = 4 * _inv(5) % _P
_G_X = _recover_x(_G_Y, 0)
_G = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _compress(P) -> bytes:
    zinv = _inv(P[2])
    x, y = P[0] * zinv % _P, P[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("an Ed25519 private seed is 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key of a 32-byte private seed (issuer side)."""
    a, _ = _expand(seed)
    return _compress(_point_mul(a, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """64-byte signature. OFFLINE ISSUER ONLY (see module docstring)."""
    a, prefix = _expand(seed)
    pub = _compress(_point_mul(a, _G))
    r = _sha512_modq(prefix + message)
    rs = _compress(_point_mul(r, _G))
    h = _sha512_modq(rs + pub + message)
    s = (r + h * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid signature by the holder of `public`'s seed.
    Malformed input of any kind returns False (never raises)."""
    if not isinstance(public, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(bytes(public))
    if A is None:
        return False
    rs = bytes(signature[:32])
    R = _decompress(rs)
    if R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _sha512_modq(rs + bytes(public) + message)
    return _point_equal(_point_mul(s, _G), _point_add(R, _point_mul(h, A)))
