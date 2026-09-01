# Harness Engineering

Harness Engineering 是面向真实软件项目的 AI Coding 工程系统，当前在 Powers 仓库内建设。
核心 Skills、运行入口和本地接入工具已完成初步实现，但尚未接入真实业务项目或标记为启用。

## 项目结构

| 目录 | 职责 |
|---|---|
| `docs/` | 系统架构、子系统设计、实施路线和问题记录 |
| `runtime/` | Harness 全局运行与编排入口 |
| `flows/` | 工作流程及其 Skill 编排信息 |
| `skills/` | 按子系统或横向职责分类的 Harness 独立能力源码 |
| `agents/` | 固定职责、Prompt 和权限的专用 Agent 配置 |
| `scripts/` | 项目接入、检查、更新和解除接入工具 |

系统设计以[系统架构](./docs/系统架构.md)为入口，[流程路由](./flows/流程路由.md)维护可选流程，
[Skill 能力设计](./docs/实施方案/Skill%20能力设计.md)维护最小能力拓扑，
[Harness 运行入口](./runtime/HARNESS.md)连接任务交付与系统演进闭环。

`skills/` 第一层直接使用中文编号，第二层才是 Codex-compatible Skill：

| 目标一级目录 | 目标 Skill | 归属说明 |
|---|---|---|
| `1. 意图接入系统/` | `intent`、`cooper` | 意图确认与指定 Cooper 材料读取 |
| `2. 知识系统/` | `know`、`build` | 项目知识检索与沉淀，以及四类独立知识建设子能力的显式路由 |
| `3. 路由系统/` | `router` | 工作流程选择 |
| `4. 执行系统/` | `design`、`dev`、`debug`、`fix`、`it-test` | 设计、实现、诊断、修复与真实 HTTP 验收 |
| `5. 审计系统/` | `audit` | 交付候选和知识候选的独立审计 |
| `6. 交付系统/` | `cr` | 人工 Review、反馈和交付组织 |
| `7. 演进系统/` | `retro`、`skill-creator`、`skill-neat` | 复盘 Harness 运行并受控实施系统改进 |
| `8. 横向能力/` | `subagent` | 跨系统的分支、隔离、并行与结果回收 |
| `9. 额外能力/` | `biz`、`mock` | 条件业务理解与 Mock 平台管理 |

13 个固定 Skill 与三个条件扩展能力会投影到接入项目；
`skill-creator` 和 `skill-neat` 只服务 Harness 演进，不投影到业务项目。`test-design`、
`coding-standards` 和 `unit-test` 已由目标能力或运行机制替代。所有能力在真实主链试运行前保持未启用。

## 初步运行

在本地业务项目建立受管入口：

```bash
python3 scripts/harness.py init --project <业务项目目录>
python3 scripts/harness.py doctor --project <业务项目目录>
```

`init` 会复制 `.harness/runtime/` 与流程入口，并把分层源码扁平投影到业务项目
`.agents/skills/<skill>`；它不会修改业务项目 `AGENTS.md`。接入后仍需由业务项目规则明确
“软件开发任务先读取 `.harness/runtime/HARNESS.md`”，并在首次启用前显式运行 `build` 建设
项目知识体系，再按该入口运行意图、任务信息整合、
路由、执行、独立审计和人工交付。当前只在临时目录试用过接入命令，尚未接入真实业务项目。
