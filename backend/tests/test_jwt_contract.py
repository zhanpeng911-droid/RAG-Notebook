"""JWT 双服务撤销契约测试（真实共享 Redis）。

需要环境变量 REDIS_TEST_URL 指向可用的共享测试 Redis（CI 集成 job 提供）。
未配置时跳过，避免单测依赖本机 Redis。

验证跨服务撤销一致性的核心契约（见 docs/JWT_CONTRACT.md）：
- Django 侧写入的 `blacklist:{jti}` 键，FastAPI 撤销检查必须拒绝（401）。
- 旧版 `:1:blacklist:{jti}` 键在过渡期内同样被拒绝。
- 键不存在时不拒绝（正常放行）。
"""
import os
import time
import uuid

import pytest
import redis.asyncio as redis

from app.utils import auth_utils

REDIS_TEST_URL = os.getenv("REDIS_TEST_URL", "")

pytestmark = pytest.mark.skipif(
    not REDIS_TEST_URL,
    reason="需要 REDIS_TEST_URL 指向共享测试 Redis",
)


def _make_token(jti: str) -> str:
    from jose import jwt as jose_jwt

    from app.config.validator import get_settings

    settings = get_settings()
    now = int(time.time())
    payload = {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "username": "contract-user",
        "email": "contract@example.com",
        "exp": now + 3600,
        "iat": now,
        "jti": jti,
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


async def _write_and_expect_reject(monkeypatch, key: str) -> None:
    client = redis.from_url(REDIS_TEST_URL, decode_responses=True)
    jti = str(uuid.uuid4())
    try:
        await client.set(key.format(jti=jti), "1", ex=300)
        monkeypatch.setattr(auth_utils, "JWT_BLACKLIST_REDIS_URL", REDIS_TEST_URL)

        payload = auth_utils.decode_django_jwt(_make_token(jti))
        assert payload is not None, "同密钥签发的 token 必须能被 FastAPI 解析"

        with pytest.raises(Exception) as ei:
            await auth_utils._reject_if_blacklisted(payload)
        # 撤销命中必须拒绝（401），基础设施异常也应关闭（503），不得放行
        assert getattr(ei.value, "status_code", None) in (401, 503)
    finally:
        await client.delete(key.format(jti=jti))
        await client.aclose()


@pytest.mark.asyncio
async def test_django_blacklist_key_rejected_by_fastapi(monkeypatch):
    """Django 新写入的 blacklist:{jti} 键，FastAPI 必须拒绝。"""
    await _write_and_expect_reject(monkeypatch, "blacklist:{jti}")


@pytest.mark.asyncio
async def test_legacy_prefixed_key_rejected_by_fastapi(monkeypatch):
    """旧版 :1:blacklist:{jti} 键（Django cache 前缀）过渡期兼容。"""
    await _write_and_expect_reject(monkeypatch, ":1:blacklist:{jti}")


@pytest.mark.asyncio
async def test_not_blacklisted_token_passes(monkeypatch):
    """未撤销的 token 通过撤销检查。"""
    client = redis.from_url(REDIS_TEST_URL, decode_responses=True)
    jti = str(uuid.uuid4())
    try:
        monkeypatch.setattr(auth_utils, "JWT_BLACKLIST_REDIS_URL", REDIS_TEST_URL)
        payload = auth_utils.decode_django_jwt(_make_token(jti))
        await auth_utils._reject_if_blacklisted(payload)  # 不应抛异常
    finally:
        await client.aclose()
