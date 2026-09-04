# ADR-0001: 浏览器登录会话（HttpOnly Cookie + CSRF）

- 状态：**已批准（设计）**；6B 代码迁移待 HTTPS 统一入口就绪后实施
- 日期：2026-09-04
- 决策背景：`localStorage` 保存 JWT 增加 XSS 后果；改为浏览器不可读的登录 Cookie，
  同时必须解决 CSRF。

## 结论

浏览器使用**同源 HTTPS、HttpOnly / Secure、SameSite=Lax** 的 host-only 登录 Cookie，
Path 覆盖 `/user/` 与 `/api/v1/`。删除 Cookie 的名称、Path/Domain 必须与签发一致。
本地 HTTP 开发使用显式独立配置（`SameSite=Lax` + 显式允许的本地 Origin）。

## 设计要点

1. **不与 refresh-token 数据模型重做混在一起**：第一步保持现有 token 生命周期与撤销
   语义（见 `docs/JWT_CONTRACT.md`），仅改为经 Cookie 传输。
2. **CSRF 采用与会话绑定的签名双提交（Signed Double-Submit）**，并校验可信 Origin；
   CSRF token 的获取/旋转/过期/校验在两服务使用同一契约与测试向量。
   不自行发明加密算法，不把 SameSite 当作全部 CSRF 防护。
   - 若选 Django 内建 CSRF：必须明确 FastAPI 的等价验证边界（不能只在 Django 加中间件）。
3. **登录前也要防登录 CSRF**；登录后旋转 CSRF 状态；写操作以及刷新/退出均验证，
   缺失或错误返回 403。**CSRF token 可以让前端读取，认证 JWT 不可以。**
4. **Bearer 兼容窗口按版本/日期明确限定**；同时携带两种不一致身份凭据时拒绝
   （不能任选其一）；Cookie → localStorage 不设自动回退。窗口结束后关闭浏览器旧路径，
   通知用户可能需要重新登录。

## 改动顺序（6B，实施时执行）

1. Django 登录/刷新/资料更新/改密码统一签发 Cookie；退出删除 Cookie 并撤销 token；
   Cookie 模式响应不再把 JWT 返回给浏览器 JS。
2. FastAPI 在统一认证入口提取 Cookie（或兼容 Bearer），继续执行签名、过期、用户身份
   与撤销检查；CSRF 验证必须在 Cookie 写请求执行前生效。
3. 前端 `http.js`、`sseClient.js`、上传 fetch、`useAuthImage.js`、session/model 调用
   统一凭据策略，覆盖绕过 Axios 的所有路径。
4. `user store` 不再持久化 token；路由守卫不再检查本地 token，启动时通过
   `/user/detail/` 恢复会话，区分“加载中/已登录/未登录/服务不可用”。
5. 清除旧 `jwt_token` 与 Pinia 持久化中的 token 字段；部署切换期间记录兼容 Bearer
   使用量，结束窗口后关闭旧路径。

## 前提（本 ADR 批准前不实施 6B）

- 真实 HTTPS 统一入口（生产 TLS 终止点、可信代理配置）就绪。
- 批次 1 认证状态统一、批次 3 真实全栈测试已落地（已完成）。
- 前端 `clearAuth`/`authEpoch` 代次机制（批次 1B）作为切换期间的兜底保留。

## 验收（实施后）

- localStorage/sessionStorage 及持久化 store 中无认证 JWT；刷新页面能恢复登录。
- 登录、上传、私有图片、SSE、改密码、退出全部工作。
- 跨站写请求被两服务拒绝；合法同源请求成功。
- 401 与认证服务 503 界面语义不同；Django 使用强制 CSRF 校验的测试客户端
  （不依赖测试默认豁免）。
