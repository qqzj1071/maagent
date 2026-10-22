# 图标优先，禁止全仓遍历

## 已实施

- 删除逐件点开读文字的 `_traverse_find`、全仓清点 `get_warehouse_inventory`、游戏内逐格学习 `build_item_catalog`，同时移除工具注册与提示。删除其仓库翻页及其他不再使用的辅助函数。
- 查询首先检查正式名称或明确别名对应的本地图标文件。没有有效文件时立即报告不认识，不截图、不进入游戏、不自动下载图标。
- 删除宽松模糊名称匹配。已知图标仅在当前分类页面比较本地图片，可按导入分类选择一个标签；不翻页、不遍历其他物品。
- 匹配分数不足直接报告未定位，这不等于库存为零。最多点开一个候选，详情完整名称必须一致，库存须为清晰整数，否则报告失败。
- 新增 `check_item_icon`（只查本地）和 `learn_item_icon`（博士明确授权后，从PRTS物品页获取名称明确的图标）。普通查询不得自动调用学习工具。

## 图标来源

本机 `E:/MAA/resource/item_index.json` 的480条记录全部有可读图片，来自 `E:/MAA/resource/template/items`；同名不同物品ID保留为多张参考图，来源与导入时间一起记录。

合并旧目录后，`C:/Users/33763/AppData/Local/Maagent/agent_item_catalog.json` 共440个名称。原目录导入前已备份到同目录 backups。图片在 item_icons/maa 与 item_icons/prts；原有本机截图模板仍保留。

PRTS补充：

- [应急理智浓缩液](https://prts.wiki/w/应急理智浓缩液)，图片资源 `https://torappu.prts.wiki/assets/item_icon/ap_supply_lt_120.png`。
- [情报凭证](https://prts.wiki/w/情报凭证)，图片资源 `https://torappu.prts.wiki/assets/item_icon/REP_COIN.png`。

导入使用名称索引及页面图片名称证据，不以模型猜测建立映射。导入图标不代表该物品已拥有或其库存已知。

## 记忆与验证

旧的全仓遍历/模糊猜测规则已归档，仓库查询技能已改为未知即报告。没有再次启动游戏或查询聚合剂。

34项离线回归与模块导入通过。新增检查覆盖：未知及相似名称不触发游戏；已删除遍历入口；图标多版本导入与幂等性；详情名称错误不返回库存。多尺度图像匹配仍可能因UI变化无法定位，此时应报告失败，不能恢复遍历回退。
