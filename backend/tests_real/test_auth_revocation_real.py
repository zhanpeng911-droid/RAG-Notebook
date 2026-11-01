"""Real Redis contract: a token accepted once must be rejected after revocation."""
import os
import uuid

import pytest
import redis.asyncio as redis
from fastapi import HTTPException

from app.utils import auth_utils


@pytest.mark.asyncio
async def test_revocation_after_accept_is_visible_on_next_check():
    url = os.environ.get("JWT_BLACKLIST_REDIS_URL") or ""
    if not url:
        pytest.fail("JWT_BLACKLIST_REDIS_URL is required for the real revocation test")
    jti = str(uuid.uuid4())
    payload = {"jti": jti, "user_id": str(uuid.uuid4())}
    auth_utils._blacklist_cache.clear()
    auth_utils._blacklist_cache_url = None
    client = redis.from_url(url, decode_responses=True)
    try:
        await auth_utils._reject_if_blacklisted(payload)
        await client.set(f"blacklist:{jti}", "1", ex=60)
        with pytest.raises(HTTPException) as exc:
            await auth_utils._reject_if_blacklisted(payload)
        assert exc.value.status_code == 401
    finally:
        await client.delete(f"blacklist:{jti}")
        await client.aclose()
        auth_utils._blacklist_cache.clear()
        auth_utils._blacklist_cache_url = None
