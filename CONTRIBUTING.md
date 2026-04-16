# Contributing to PolyAgent

感谢你有兴趣贡献 PolyAgent！以下是参与项目的指南。

## 开发环境

```bash
git clone https://github.com/your-username/polyagent.git
cd polyagent
uv sync
playwright install chromium
cp .env.example .env   # 填入你的 API Key
```

## 项目结构

```
agents/    Agent 定义（LLM 调用逻辑）
graphs/    LangGraph 工作流（节点 + 路由）
tools/     工具集（浏览器、文件、搜索等）
prompts/   系统提示词（.txt 文件）
cli/       CLI 入口与报告生成
user/      运行时产物（测试文件、报告，gitignore）
```

## 贡献方式

### 报告 Bug
在 [Issues](https://github.com/your-username/polyagent/issues) 中新建 issue，请包含：
- 复现步骤
- 期望行为 vs 实际行为
- Python 版本 / 操作系统

### 提交 PR

1. Fork 本仓库，基于 `main` 创建功能分支
   ```bash
   git checkout -b feat/your-feature
   ```
2. 保持单次 PR 聚焦一个功能或修复
3. 修改提示词（`prompts/`）时，请附上前后对比示例
4. 新增 Agent 时，请同步更新 `graphs/workflow.py` 的路由逻辑

### 新增 Agent 流程

1. 在 `agents/` 下新建文件，参照 `agents/chat.py` 结构
2. 在 `prompts/system_prompts/` 下新增对应 `.txt` 提示词
3. 在 `graphs/workflow.py` 中添加节点和路由条件
4. 在 `cli/runner.py` 中更新节点显示标签（如需要）

## 代码风格

- Python 3.13+，遵循 PEP 8
- 函数和类使用中文注释（面向中文用户的项目）
- 不在代码中硬编码 API Key 或内网地址

## 提交信息规范

```
feat: 新增 xxx Agent
fix: 修复 swagger 解析 YAML 失败的问题
refactor: 拆分 main.py 为 cli/ 模块
docs: 更新 README 安装说明
```
