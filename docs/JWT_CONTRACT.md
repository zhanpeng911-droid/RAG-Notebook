# JWT 契约（DjangoUserService ↔ FastAPI backend）
>
> 两边必须使用同一密钥与算法，否则鉴权必然漂移。

## 密钥

| 服务 | 环境变量 | 代码读取 |
|------|----------|----------|
| Django | `JWT_SECRET_KEY` | `settings.SECRET_KEY` |
| FastAPI | `SECRET_KEY` | `AppSettings.SECRET_KEY` |

**要求**：两值必须完全一致。Docker 根目录 `.env` 的 `JWT_SECRET_KEY` 应同时注入两边。

## 算法

- `HS256`（固定）

## Access Token Claims

| Claim | 类型 | 说明 |
|-------|------|------|
| `user_id` | string (UUID) | 用户主键（Django `User.uuid`） |
| `username` | string | 用户名 |
| `email` | string | 可选 |
| `exp` | int (unix) | 过期时间，**必填** |
| `iat` | int (unix) | 签发时间 |
| `jti` | string (uuid4) | 唯一 ID，用于黑名单 |

默认有效期：24 小时（Django `JWTTokenGenerator.generate_token`）。

## 传输

- HTTP Header：`Authorization: Bearer <token>`
- 前端当前存储：`localStorage.jwt_token`（XSS 风险；已加 CSP 短期缓解，中期建议 HttpOnly Cookie）

## 黑名单（登出/撤销）

| 项 | 值 |
|----|----|
| Redis/Cache key | `blacklist:{jti}` |
| Django cache 前缀 | 可能为 `:1:blacklist:{jti}`（django-redis 默认） |
| FastAPI 检查 | 同时查 `blacklist:{jti}` 与 `:1:blacklist:{jti}` |
| TTL | token 剩余有效期（秒） |

## 时钟偏差

- FastAPI `jwt.decode(..., leeway=30)`：允许 ±30 秒

## 校验失败语义

| 情况 | HTTP |
|------|------|
| 签名无效 / 无法解码 | 401 |
| 过期 | 401 |
| 在黑名单 | 401 `Token has been revoked` |
| 黑名单 Redis 不可用且检查开启 | 503 |
| payload 无 `user_id` | 401 |

## 集成验证

见 `backend/tests/test_jwt_contract.py`：用与 Django 相同的 payload/算法签发，FastAPI `decode_django_jwt` 必须成功解析。
