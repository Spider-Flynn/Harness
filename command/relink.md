# relink：修复已有绑定

确定目标项目；缺少目标时根据本仓库 `data/projects.json` 帮助选择，索引不可用则询问路径。
复用目标项目原绑定，不重新询问系统、入口和能力，不把 `relink` 当作更换系统或首次绑定。

从当前 Harness 仓库调用 `python3 scripts/harness.py relink --project "<项目绝对路径>"`；
成功后调用同一脚本的 `doctor --project "<项目绝对路径>"`，反馈实际修复、检查结果和提示。
失败时解释脚本提示，不手工改清单，也不自动解绑重建。
