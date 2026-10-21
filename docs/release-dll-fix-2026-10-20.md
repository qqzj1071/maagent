# QtCore 打包启动错误排查

实际启动新版 EXE 重现 `DLL load failed while importing QtCore: 找不到指定的程序`。
对比 10 月 20 日 12:01 可用版与失败版 CArchive：Qt6Core.dll、QtCore.pyd、
shiboken 和 MSVC DLL 文件内容相同，但 UCRT/API-set DLL 不同。

失败构建的 Analysis-00.toc 显示：

- `ucrtbase.dll` 和多项 `api-ms-win-*.dll` 来自
  `.cache/codex-runtimes/codex-primary-runtime/dependencies/native/libheif/libheif/bin`。
- 可用构建的对应文件来自 `C:\Windows\System32`。
- 失败构建还从工具环境 poppler 目录收集了其他 DLL。

原因是构建宿主 PATH 中工具目录优先，PyInstaller 依赖分析收集了不适合本程序的
运行库。仅验证开发环境导入或安装包文件哈希无法发现这种发布包错误。

修复：在 `maagent.release.spec` 执行依赖分析前，把 PATH 限定到系统和本次 Python
目录，再用 `--clean` 重建。仍需实际安装启动并核对主窗口、启动日志及手机服务，
不得仅凭构建成功宣称发布成功。

## 发布验证

2026-10-20 21:16 修正版安装完成，实际从桌面快捷方式启动成功：
主窗口 `Maagent - 二游日常助手` 已出现、进程响应正常，启动日志确认 Qt 界面、
全局热键和账号服务正常初始化。`/api/v1/health` 返回 `ok: true`。
实际安装 EXE 与构建结果哈希一致，安装前后 config.yaml 哈希一致。
36 项发布源码测试通过。安装包为桌面 `Maagent-Setup-0.1.0-20261020.exe`。

启动后原配置的 21:00 定时任务于 21:16:25 被调度器补执行；未手动触发日常。
后续启动验证前也应检查定时补执行窗口，避免将配置自动触发误认为手动测试。
