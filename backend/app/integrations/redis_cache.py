import json
import redis.asyncio as redis
from app.config import REDIS_URL

# Global Redis connection pool
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# In-memory fallback
_fallback_cache = {}

async def get_cache(key: str):
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        print(f"Redis get error: {e}, falling back to memory")
        data = _fallback_cache.get(key)
        if data:
            return json.loads(data)
    return None

async def set_cache(key: str, value: dict | list | str | int | float, expire_seconds: int = 300):
    try:
        await redis_client.setex(key, expire_seconds, json.dumps(value))
    except Exception as e:
        print(f"Redis set error: {e}, falling back to memory")
        _fallback_cache[key] = json.dumps(value)
