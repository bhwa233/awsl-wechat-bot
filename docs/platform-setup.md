# 平台配置与疑难排查

[返回 README](../README.md)

当前维护 Windows 和 Linux。**macOS 已停止维护，不建议使用**；Quartz / Vision 实现仅保留作历史参考。

以下命令均在项目根目录执行。

## Gateway 收发方式（Windows / Linux）

沿用 `.env` / 环境变量 `WINDOWS_MESSAGE_READER`（默认 `accessibility`）、`LINUX_MESSAGE_READER`（默认 `chatlog`）。两项含义一致，分别控制对应平台的 Gateway 收发，修改后重启 Gateway：

| 值 | 接收消息 | 独立窗口发送 |
| --- | --- | --- |
| `chatlog` | Chatlog v2，提供发送人及 @ 信息 | Windows 使用 Win32，Linux 使用 X11；不调用 UIA / AT-SPI |
| `accessibility` | Windows UIA / Linux AT-SPI，只读取窗口已加载的内容 | 使用对应平台的辅助功能定位和操作控件 |

Console 独立于此配置，始终使用 Windows UIA / Linux AT-SPI。微信自动登录和窗口恢复也仍需辅助功能。不要通过切换 Gateway 模式关闭 Console 的辅助功能。

Chatlog 模式要求配置本机 Chatlog v2 和数据库密钥。独立窗口必须已打开且名称唯一；发送按窗口尺寸定位输入区，校验窗口身份与前台窗口，只能确认窗口级焦点，不能确认输入控件焦点。普通文本中的 `@昵称` 按原文发送，原生 @（含 `mention_all=true`）需要辅助功能模式；Linux 原生语音也需要辅助功能模式，Windows 可沿用右 Alt 录音快捷键。

Windows 的 Chatlog 模式仅发现属于微信进程、且标题能唯一匹配 Chatlog 群列表的窗口；不会将其他程序、迁移或备份对话框当作群聊。

Linux AT-SPI 不依赖 Chatlog，但当前控件不提供发送人及 @，只能过滤程序自己发送的文本/文件，无法保证窗口外消息不丢失。

## Windows 语音发送

Windows 支持两种语音消息：`voice` 在独立线程中优先调用 OpenAI 兼容的 LocalAI TTS 接口，失败时回退 EdgeTTS，合成期间不阻塞其他消息发送；`voice_file` 直接发送 Base64 编码的 MP3。Windows 经 VB-CABLE 送入微信麦克风；Linux 使用 PulseAudio 虚拟麦克风，macOS 暂不实现语音发送。由于 TTS 完成时间不固定，后入队的普通消息可能先于语音发出。

1. 安装 [VB-CABLE](https://vb-audio.com/Cable/)。
2. 将 Windows 默认录音设备设为 `CABLE Output (VB-Audio Virtual Cable)`。
3. 确认播放设备中存在 `Speakers (VB-Audio Virtual Cable)`。
4. 微信和 Gateway 必须运行在同一个交互式桌面会话中。

LocalAI 默认使用 `qwen3-tts-cpp-customvoice-q4` 的 Serena 女声、请求 MP3，API 超时 180 秒；EdgeTTS 回退使用晓晓女声和正常语速。可在 `.env` 覆盖：

```env
LOCALAI_TTS_API_URL=https://local-ai.example.com/v1/audio/speech
LOCALAI_TTS_API_KEY=your-localai-api-key
LOCALAI_TTS_MODEL=qwen3-tts-cpp-customvoice-q4
LOCALAI_TTS_VOICE=Serena
LOCALAI_TTS_TIMEOUT=180
EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural
EDGE_TTS_RATE=+0%
WINDOWS_VOICE_PLAYBACK_DEVICE=Speakers (VB-Audio Virtual Cable)
WINDOWS_VOICE_RECORDING_DEVICE=CABLE Output (VB-Audio Virtual Cable)
WINDOWS_VOICE_SAMPLE_RATE=48000
WINDOWS_VOICE_MAX_TEXT_LENGTH=1000
# 图片、文件和 MP3 语音的原始二进制上限（字节）
BINARY_MAX_FILE_BYTES=33554432
WINDOWS_VOICE_MAX_DURATION=60
WINDOWS_VOICE_TIMEOUT=90
```

语音发送和其他消息共用 Windows Adapter 的全局 UI 锁。发送时激活目标窗口、按住右 Alt、向 VB-CABLE 播放音频，结束后释放右 Alt。

可直接用测试脚本验证指定窗口，只有松开右 Alt 后出现新的语音气泡才会返回成功：

```bash
uv run python -m tools.test_windows_voice --group Bruce --text "你好，这是一条中文语音测试。" --send
uv run python -m tools.test_windows_voice --group Bruce --file-path ./voice.mp3 --send
```

## Linux setup

Linux Gateway 通过 Chatlog 读取消息、通过 X11 发送消息，不使用截图/OCR。需要先启动 Linux WeChat，并保持要监听的群聊窗口打开。

```bash
# Ubuntu/Debian 示例：只安装系统工具，Python 依赖仍由 uv 管理
sudo apt-get install -y wmctrl xdotool xclip x11-utils xsel pulseaudio pulseaudio-utils
```

微信与 Gateway 使用同一个 Linux 用户和 X11 会话。`LINUX_MESSAGE_READER=chatlog` 时，已打开独立窗口的文本、图片和文件收发不要求微信开放 AT-SPI，也不会先尝试 AT-SPI 再回退。

Gateway 的 `accessibility` 模式、Console、微信自动登录和窗口恢复需要 AT-SPI。启用方式是在启动微信前设置：

```bash
QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 /opt/wechat/wechat
```

也可将 `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` 写入 `/etc/environment`，重新登录桌面后生效；已运行的微信仍需重启。
Console 通过 AT-SPI 读取会话列表、切换聊天、定位输入框和操作独立窗口，复用 `/console`、Cortex 前端及 `/api/console/*` 接口。
会话列表会虚拟化，`path` 必须使用最近一次 state 返回的值；列表变化后失效的 path 会拒绝执行。
列表 path 不是 `xxx@chatroom` 群 ID；稳定会话 ID 由 Chatlog 查询得到。Console 的 AT-SPI 控件缺失或返回无效坐标时停止发送；界面可见不代表辅助功能控件可用，应刷新或重新选择会话后再操作。
独立窗口缓存使用会话 ID，恢复时重新查询当前显示名；同名会话无法唯一对应时拒绝自动操作。
Linux 微信打开独立窗口时会清空主窗口右侧聊天区；Console 在开窗后重新选中原会话，让主窗口继续可用。手动开窗留下空白区时，重新点击目标会话即可恢复；此时 state 的整体 `available=true` 只代表主窗口可访问，仍需检查 `chat_area.available` 和 `input_box.available`。

文本、图片、文件和原生语音发送共用跨进程 UI 锁。粘贴前校验剪贴板，回车前复查发送上下文；辅助功能路径检查当前聊天与输入框焦点，Chatlog 模式的独立窗口发送检查目标窗口与前台窗口。同名会话在 Chatlog 或当前窗口列表中无法唯一对应时停止操作。
Console 的操作锁覆盖 TTS 合成期间，期间切换聊天、滚动和开关窗口会返回忙；合成本身不占全局 UI 锁。窗口缓存恢复按实际结果返回成功或失败，并提供 opened/already_open/failed 计数。
图片按图片 MIME 粘贴，文件按 `text/uri-list` 粘贴并保留文件名。文本开头的 `@昵称 ` 会尝试选择原生成员候选，支持多个前缀（如 `@甲 @乙 正文`）；名称中含空白不支持作为单个候选。只扫描新出现的提及弹层，点击候选后确认弹层关闭且输入框新增原生提及对象。候选不存在或重名时清理已选提及后按原文发送。原生 @ 是否成功应核对 Chatlog `at_users`，不能只看正文有 `@`。
Console 文本引用只匹配当前可见消息，未找到原文或无法确认引用预览时停止发送。

Linux 原生语音需要启动用户的 PulseAudio 服务（`systemctl --user start pulseaudio.socket pulseaudio.service`）。
无声卡机器需在微信启动前创建可枚举的虚拟麦克风；微信可能缓存启动时的设备列表，仅在发送时创建设备会提示未检测到麦克风。
`voice` 复用 LocalAI/EdgeTTS，`voice_file` 接收 MP3；音频解码后播放到临时虚拟麦克风，AT-SPI 定位录音/发送控件，X11 点击执行。Linux 不依赖 Windows 的右 Alt 录音快捷键。
结束时恢复原默认麦克风并移除临时设备。安装 `pactl`、`paplay` 后健康接口会报告语音能力，音频服务不可用时发送会失败。
若录音取消或结束无法确认，保留无声麦克风与默认无声输入，并将目标窗口记录到 `~/.cache/awsl-wechat-gateway/pending-recording.json`；后续自动 UI 操作暂停，进程重启后仍会检查。确认录音流关闭、录音控件退出后，才恢复原默认麦克风、清理设备和标记。不要直接删除标记；应在微信中取消录音后重试操作。
Linux 语音沿用 `WINDOWS_VOICE_MAX_DURATION`、`WINDOWS_VOICE_TIMEOUT` 限制。录音或发送结果不确定时应先查 Chatlog，不自动重发。

无声卡机器可在现有 `~/.config/pulse/default.pa` 末尾加入以下配置；新建此文件时，第一行先写 `.include /etc/pulse/default.pa`，保留系统基础模块。重新启动用户 PulseAudio 和微信后生效：

```text
load-module module-null-sink sink_name=awsl_gateway_input_sink rate=48000 channels=2
load-module module-remap-source master=awsl_gateway_input_sink.monitor source_name=awsl_gateway_mic source_properties="device.class=sound device.description=AWSL_Gateway_Microphone"
set-default-source awsl_gateway_mic
```

systemd 启动时需让微信与 Gateway 指向同一 X11 会话（`DISPLAY`、`XAUTHORITY`）及用户运行目录（`XDG_RUNTIME_DIR`）。桌面 D-Bus 地址可能与 SSH 会话不同，AT-SPI 连接优先读取同用户微信进程的会话环境。环境变量改变后要重启对应进程，不能只修改 `.env` 或 SSH 当前环境。

Chatlog 的 Windows DLL、Linux amd64/ARM64 SO 及兼容别名统一使用上游 v0.1.22 release pipeline 产物。Linux 使用 `file:…?mode=ro` 并移除 `nolock`，继续读取 WAL，禁止写数据库；不使用会忽略 WAL 的 `immutable`。发布来源、CI、回归和各二进制 SHA256 见 [原生库来源](../chatlog/vendor/UPSTREAM.md)。

收消息使用 Chatlog v2 的 `seq` 增量游标，按升序排空积压；同 seq 边界填满一页时用批量接口的 `offset` 继续翻页，保留边界去重。已识别的自发消息（含未确认消息）直接过滤，不阻塞后续他人消息。Gateway 监听仍以已打开的独立聊天窗口为准。
批量增量读取严格检查数据库时间索引，任一相关数据库读取失败则整批报错；Gateway 保留原游标，下轮重试，避免把不完整结果当作最新进度。

`.env` 至少需要配置：

```env
CHATLOG_USE_V2=true
WECHAT_DB_INPUT_PATH=/path/to/xwechat_files/xxx/db_storage
WECHAT_DB_KEY={"contact/contact.db":"64hex","message/message_0.db":"64hex"}

LINUX_WECHAT_DISPLAY=:10
LINUX_WECHAT_XAUTHORITY=~/.Xauthority
```

`WECHAT_DB_KEY` 在 Linux 下是 per-db key JSON，key 使用相对 `db_storage` 的数据库路径；`LINUX_CHATLOG_MESSAGE_LIMIT` 和 `LINUX_CHATLOG_GROUP_CACHE_TTL` 可不填，使用默认值即可。
微信新建/重建数据库后需补充或重新派生对应 key；例如首次发送语音后可能才创建 `message/media_0.db`，缺少它的 key 会导致 Chatlog 语音下载失败。Gateway 不自动捕获 passphrase 或修复数据库。

按微信版本提取 Linux 密钥：

```bash
# 微信 ≤4.1.12：从运行进程内存扫描
sudo uv run tools/extract_wx_keys_linux.py --json

# 微信 ≥4.1.13：先关闭微信，工具会重启微信并在登录时捕获 passphrase
uv run tools/extract_wx_keys_linux.py --mode passphrase --json
```

默认输出是 `{"contact/contact.db":"64hex","message/message_0.db":"64hex"}` 这种 flat JSON；如果写入 `.env`，整段 JSON 放到 `WECHAT_DB_KEY=` 后面即可。

Linux 的主要 Gateway/Console 能力包括文本、图片、文件、TTS/MP3 原生语音、前缀提及、会话列表/切换/滚动、独立窗口开关/缓存/手动恢复和可见文本引用。与 Windows 的边界差异：Linux 需要 X11（不支持纯 Wayland），默认接收消息依赖 Chatlog v2 与 per-db key，也可配置为 AT-SPI，引用原文找不到时停止发送，列表没有稳定群 ID。
Console state 提供中英文引用的 `content/quote/raw_text`、时间行分类及置顶/免打扰/多行预览；时间行结合格式和行高识别，避免把时间格式的正文直接当作时间。`include_patterns=true` 返回当前控件实际支持的 AT-SPI 接口、属性、动作与文本能力，只读取信息，不执行动作。Linux AT-SPI 不提供的 Windows UIA 类名和 Pattern 不会伪造。
图片/文件名限制为 200 个 UTF-8 字节，截断时保留普通扩展名并加短哈希避免同前缀撞名，文件创建或写入失败也会清理临时路径。独立窗口无法匹配会话 ID 时拒绝覆盖原缓存；语音结束后先恢复默认麦克风、卸载临时设备，再删除恢复标记，清理失败会保留标记供后续操作重试。

## Windows 疑难排查

Windows 的 UIA 后台线程统一使用 COM MTA，启动登录的 WMI 调用也使用同一模式并成对释放，避免扫描、看护与发送共用客户端时互相等待 STA 消息循环。

如果在 Windows 上启动后找不到窗口或无法获取消息，可能是讲述人（Narrator）未开启导致 UI Automation 无法正常工作。尝试通过注册表启用：

```powershell
# 查询讲述人状态（0=关闭，1=开启）
reg query "HKCU\SOFTWARE\Microsoft\Narrator\NoRoam" /v RunningState

# 启用讲述人
reg add "HKCU\SOFTWARE\Microsoft\Narrator\NoRoam" /v RunningState /t REG_DWORD /d 1 /f
```

也可以通过 **设置 → 轻松使用 → 讲述人** 手动开启，或按 `Win + Ctrl + Enter` 快捷键切换。
