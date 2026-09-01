"""认证安全模块：密码哈希 + JWT 签名（纯标准库，零第三方依赖）

- 密码：PBKDF2-HMAC-SHA256（盐 + 迭代 20 万次）
- Token：HMAC-SHA256 签名，类 JWT 结构
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# Token 密钥：优先读环境变量，否则每次启动生成（重启后旧 token 失效，可接受）
# 生产环境建议在 .env 里固定 AUTH_SECRET
_SECRET = os.getenv("AUTH_SECRET", "").strip()
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    os.environ["AUTH_SECRET"] = _SECRET

TOKEN_EXPIRE_SECONDS = int(os.getenv("TOKEN_EXPIRE_SECONDS", 7 * 24 * 3600))  # 7 天


# ---------- 密码 ----------
def hash_password(password: str) -> str:
    """生成密码哈希：pbkdf2_sha256$salt$digest"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码与存储哈希是否匹配"""
    try:
        algo, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


# ---------- JWT ----------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(user_id: int) -> str:
    """签发访问令牌"""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + TOKEN_EXPIRE_SECONDS,
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    sig = hmac.new(_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def verify_token(token: str) -> dict | None:
    """校验令牌，成功返回 payload，失败返回 None"""
    try:
        signing_input, sig = token.rsplit(".", 1)
        expected = hmac.new(
            _SECRET.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(sig, _b64url(expected)):
            return None
        _, payload_b64 = signing_input.split(".")
        payload = json.loads(_b64url_decode(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
