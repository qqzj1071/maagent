# Maagent 手机端（Flutter）

登录账号后远程操控电脑端 maagent：启动/停止日常工作流、查看实时日志与任务报告。

## 一、环境准备

1. 安装 [Flutter SDK](https://docs.flutter.dev/get-started/install/windows)（含 Android 工具链）。
2. 在 `mobile/` 目录下生成平台目录（`android/`、`ios/` 等）：

   ```
   flutter create .
   ```

   > 不加 `--overwrite`，不会覆盖已有的 `lib/` 与 `pubspec.yaml`。

3. 拉取依赖：

   ```
   flutter pub get
   ```

## 二、Android 网络配置（重要）

默认情况下 Android 9+ 禁止明文 HTTP，且 release 包需要显式声明联网权限。因为手机端通过
Tailscale 访问电脑的 `http://100.x.y.z:8765`（明文，但链路已被 WireGuard 加密），需要：

编辑 `android/app/src/main/AndroidManifest.xml`：

```xml
<manifest ...>
    <uses-permission android:name="android.permission.INTERNET"/>
    <application
        android:usesCleartextTraffic="true"
        ...>
```

## 三、运行 / 打包

```
flutter run                      # 真机调试
flutter build apk --release      # 生成 APK
```

APK 输出：`build/app/outputs/flutter-apk/app-release.apk`

## 四、使用步骤

1. 电脑与手机各安装 Tailscale，登录**同一账号**。
2. 电脑上执行 `tailscale ip -4` 得到 `100.x.y.z`。
3. 启动电脑端服务，二选一：
   - **GUI 模式（推荐）**：`config.yaml` 设 `server.enabled: true`、`server.host: "tailscale"`，
     正常启动 maagent。服务随 GUI 启动，**电脑界面与手机共用同一个工作流控制器**，
     手机上启动/停止，电脑界面会同步显示运行状态与报告。
   - **命令行模式**：`.venv\Scripts\python.exe -m maagent.main --config config/config.yaml --serve`
     （无界面，服务独立运行）。
4. 打开 App，服务器地址填 `http://100.x.y.z:8765`，注册 / 登录。
5. 在「状态」页点「启动日常」，日志页看实时输出，报告页查看历史报告。

> 报告在电脑端也会按原逻辑发送到配置的邮箱，手机端只是多一个实时入口。

## 五、电脑端接口一览（前缀 `/api/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（免鉴权） |
| POST | `/auth/email/code` | 发送邮箱验证码（purpose: register / reset_password） |
| POST | `/auth/register` | 邮箱+手机号+密码+验证码 注册 |
| POST | `/auth/login` | 登录，返回令牌 |
| POST | `/auth/logout` | 登出 |
| GET | `/account/me` | 当前账号 |
| POST | `/account/bind_phone` | 绑定/改绑手机号 |
| POST | `/account/password` | 修改密码 |
| POST | `/auth/password/reset` | 邮箱验证码重置密码 |
| GET | `/status` | 工作流状态（含下次定时） |
| GET | `/config` | 任务清单与定时信息 |
| POST | `/workflow/start` | 启动日常（可传 `{"software":["maa"]}`） |
| POST | `/workflow/stop` | 停止日常 |
| GET | `/logs?limit=200` | 最近日志 |
| GET | `/reports?limit=20` | 报告列表 |
| GET | `/reports/latest` | 最新报告 |
| GET | `/reports/detail?id=` | 报告详情（含日志） |
| GET | `/events` | SSE 实时事件（snapshot/status/log/report） |

除 `/health` 外均需请求头 `Authorization: Bearer <token>`；`/events` 也支持 `?token=`。
