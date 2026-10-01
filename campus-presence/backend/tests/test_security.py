import time

import jwt
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.security import (RateLimiter, create_jwt, decode_jwt, hash_password, hash_secret_key, student_key_for,
                          verify_password, verify_secret_key)

S = Settings(jwt_secret="s" * 32, pepper="pepper", jwt_ttl_s=60)


def test_password_hashing():
    h = hash_password("hunter2", 1000)
    assert h.startswith("pbkdf2_sha256$") and "hunter2" not in h
    assert verify_password("hunter2", h) and not verify_password("hunter3", h)
    assert hash_password("hunter2", 1000) != h               # salted
    assert not verify_password("x", "garbage")


def test_node_key_hashing():
    h = hash_secret_key("node-204-demo")
    assert verify_secret_key("node-204-demo", h) and not verify_secret_key("node-205-demo", h)
    assert not verify_secret_key("x", None)


def test_student_key_is_stable_keyed_and_not_the_id():
    a = student_key_for("STU102", "pepper")
    assert a == student_key_for(" stu102 ", "pepper")        # normalised
    assert a != student_key_for("STU102", "other-pepper")    # keyed: cannot be recomputed without the pepper
    assert "102" not in a and len(a) == 16


def test_jwt_roundtrip_expiry_and_tampering():
    tok = create_jwt(S, sub="faculty", role="faculty")
    p = decode_jwt(S, tok)
    assert (p.sub, p.role) == ("faculty", "faculty")
    expired = jwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) - 5}, S.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        decode_jwt(S, expired)
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        decode_jwt(S, tok[:-3] + "abc")
    forged = jwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) + 60}, "wrong-key", algorithm="HS256")
    with pytest.raises(HTTPException):
        decode_jwt(S, forged)
    badrole = jwt.encode({"sub": "x", "role": "root", "exp": int(time.time()) + 60}, S.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        decode_jwt(S, badrole)
    none_alg = jwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(HTTPException):
        decode_jwt(S, none_alg)


def test_rate_limiter_sliding_window():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("u", "b", 3, 60)
    with pytest.raises(HTTPException) as e:
        rl.check("u", "b", 3, 60)
    assert e.value.status_code == 429 and "Retry-After" in e.value.headers
    rl.check("other", "b", 3, 60)                              # separate identity
    rl.check("u", "different-bucket", 3, 60)
