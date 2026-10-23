# 微信启动与窗口恢复

[返回 README](../README.md)

Cortex 概览中的 **启动微信** 按钮会请求 Gateway 打开微信并尝试登录。仅点击明确识别的登录按钮一次；出现二维码或锁定时停止，不会扫码、切换账号或继续恢复窗口。人工处理后可再次点击。确认已进入聊天列表后，恢复已缓存的独立窗口，并将主窗口和所有独立窗口全部最大化；切换窗口保留最大化状态，只有最小化窗口才会还原唤起。

`WECHAT_AUTO_LOGIN_ENABLED=false` 为默认值。设为 `true` 后，Gateway 启动时在后台执行同一流程并持续看护微信，不阻塞 HTTP/WS 服务。Cortex 按钮也启动同一个看护流程，不受开机自动启动开关限制，不会创建第二套守护。`WECHAT_EXECUTABLE_PATH` 可指定微信程序的完整路径，留空时 Windows 查注册表/常见目录，Linux 查 PATH；不接受附加命令行参数。

窗口列表由用户在控制台“独立窗口管理”中手动保存。启动与持续看护只读取该缓存来恢复窗口，不自动保存或覆盖；没有缓存时跳过窗口恢复。恢复阶段失败会在登录等待期内重试，未确认登录最多等待 120 秒，结束后须手动重试或重启 Gateway。

Windows 需要登录的交互式桌面，不能在 Session 0 服务桌面操作 UI。Linux 需要同一用户的 X11 桌面、`xprop` 和 systemd 用户管理器；启动器始终显式注入 `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` 并使用匹配桌面的 D-Bus，与全局设置是否存在无关。微信放在独立 systemd 单元中，重启 Bot 不会连带终止微信。

运行中每 5 秒检查进程；连续确认进程退出后等待 5 秒，再走同一登录、最大化和窗口恢复流程，一小时最多自动恢复 3 次。重试记录以用户私有文件保存，重启 Gateway 不会清空限额。进程存在但界面暂时不可见时只等待，不终止或重复拉起微信；二维码、锁定、登录超时或恢复失败时停止自动操作并保留缓存。Windows/Linux 的进程、启动和 UI 操作分别封装在平台适配器中。窗口扫描会在同名窗口的 ID 变化时替换发送器和检测线程的窗口对象，避免恢复后仍使用失效控件。

启动/看护会等待正在进行的 Console 操作结束，等待期间可响应停止。启动/看护不会调用终止微信的操作。Linux 微信运行在独立 systemd 单元；Windows 通过本机 WMI `Win32_Process.Create` 在交互式桌面启动微信，由系统提供程序创建进程，不进入 Bot 的后代进程树。Windows 启动失败会报错，不回退为 Bot 直接创建子进程；Daemon 的启停逻辑保持原样。

状态及手动启动：Gateway/Cortex 均提供 `GET/POST /api/console/wechat-startup`（REST Bearer 认证）；GET 返回 `enabled/supported/state/message`，POST 额外返回 `accepted`。重复请求复用正在执行的任务。macOS 暂不支持。

Linux 服务开机启动需要同时满足：systemd 用户服务 `awsl-wechat-daemon.service` 已 enable、用户开启 linger、X11 桌面服务已开机启动，以及服务器 `.env` 中启用 `WECHAT_AUTO_LOGIN_ENABLED=true`。已有用户服务时可运行：

```bash
sudo loginctl enable-linger "$USER"
systemctl --user enable --now awsl-wechat-daemon.service
```

用户单元建议 `WantedBy=default.target`、`Restart=always`、`RestartSec=5`，不要反向声明 `After=default.target`。启动线程持续等待图形桌面和对应 D-Bus 就绪后才拉起微信，不因桌面晚启动而永久放弃；进入登录流程后仍有 120 秒超时限制。systemd 管理 Bot 自启/异常重启，平台适配器负责同一流程中的微信启动与持续看护；Linux 的独立微信单元仅隔离进程，不另设重启策略。


完整的操作系统自启配置见 [开机启动部署](boot-autostart.md)。
