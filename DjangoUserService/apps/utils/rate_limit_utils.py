from functools import wraps

from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response


def rate_limit(limit: int = 1, window: int = 60, scope: str = "default") -> callable:
    """
    限流装饰器
    :param limit: 时间窗口内的最大请求数
    :param window: 时间窗口大小（秒）
    :return: 装饰器
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            # 获取客户端IP
            client_ip = request.META.get('REMOTE_ADDR')
            if not client_ip:
                client_ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or 'unknown'

            # 生成限流键（scope 区分不同接口，避免共享桶互相干扰）
            key = f"rate_limit:{scope}:{client_ip}"

            # 原子计数：先 incr；key 恰好过期时 incr 抛 ValueError，回退 set
            try:
                current = cache.incr(key)
                if current == 1:
                    cache.expire(key, window)
            except ValueError:
                cache.set(key, 1, window)
                current = 1

            if current > limit:
                return Response(
                    {"detail": "请求过于频繁，请稍后再试"},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            return func(self, request, *args, **kwargs)
        return wrapper
    return decorator
