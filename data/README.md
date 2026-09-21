# 运行数据库目录

SQLite 文件 `pottery.db` 由服务首次启动时自动创建并播种固定 fixture。
该文件是运行态数据，不随交付提交（见 `.gitignore` 的 `data/` 规则可自行追加）。

- 清空并重播 fixture：`POST /api/admin/reset`（页面“清空并重置 fixture”按钮）
- 全量导出：`GET /api/export`
- 导入复核：`POST /api/import`（body 为导出 JSON 的 `{"data": ...}` 包裹）
