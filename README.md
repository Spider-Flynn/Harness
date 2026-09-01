# Harness Engineering

Harness Engineering 是一个在人工治理下、由用户意图驱动的 AI Coding 工程系统。项目围绕知识、
路由、执行、审计、交付和系统演进组织可组合能力，目标是让软件任务的输入、决策、实现、验证与
交付边界可追溯。

本仓库当前处于设计与初步实现阶段，尚未完成真实业务项目的主链验证，也未标记为启用。

## 当前状态

- 系统架构、七个子系统、整体运行闭环和最小 Skill 拓扑已有设计载体。
- 核心 Skills、运行记录模板和项目接入脚本已有初步源码。
- `scripts/harness.py` 与目标设计期待 `design/dev/fix`，当前执行系统源码目录则是
  `tec-design/test-design/coding/debug/unit-test/it-test`。在这组拓扑收敛并重新验证前，
  `init/doctor/update/remove` 不能视为可用的接入能力。
- 静态设计或脚本存在不代表真实路由、外部操作、失败恢复和完整交付链已经验证。

## 项目结构

| 目录 | 职责 |
|---|---|
| `docs/` | 系统架构、子系统设计、实施方案和问题记录 |
| `runtime/` | 全局运行入口、跨系统状态与整体闭环 |
| `flows/` | 可复用流程及其 Skill 编排，不承载单项能力实现规则 |
| `skills/` | 按子系统、横向能力或额外能力分类的独立 Skill 源码 |
| `agents/` | 固定职责、Prompt 与权限边界的专用 Agent 配置 |
| `scripts/` | 项目接入与维护工具 |

主要阅读入口：

- [系统架构](./docs/系统架构.md)：项目定位、系统边界和架构原则。
- [Skill 能力设计](./docs/实施方案/Skill%20能力设计.md)：目标能力拓扑与能力合同。
- [流程路由](./flows/流程路由.md)：可选流程及选择边界。
- [Harness 运行入口](./runtime/HARNESS.md)：未来接入业务项目后的全局运行规则。
- [任务交付闭环](./runtime/整体编排/任务交付闭环.md)：业务任务的跨系统状态与交接。
- [系统演进闭环](./runtime/整体编排/系统演进闭环.md)：Harness 自身的受控改进流程。

## 能力分层

| 分类 | 当前公开源码 |
|---|---|
| `1. 意图接入系统` | `intent` |
| `2. 知识系统` | `know`、`build` |
| `3. 路由系统` | `router` |
| `4. 执行系统` | `tec-design`、`test-design`、`coding`、`debug`、`unit-test`、`it-test` |
| `5. 审计系统` | `audit` |
| `6. 交付系统` | `cr` |
| `7. 演进系统` | `retro`、`skill-creator`、`skill-neat` |
| `8. 横向能力` | `subagent` |
| `9. 额外能力` | `biz` |

`cooper` 与 `mock` 是永久仅限本机的能力副本，由根目录 `.gitignore` 排除，不属于公开仓库源码。
`skill-creator` 与 `skill-neat` 只服务 Harness 演进控制面，不投影到业务项目。

## 实验性接入工具

脚本提供以下命令入口：

```bash
python3 scripts/harness.py --help
python3 scripts/harness.py init --project <业务项目目录>
python3 scripts/harness.py doctor --project <业务项目目录>
python3 scripts/harness.py update --project <业务项目目录>
python3 scripts/harness.py remove --project <业务项目目录>
```

当前拓扑不一致尚未解决，以上命令只作为实施源码与后续验证入口保留，不应直接用于业务项目。
完成拓扑收敛后，仍需在经授权的低风险业务项目中验证正常链、问题回流、恢复和解除接入。

## 维护约定

- 变更前先读取当前文件和直接合同，区分目标设计、当前实现与验证结果。
- 单个子系统只维护自身职责；跨系统时序、状态、失效、恢复、终止和交接归 `runtime/` 与整体编排。
- Powers 与本仓库的同名 Skill 不自动同步；吸收变化前先比较差异并确认适用边界。
- 不把静态检查、Mock、模型判断或脚本存在描述成真实业务链路已经通过。
- 所有公开提交都应遵守 [AGENTS.md](./AGENTS.md) 中的项目边界、Git 规则与维护要求。
