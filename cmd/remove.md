# remove：解除绑定

明确具体项目；缺少目标时根据本仓库 `data/projects.json` 帮助选择，索引不可用则询问路径。
说明只移除受管链接、规则片段、Git Hook 和绑定记录，恢复接入前的两个 Hook，保留业务代码、
`.harness/know/`、`.harness/tasks/` 及其他规则；工具创建且解绑后
为空的规则文件也可能被移除。目标、范围和授权清楚后，从本仓库调用
`python3 scripts/harness.py remove --project "<项目绝对路径>"`。

反馈实际结果并回读相关路径确认，不用 `doctor` 将已解绑误报为失败。冲突时解释脚本提示，
不强制删除。需要恢复时可重新 `init`，不承诺自动恢复原选择。
