# Harness Engineering

Harness Engineering 是围绕 Agent 和 LLM 的运行与编排框架。本仓库当前实现软件工程领域实例，
通过可组合能力、运行时约束和真实证据，支持软件任务的理解、执行、验证、审计、交付与受控演进。

当前仓库已确认框架、领域实例与具体实现的分层边界；软件工程实例的七个职责域已有初步实现，
但 Harness 尚未启用，也未经过真实业务项目的完整任务验证。

## 项目结构

| 路径 | 职责 |
|---|---|
| `AGENTS.md` | 本仓库自身的边界、维护规则和长期协作约束 |
| `TODO.md` | 当前整体重构、二次实现、验证和未决问题 |
| `docs/` | 已确认的系统设计，以及明确标注的讨论候选 |
| `runtime/HARNESS.md` | 接入业务代码仓库后需要加载的唯一运行合同 |
| `skills/` | 按职责域分类的 Harness 能力源码 |
| `scripts/` | 仓库维护、能力投影和业务项目接入工具 |

主要入口：

- [系统设计](./docs/系统设计.md)：整体定位、边界、职责域和设计原则。
- [外部实践与设计候选](./docs/外部实践与设计候选.md)：资料提炼与讨论输入，不是已确认设计或实施计划。
- [运行合同](./runtime/HARNESS.md)：业务项目运行时的公共编排语义。
- [TODO](./TODO.md)：当前实施顺序、依赖和验收门禁。

## 当前软件工程领域实例

以下七个职责域属于当前软件工程领域实例，不是 Harness 框架要求。其他领域实例可以定义不同的
子系统、过程产物、结果产物和运行路线。

| 职责域 | 当前公开能力 |
|---|---|
| 意图接入 | `intent` |
| 知识 | `know`、`build` |
| 路由 | `router` |
| 执行 | `tec-design`、`test-design`、`coding`、`debug`、`unit-test`、`it-test` |
| 审计 | `audit` |
| 交付 | `cr` |
| 演进 | `retro`、`skill-creator`、`skill-neat` |
| 横向能力 | `subagent` |
| 额外能力 | `biz` |

`cooper` 与 `mock` 是永久仅限本机的能力目录，由根目录 `.gitignore` 排除，不属于公开仓库源码；
是否在本机业务项目中投影，另按接入条件和授权决定。

七个职责域不是固定七段流水线。Runtime 根据当前意图、上下文、风险和外部副作用决定能力进入、
跳过、阻塞、恢复、审计、交付和终止；Skill 只维护自身能力规则。

## 接入工具

`scripts/harness.py` 当前仍是实验实现，命令入口为：

```bash
python3 scripts/harness.py --help
python3 scripts/harness.py init --project <业务项目目录>
python3 scripts/harness.py doctor --project <业务项目目录>
python3 scripts/harness.py update --project <业务项目目录>
python3 scripts/harness.py remove --project <业务项目目录>
```

接入工具最终只管理 `runtime/HARNESS.md`、受批准的 Skill 投影和受管清单，不负责选择 Prompt 流程，
也不自动修改业务项目的普通规则。当前拓扑和运行合同仍在收敛，工具未经过真实业务项目验证，
不能直接视为可用接入协议。

## 维护原则

- 修改前先读取当前文件和直接合同，区分当前实现、目标设计和验证结果。
- 跨系统状态、交接、失效、反馈、恢复、终止和重新审计只归 `runtime/HARNESS.md`。
- 仓库维护规则归 `AGENTS.md`，变化中的工作项归 `TODO.md`，Skill 内部方法归对应 Skill。
- 不把静态检查、Mock、模型自述或脚本存在描述成真实业务行为已经通过。
- `cooper` 和 `mock` 永久保持本机忽略，不使用强制添加绕过。
