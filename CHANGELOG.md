# Changelog

所有版本变更记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范。

## [0.1.0] - 2026-04-16

### 新增
- **6 条工作流路由**：`code` / `swagger` / `design` / `ui` / `test` / `chat`
- **design 链路**：4 节点顺序流水线（产品方案 → SQL DDL → 后端代码 → 前端代码）
- **swagger 链路**：自动解析 OpenAPI 文档，生成 pytest CRUD 链式测试用例，含 MD 测试报告
- **ui 链路**：基于 Playwright 的 UI 自动化，支持子任务拆解、登录态持久化、网络请求捕获
- **code 链路**：architect 规划 → coder 实现 → tester 验收，失败自动修复循环（最多 3 次）
- **CLI 工具**：`polyagent` 命令，支持 `init`（配置向导）/ `run`（单次任务）/ 交互模式
- **多 Provider 支持**：OpenAI / SiliconFlow / DeepSeek / Ollama / Groq / Together / vLLM
- **27 个浏览器工具**：涵盖导航、表单、截图、iframe、状态保存等
- **独立测试 venv**：`user/.venv` 隔离测试依赖，不污染主项目
