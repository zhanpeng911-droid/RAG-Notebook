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
| Redis/Cache key | `blacklist:{jti}`（Django 新写入；旧版 `:1:blacklist:{jti}` 过渡期兼容读取） |
| Django cache 前缀 | 可能为 `:1:blacklist:{jti}`（django-redis 默认） |
| FastAPI 检查 | 同时查 `blacklist:{jti}` 与 `:1:blacklist:{jti}` |
| TTL | token 剩余有效期（秒） |
| 生产共享 Redis | `REDIS_CACHE_URL` 必填，**禁止自动降级 LocMemCache**（settings.py 强制） |
| 撤销失败语义 | **失败关闭**：Redis 不可用时认证相关请求返回 503，不得静默放行已撤销 token，也不得伪报"已撤销成功" |

## 时钟偏差

- FastAPI `jwt.decode(..., leeway=30)`：允许 ±30 秒

## 校验失败语义

| 情况 | HTTP |
|------|------|
| 签名无效 / 无法解码 | 401 |
| 过期 | 401 |
| 在黑名单 | 401 `Token has been revoked` |
| 黑名单 Redis 不可用且检查开启 | 503（两服务一致，失败关闭） |
| payload 无 `user_id` | 401 |
| 注销/刷新/改密码时撤销写入失败 | 503，不得返回"成功" |

## 集成验证

- 双服务共享真实测试 Redis 的撤销契约：`backend/tests/test_jwt_contract.py`
  （需要 `REDIS_TEST_URL` 环境变量指向共享测试 Redis，未配置时跳过；CI 集成 job 注入）。
  覆盖：Django 写入的 `blacklist:{jti}` 与旧版 `:1:blacklist:{jti}` 都能被 FastAPI 撤销检查拒绝。
- FastAPI 撤销失败关闭：`backend/tests/test_auth_utils.py`。
- Django 撤销失败关闭与视图 503：`DjangoUserService/apps/user/tests.py`（RevocationFailClosedTests）。
