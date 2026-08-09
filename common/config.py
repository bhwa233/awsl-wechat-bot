"""
AWSL 微信机器人配置 - 使用 Pydantic Settings
支持从环境变量读取配置
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """配置类 - 自动从环境变量读取"""

    # 触发关键词
    TRIGGER_KEYWORD: str = "awsl"

    # OCR 自定义词汇表
    OCR_CUSTOM_WORDS: list[str] = ["awsl"]

    # API 地址
    API_URL: str = "https://awsl.api.awsl.icu/v2/random_json"

    # 命令 API 地址
    REMOTE_COMMANDS_ENABLED: bool = True
    COMMAND_API_BASE_URL: str = ""

    # 检查消息间隔(秒)
    CHECK_INTERVAL: int = 1

    # 触发限速 - 每个窗口每分钟最多触发次数
    TRIGGER_RATE_LIMIT: int = 10
    TRIGGER_RATE_WINDOW: int = 60  # 限速时间窗口(秒)

    # 发送间隔配置
    GLOBAL_SEND_INTERVAL: float = 3.0  # 全局发送间隔(秒)

    # Windows 语音发送(EdgeTTS -> VB-CABLE -> 微信右 Alt 录音)
    EDGE_TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"
    EDGE_TTS_RATE: str = "+0%"
    WINDOWS_VOICE_PLAYBACK_DEVICE: str = "Speakers (VB-Audio Virtual Cable)"
    WINDOWS_VOICE_RECORDING_DEVICE: str = "CABLE Output (VB-Audio Virtual Cable)"
    WINDOWS_VOICE_SAMPLE_RATE: int = 48_000
    WINDOWS_VOICE_MAX_TEXT_LENGTH: int = 1_000
    WINDOWS_VOICE_MAX_FILE_BYTES: int = 7_000_000
    WINDOWS_VOICE_MAX_DURATION: float = 60.0
    WINDOWS_VOICE_TIMEOUT: float = 90.0

    # 调试模式(输出每次检测到的所有消息)
    DEBUG: bool = False

    # Chatlog 默认后端(无版本前缀 /api/chatlog 路由),True 用 v2(DLL,Windows-only),False 用 v1(pycryptodome)
    CHATLOG_USE_V2: bool = True

    # AppleScript 超时配置(秒)
    APPLESCRIPT_TIMEOUT_SHORT: int = 10   # 简单操作: 窗口位置、图片复制
    APPLESCRIPT_TIMEOUT_MEDIUM: int = 20  # 中等操作: 消息获取
    APPLESCRIPT_TIMEOUT_LONG: int = 30    # 复杂操作: 深度UI遍历、窗口激活

    # OCR 截图区域参数(弹出窗口只用 TOP/HEIGHT,主窗口全用)
    SCREENSHOT_LEFT_RATIO: float = 0.30
    SCREENSHOT_TOP_RATIO: float = 0.10
    SCREENSHOT_WIDTH_RATIO: float = 0.65
    SCREENSHOT_HEIGHT_RATIO: float = 0.75

    # OCR 识别参数
    OCR_LANGUAGES: list[str] = ["zh-Hans", "zh-Hant", "en"]  # Vision 识别语言
    OCR_MIN_TEXT_HEIGHT: float = 0.01      # Vision minimumTextHeight
    OCR_MIN_TEXT_LENGTH: int = 2           # 消息最小字符数

    # OCR 消息过滤参数
    OCR_LEFT_THRESHOLD: float = 0.2        # x < 此值判定为他人消息
    OCR_CONFIDENCE_THRESHOLD: float = 0.3  # 置信度阈值
    OCR_NICKNAME_Y_GAP: float = 0.06       # 昵称与消息的 y 差值上限
    OCR_NICKNAME_X_DIFF: float = 0.005     # 昵称与消息的 x 差值下限
    OCR_SAME_LINE_Y_GAP: float = 0.005    # 同一行 OCR 碎片的 y 差值阈值
    OCR_MESSAGE_GAP: float = 0.05          # 不同消息之间的 y 间距阈值

    # OpenAI 配置(从环境变量读取)
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_MAX_TOKENS: int = 500
    OPENAI_TEMPERATURE: float = 0.7

    # Gateway HTTP API 服务配置
    HTTP_API_HOST: str = "0.0.0.0"
    HTTP_API_PORT: int = 8000
    HTTP_API_TOKEN: str = ""  # Bearer Token 认证,为空则不启用认证
    HTTP_API_BASE_URL: str = "http://localhost:8000"  # 内部回调地址

    # Handler HTTP API 服务配置
    CORTEX_HTTP_HOST: str = "0.0.0.0"
    CORTEX_HTTP_PORT: int = 7999
    CORTEX_POOL_SIZE: int = 4
    CORTEX_QUEUE_SIZE: int = 100

    # Chatlog HTTP API 服务配置
    CHATLOG_HTTP_HOST: str = "0.0.0.0"
    CHATLOG_HTTP_PORT: int = 7998

    # 机器人名称列表(@名称 触发,替换为 "a " + 文本,支持多个名称,逗号分隔)
    BOT_NAMES: list[str] = []

    # Daemon 控制器配置
    DAEMON_PORT: int = 8001
    DAEMON_HOST: str = "0.0.0.0"
    DAEMON_RESTART_DELAY: int = 5
    DAEMON_MAX_RETRIES: int = 10
    DAEMON_LOG_BUFFER_LINES: int = 5000

    # 微信数据库解密/总结配置
    WECHAT_DB_INPUT_PATH: str = ""   # 加密数据库输入目录
    WECHAT_DB_OUTPUT_PATH: str = ""  # 解密后输出目录
    WECHAT_DB_KEY: str = ""          # Windows: 64位密钥;Linux: per-db 密钥 JSON 字符串

    # 微信图片 V2 .dat 解密密钥(Windows/Linux), 由 tools/extract_wx_image_key_*.py 派生
    IMAGE_AES_KEY: str = ""          # 16 字节 ASCII (hex 字符串)
    IMAGE_XOR_KEY: int = 0x88        # XOR 字节, 默认 0x88; .env 支持 "0x1a"/"26"/"0o32" 写法

    # 微信表情本地缓存(cache/<YYYY-MM>/Emoticon)解密密钥: AES-128-CBC(iv=key) 全局密钥.
    # 默认值为微信 4.x 内置常量(对收到的表情按 md5 缓存时使用), 个别版本不同可在 .env 覆盖.
    EMOTICON_AES_KEY: str = "4e514cb9da6282d0f70de78089c43f6c"  # 16 字节, hex 字符串(32 hex)

    @field_validator("IMAGE_XOR_KEY", mode="before")
    @classmethod
    def _parse_xor_key(cls, v):
        if isinstance(v, str):
            v = int(v, 0)
        if not isinstance(v, int) or not (0 <= v <= 255):
            raise ValueError(f"IMAGE_XOR_KEY 必须是 0..255 范围的整数, 收到: {v!r}")
        return v

    # Linux 微信 Gateway 配置
    LINUX_WECHAT_DISPLAY: str = ":10"
    LINUX_WECHAT_XAUTHORITY: str = "~/.Xauthority"
    LINUX_CHATLOG_MESSAGE_LIMIT: int = 20
    LINUX_CHATLOG_GROUP_CACHE_TTL: float = 30.0

    # Webhook 配置
    WEBHOOK_POOL_SIZE: int = 5            # Webhook worker 数量
    WEBHOOK_QUEUE_SIZE: int = 100         # Webhook 待处理队列容量
    WEBHOOK_TIMEOUT: int = 10             # 普通 webhook HTTP 超时(秒)
    WEBHOOK_REPLY_TIMEOUT: int = 180      # 回复型 webhook HTTP 超时(秒)
    WEBHOOK_MAX_REPLY_TEXT: int = 1200    # 回复文本最大长度
    WEBHOOK_CACHE_TTL: float = 5.0        # 缓存 TTL(秒)

    # 发送配置
    SEND_QUEUE_SIZE: int = 100            # 微信发送队列容量

    def has_wechat_db_config(self) -> bool:
        """检查微信数据库配置是否完整"""
        return bool(self.WECHAT_DB_INPUT_PATH and self.WECHAT_DB_OUTPUT_PATH and self.WECHAT_DB_KEY)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# 创建全局配置实例
config = Config()
