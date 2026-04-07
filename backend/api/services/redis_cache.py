import json
import os
import logging
from typing import Any, Optional
import redis

class RedisCache:
    _instance: Optional[redis.Redis] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
        """Returns a singleton Redis client."""
        if cls._instance is None:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            try:
                cls._instance = redis.from_url(redis_url, decode_responses=True)
                # Test connection
                cls._instance.ping()
            except Exception as e:
                logging.error(f"Failed to connect to Redis at {redis_url}: {e}")
                # We return the instance anyway, but it might fail on operations.
                # In a real app, we'd handle this more gracefully.
                if cls._instance is None:
                    # Create a dummy client that fails predictably or a real one that's just disconnected
                    cls._instance = redis.from_url(redis_url, decode_responses=True)
        return cls._instance

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        """Retrieves and deserializes a JSON value from Redis."""
        try:
            client = cls.get_client()
            data = client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logging.error(f"Redis GET failed for key {key}: {e}")
        return None

    @classmethod
    def set(cls, key: str, value: Any, ttl: int = 3600) -> bool:
        """Serializes and stores a value in Redis with a TTL."""
        try:
            client = cls.get_client()
            client.set(key, json.dumps(value), ex=ttl)
            return True
        except Exception as e:
            logging.error(f"Redis SET failed for key {key}: {e}")
        return False

    @classmethod
    def delete(cls, key: str) -> bool:
        """Deletes a key from Redis."""
        try:
            client = cls.get_client()
            client.delete(key)
            return True
        except Exception as e:
            logging.error(f"Redis DELETE failed for key {key}: {e}")
        return False
