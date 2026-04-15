# PolyAgent

一个基于 **LangGraph** 构建的多 Agent 协作框架，支持产品设计、代码生成、接口测试、UI 自动化等场景。

## 功能链路

| 链路 | 触发关键词示例 | 说明 |
|------|--------------|------|
| **design** | "设计一个抽奖系统"、"出订单模块的表结构" | 产品方案 → 技术架构 → 数据库设计 → SQL DDL → 服务端代码（FastAPI / Spring Boot / Gin 等）→ 前端代码（Vue3 / React 等） |
| **code** | "帮我写一个爬虫"、"创建一个 REST API" | architect 拆解任务 → coder 逐步实现 → tester 验收，支持自动修复循环 |
| **chat** | "解释这段代码"、"Redis 和 Memcached 的区别" | 通用对话，支持联网搜索（Tavily） |
| **test** | "测试这个文件有没有 Bug" | 对已有代码运行测试，自动定位并修复问题 |
| **swagger** | "根据这个 Swagger 地址生成测试用例" | 解析 Swagger / OpenAPI 文档或接口描述文件，自动生成 pytest 测试用例 |
| **ui** | "打开 example.com 测试登录功能" | planner 拆解子任务 → tester 控制 Playwright 执行，支持网络请求捕获、语义化表单填写、登录态持久化、测试报告输出 |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/polyagent.git
cd polyagent
```

### 2. 安装依赖

推荐使用 [uv](https://github.com/astral-sh/uv)：

```bash
uv sync
playwright install chromium
```

或使用 pip：

```bash
pip install -e .
playwright install chromium
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入你的配置：

```env
# 必填：LLM 配置（支持任意 OpenAI 兼容服务）
API_KEY=your-api-key-here
API_BASE=https://api.siliconflow.cn/v1
MODEL=Pro/zai-org/GLM-4.7

# 可选：Tavily 搜索（chat 联网功能需要，免费：https://tavily.com）
TAVILY_API_KEY=your-tavily-key-here
```

支持任意 OpenAI 兼容服务：

| 服务商 | API_BASE | MODEL 示例 |
|--------|----------|-----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `Pro/zai-org/GLM-4.7` |
| Azure OpenAI | `https://<resource>.openai.azure.com/` | `gpt-4o` |
| Ollama（本地）| `http://localhost:11434/v1` | `llama3.2` |

### 4. 运行

```bash
# 直接运行
uv run python main.py

# 安装为 CLI 工具
uv tool install .
polyagent
```

## 使用示例

```
请输入: 设计一个电商抽奖活动系统，用 FastAPI + Vue3
```

```
请输入: 帮我写一个 Python 爬虫，爬取豆瓣 Top250 电影列表
```

```
请输入: 测试 https://example.com 的登录功能，账号 admin 密码 123456
```

```
请输入: 读取 api_spec.txt，生成对应的 pytest 接口测试用例
```

## 项目结构

```
polyagent/
├── main.py                    # 入口：Rich 终端 UI + LangGraph 流式输出
├── agents/
│   ├── llm.py                 # LLM 配置（从 .env 读取，支持切换服务商）
│   ├── designer.py            # 设计链路：4 阶段独立节点
│   ├── architect.py           # 代码任务规划
│   ├── coder.py               # 代码生成
│   ├── tester.py              # 测试验收
│   ├── chat.py                # 通用对话
│   ├── swagger_agent.py       # 接口文档解析
│   ├── ui_planner.py          # UI 测试任务规划
│   └── ui_tester.py           # UI 测试执行
├── graphs/
│   ├── state.py               # 共享状态（TypedDict）
│   └── workflow.py            # LangGraph 拓扑（节点 + 路由）
├── tools/
│   ├── browser.py             # Playwright 工具集（20+ 浏览器操作）
│   ├── file_ops.py            # 文件读写
│   ├── terminal.py            # 终端命令执行
│   ├── web_search.py          # 联网搜索（Tavily）
│   ├── swagger_parser.py      # Swagger 文档获取
│   └── file_reader.py         # 接口描述文件读取
├── prompts/system_prompts/    # 各 Agent 系统提示词
│   ├── designer_plan.txt      # 阶段一-三：产品方案 + 架构 + DB 设计
│   ├── designer_sql.txt       # 阶段四：SQL DDL
│   ├── designer_backend.txt   # 阶段五：服务端代码
│   └── designer_frontend.txt  # 阶段六：前端代码
├── user/
│   ├── design/                # 设计产物输出（.sql / .md / 代码文件）
│   ├── session/               # 浏览器登录态（gitignore）
│   └── reports/               # UI 测试报告（gitignore）
├── .env.example               # 环境变量模板
└── pyproject.toml
```

## Design 链路详解

design 链路采用 **4 节点顺序流水线**，每个节点专注一个阶段，避免单次输出过长导致截断：

```
designer_plan  →  designer_sql  →  designer_backend  →  designer_frontend
  阶段一-三          阶段四             阶段五                 阶段六
 产品方案           SQL DDL          服务端代码              前端代码
 技术架构        ↕ write_file      ↕ write_file           ↕ write_file
 DB 设计        (x2 文件)        (按文件逐个保存)         (按文件逐个保存)
↕ web_search
```

**服务端支持框架：** FastAPI · Flask · Gin · Fiber · Spring Boot · Spring Cloud

**前端支持框架：** Vue 3 + Element Plus · Nuxt 3 · React + Ant Design · Next.js

**所有产物保存到：** `user/design/<功能名>/`

## 依赖

- Python >= 3.13
- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent 编排
- [LangChain OpenAI](https://github.com/langchain-ai/langchain) — LLM 调用
- [Playwright](https://playwright.dev/python/) — 浏览器自动化
- [Rich](https://github.com/Textualize/rich) — 终端 UI
- [Tavily](https://tavily.com) — 联网搜索（可选）

## License

MIT
