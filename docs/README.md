# Notebook 文档索引

> 公开仓库只保留产品使用、接口与架构文档。
> 内部任务书、整改计划、验收方案、性能证据与测试数据不随公开仓库发布。

## 公开文档

| 文档 | 说明 |
|---|---|
| [API.md](API.md) | 后端 API 路由与鉴权说明 |
| [JWT_CONTRACT.md](JWT_CONTRACT.md) | DjangoUserService ↔ FastAPI 的 JWT 契约 |
| [adr/0001-browser-session.md](adr/0001-browser-session.md) | 浏览器会话架构决策 |
| [screenshots/](screenshots/) | README 界面截图 |
| [backend/tools/backend_acceptance/README.md](../backend/tools/backend_acceptance/README.md) | 后端验收工具使用说明（隔离环境） |

## 文档约定

1. 仓库根目录只保留 `README.md`。
2. 面向使用者的 API、JWT、架构决策与截图放在 `docs/`。
3. 内部任务书、整改计划、验收报告与性能证据保留在本地，不提交到公开仓库。
4. 新增公开文档时同步更新本索引和根 README 的文档导航。
