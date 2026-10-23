# 开机启动、微信登录与窗口恢复

本文说明部署机器的配置，不引入新的程序或守护逻辑。Linux 和 Windows 都由操作系统启动 `run_daemon.py`，Daemon 启动 Gateway、Chatlog、Cortex，Gateway 使用现有平台适配器启动微信、点击登录并恢复手动保存的独立窗口。

## 部署参数

先确定运行账户、项目目录和桌面会话。下文 `<LINUX_USER>`、`<LINUX_HOME>`、`<LINUX_UID>`、`<LINUX_PROJECT_DIR>`、`<WINDOWS_USER>`、`<WINDOWS_HOME>`、`<WINDOWS_PROJECT_DIR>` 均为占位符，使用前必须替换为目标机器的实际值，不要原样执行。Linux UID 可通过 `id -u` 查询。

Linux 示例使用 TigerVNC 显示器 `:1`、固定分辨率 `1920x1080`；如采用其他显示器或桌面，需要同步调整对应配置。系统目录、服务名和应用缓存路径为配置示例，不包含实际部署机器的信息。

## 共用的应用配置

在目标机器的项目 `.env` 中设置：

```dotenv
WECHAT_AUTO_LOGIN_ENABLED=true
# 留空自动查找；也可填写完整可执行文件路径，不附带启动参数。
WECHAT_EXECUTABLE_PATH=
```

修改后需要重启 Bot 才会加载。系统启动入口始终使用 `run_daemon.py`，不要同时再注册 `main.py` 或各服务的独立自启入口。微信也由 Gateway 启动，避免另设微信自启任务。

在 Cortex 控制台的“独立窗口管理”中手动保存需要恢复的窗口。缓存位于运行账户的 `~/.config/awsl-wechat-gateway/standalone-windows.json`；Windows 的 `~` 对应运行账户的用户目录（通常为 `%USERPROFILE%`）。启动流程只读取缓存，不自动保存或覆盖。没有缓存时只登录微信，不恢复独立窗口。

自动流程需要微信已有可复用的登录状态。遇到二维码、锁定或登录超时会停止并保留缓存，不能绕过手机确认；人工处理后可通过 Cortex“启动微信”按钮重新启动同一流程。进程正常运行但主界面不可识别时只等待，不应额外直接执行微信程序来尝试唤起。

## Linux：桌面与 Python 服务开机启动

使用同一个普通用户运行桌面、Daemon 和微信。采用以下 TigerVNC 方案时，无需在显示管理器中额外配置本地桌面自动登录：systemd 在开机时启动 TigerVNC，TigerVNC 启动 XFCE，服务使用这一套 X11 会话。

### 1. 桌面服务

创建系统单元 `/etc/systemd/system/awsl-wechat-desktop.service`；已有桌面服务时复用原服务，不重复创建：

```ini
[Unit]
Description=AWSL fixed 1920x1080 TigerVNC XFCE desktop
After=network.target

[Service]
Type=simple
User=<LINUX_USER>
Environment=HOME=<LINUX_HOME>
Environment=USER=<LINUX_USER>
PAMName=login
ExecStartPre=-/usr/bin/tigervncserver -kill :1 -clean
ExecStart=/usr/bin/tigervncserver -fg :1
ExecStop=/usr/bin/tigervncserver -kill :1
KillMode=control-group
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

`ExecStartPre` 清理的是同一显示器的旧 VNC 会话。不要为了更新 Bot 重启这个桌面服务，桌面重启会影响其中的图形应用。

建议 TigerVNC 使用固定的 `1920x1080`、24 位色深、96 DPI，`AcceptSetDesktopSize=0`，避免客户端连接时改变桌面分辨率。VNC 认证沿用机器已有配置，不将密码写入仓库。

`~/.config/tigervnc/xstartup` 负责启动桌面；部分版本使用 `~/.vnc/xstartup`，以安装版本实际读取的位置为准。文件需有执行权限：

```sh
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
export XAUTHORITY="$HOME/.Xauthority"
xrdb "$HOME/.Xresources" 2>/dev/null || true
xsetroot -solid '#1e1e1f' 2>/dev/null || true
exec dbus-run-session -- xfce4-session
```

### 2. Daemon 用户服务

创建用户单元 `~/.config/systemd/user/awsl-wechat-daemon.service`。已有服务应先用 `systemctl --user cat` 检查主单元和 drop-in，复用同一个服务名，避免遗留的覆盖配置与新配置冲突。

```ini
[Unit]
Description=AWSL WeChat Bot Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=<LINUX_PROJECT_DIR>
ExecStart=<LINUX_HOME>/.local/bin/uv run run_daemon.py
Environment=PYTHONUNBUFFERED=1
Environment=PATH=<LINUX_HOME>/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=DISPLAY=:1
Environment=XAUTHORITY=<LINUX_HOME>/.Xauthority
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<LINUX_UID>/bus
Environment=QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

路径、UID、显示器必须与运行账户和桌面对应；`uv` 的可执行文件位置也应按实际安装位置调整。`XDG_RUNTIME_DIR` 由 systemd 用户管理器提供。

Gateway 等待 X11 和匹配桌面的 D-Bus 就绪后才启动微信。用户服务中的 D-Bus 地址是用户总线；Linux 启动适配器会读取匹配 XFCE 会话的 D-Bus 地址，并显式给微信注入 `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`。如需对其他启动来源全局启用，也可在 `/etc/environment` 中设置该变量；Gateway 启动微信时仍会显式注入。

以选定的 Linux 运行账户执行下列命令；桌面系统服务与 linger 需要管理员权限：

```sh
sudo loginctl enable-linger "$USER"
sudo systemctl daemon-reload
sudo systemctl enable --now awsl-wechat-desktop.service
systemctl --user daemon-reload
systemctl --user enable --now awsl-wechat-daemon.service
```

linger 使用户服务在无人 SSH 登录时也能随开机启动。不要给用户服务添加 `After=default.target`；桌面尚未就绪时，由 Gateway 现有等待流程处理。

systemd 负责 Daemon 自启与异常退出后的重启，Daemon 负责 Bot 子进程，Gateway 负责微信持续看护。微信使用独立的临时 systemd 用户单元隔离进程，其单元不另设重启策略；正常重启 Bot 不会连带终止微信。

### 3. 检查与维护

```sh
loginctl show-user "$USER" -p Linger
systemctl is-enabled awsl-wechat-desktop.service
systemctl status awsl-wechat-desktop.service
systemctl --user is-enabled awsl-wechat-daemon.service
systemctl --user status awsl-wechat-daemon.service
systemctl --user cat awsl-wechat-daemon.service
journalctl --user -u awsl-wechat-daemon.service -b -n 100 --no-pager
```

更新 `.env` 后，可使用 Daemon 的 Bot 重启 API 加载配置；修改 systemd 单元后执行 `daemon-reload` 并重启对应 Daemon 服务。不要同时再手动运行一个 `run_daemon.py`。

## Windows：先自动进入桌面，再启动 Daemon

Windows 的 UI Automation 需要已登录、可交互的用户桌面。单纯配置“系统启动时运行”或安装 Session 0 系统服务，不能代替进入桌面。完整顺序为：

1. Windows 自动登录选定的运行账户，建立用户桌面。
2. 当前用户的“启动”文件夹快捷方式在登录桌面后启动 Daemon。
3. Gateway 使用已启用的自动登录流程启动微信、恢复缓存窗口并最大化。

### 1. 自动进入桌面

使用微软官方 [Sysinternals Autologon](https://learn.microsoft.com/en-us/sysinternals/downloads/autologon)，在 Windows 本机界面中填入选定的运行账户名、本机计算机名以及账户真实密码，点击 **Enable**。这里使用账户密码，不是 Windows Hello PIN；不要把密码发送到聊天、写入文档或放进命令行参数。

该工具把密码保存为 LSA secret，避免直接在 Winlogon 的 `DefaultPassword` 中写明文；管理员仍可以提取，因此适用于受控的专用机器。[微软自动登录说明](https://learn.microsoft.com/en-us/troubleshoot/windows-server/user-profiles-and-logon/turn-on-automatic-logon)也列出了登录提示横幅、账户策略等可能阻止自动登录的条件。

这一步解决的是 Windows 桌面登录，不能代替微信登录。禁用时重新打开 Autologon，点击 **Disable**。

### 2. 用“启动”文件夹启动 Daemon

在选定的运行账户中按 `Win+R`，输入 `shell:startup`，打开当前用户的启动文件夹。在其中创建一个 `AWSL-WeChat-Daemon.lnk` 快捷方式：

| 字段 | 设置 |
| --- | --- |
| 目标程序 | `<WINDOWS_HOME>\.local\bin\uv.exe`（按实际安装位置调整） |
| 参数 | `run run_daemon.py` |
| 起始位置 | `<WINDOWS_PROJECT_DIR>` |
| 运行方式 | 正常窗口 |

Windows 快捷方式属性中的“目标”字段需要把程序与参数合并填写，例如 `"<WINDOWS_HOME>\.local\bin\uv.exe" run run_daemon.py`；“起始位置”单独填写项目目录。它会在该用户登录桌面后运行，继承交互式桌面，不需要计划任务或 Session 0 系统服务。参见[微软启动应用配置说明](https://support.microsoft.com/en-us/windows/experience/startup-boot/configure-startup-applications-in-windows)。

`uv` 还需能从该用户的 `PATH` 查找到，因为 Daemon 内部也通过 `uv` 启动 Bot。提前在项目目录运行 `uv sync --locked`，使依赖安装完成。不要使用 `pythonw.exe`：当前 Daemon 的日志读取线程直接写入标准输出，需要正常的标准输出流。

把项目 `.env` 中的 `WECHAT_AUTO_LOGIN_ENABLED` 改为 `true`，并关闭微信自身的“开机自动启动”入口，使 Gateway 负责启动微信。检查微信设置、用户 `Run` 项和启动文件夹中的既有微信自启入口，避免两个启动来源竞争。

启动文件夹只负责登录时启动一次 Daemon；运行期间的 Bot 子进程看护使用 Daemon 现有机制。它不提供 Daemon 自身退出后的自动重启或跨入口的单实例保护。保留一个启动快捷方式，登录后不要再手动运行第二个 Daemon。

配置时只保存快捷方式，不双击执行，避免与当前手动运行的 Daemon 抢占端口。首次手动验证前先正常退出原 Daemon；已有微信无需为此退出，也不要另外直接运行微信程序来验证。

### 3. Windows 的验证与回退

确认 Autologon 已启用、启动快捷方式的目标与起始位置正确，并已开启微信自动登录配置后，再经确认执行整机重启。观察是否依次进入桌面、启动 Daemon、登录微信并恢复缓存窗口。不能用 SSH 中手动启动一次服务代替此验证。

需要暂停服务自启时，从 `shell:startup` 移走或删除 `AWSL-WeChat-Daemon.lnk`；需要取消系统自动登录时，用 Autologon 的 **Disable**。应用内关闭 `WECHAT_AUTO_LOGIN_ENABLED` 只关闭 Gateway 的自动启动与看护，不会取消 Windows 桌面自动登录。

## 如何判断恢复完成

所有 REST 接口按机器配置使用 Bearer 认证，不把实际 Token 写进文档或 URL：

- Daemon `:8001/status`：Bot 正在运行。
- Gateway `:8000/api/health`、Chatlog `:7998/api/chatlog/health`、Cortex `:7999/api/health`：服务可用；启动中出现短暂未就绪应继续观察。
- Gateway `GET /api/console/wechat-startup`：`enabled=true`、`state=ready`。
- Gateway `GET /api/console/standalone-windows`：缓存中的会话都已恢复，以稳定 `chat_id` 比对，不要求重启后窗口 ID 相同。
- 主窗口与独立窗口均最大化；手动保存的窗口缓存保持不变。

服务进程存在不代表微信已登录。出现 `qr_required`、`locked`、`timeout`、`failed` 或 `restart_limited` 时，应检查状态说明和日志并人工处理，不额外启动第二个微信，也不主动终止现有微信。
