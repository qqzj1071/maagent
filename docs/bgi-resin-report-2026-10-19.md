# BetterGI 树脂消耗报告

> 2026-10-20：按用户要求撤销本功能。报告不再添加原粹树脂消耗字段，
> 不再解析原粹树脂或浓缩树脂信息，相关解析函数和专用测试已删除。
> 以下保留为历史开发记录。

今天 `D:/tools/BetterGI/log/better-genshin-impact20261019.log` 仅记录了
08:44:19.947「合成树脂执行异常：当前获取焦点的窗口不是原神」，没有用量。
不能由此推断消耗为 0，更不能按日常次数推断 200 点。
10 月 18 日成功合成时也仅记录「合成"浓缩树脂"」，没有合成数量。

新增报告字段「原粹树脂消耗」，沿用 RunReport.extra，覆盖邮件文本、HTML
及手机报告。只处理本次增量日志，并在汇总前补读尾部日志。

支持 BetterGI 官方 AutoDomainTask 的日志格式：
`"自动秘境"：使用 "原粹树脂", 数量：20`，累加记录的用量。
浓缩树脂使用数量单列，不折算到本次原粹树脂消耗（可能来自以前的库存）。
无记录显示无法确认；无数量的合成记录提示统计缺失。
所有数值均标为日志记录用量，不宣称覆盖完整日常；BGI 的点击使用日志
本身也不等同于游戏服务器确认，且其 OCR 失败时可能默认按 20 计算。
树脂任务异常、数量识别失败会进入报告提示。

格式核对来源：
- https://github.com/babalae/better-genshin-impact/blob/main/BetterGenshinImpact/GameTask/AutoDomain/AutoDomainTask.cs
- https://github.com/babalae/better-genshin-impact/blob/main/BetterGenshinImpact/GameTask/Common/Job/GoToCraftingBenchTask.cs

要实现合成路径的精确日常总量，需要 BGI 额外输出实际合成数量或可靠的
消耗事件；现有日志不足以还原。此改动没有修改 BGI 文件、历史报告或发送邮件。
