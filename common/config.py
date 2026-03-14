"""
AWSL 微信机器人配置 - 使用 Pydantic Settings
支持从环境变量读取配置
"""

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
    COMMAND_API_BASE_URL: str = "https://your-api-domain.com/api"

    # 检查消息间隔（秒）
    CHECK_INTERVAL: int = 1

    # 触发限速 - 每个窗口每分钟最多触发次数
    TRIGGER_RATE_LIMIT: int = 10
    TRIGGER_RATE_WINDOW: int = 60  # 限速时间窗口（秒）

    # 发送间隔配置
    GLOBAL_SEND_INTERVAL: float = 3.0  # 全局发送间隔（秒）

    # 调试模式（输出每次检测到的所有消息）
    DEBUG: bool = False

    # AppleScript 超时配置（秒）
    APPLESCRIPT_TIMEOUT_SHORT: int = 10   # 简单操作: 窗口位置、图片复制
    APPLESCRIPT_TIMEOUT_MEDIUM: int = 20  # 中等操作: 消息获取
    APPLESCRIPT_TIMEOUT_LONG: int = 30    # 复杂操作: 深度UI遍历、窗口激活

    # OCR 截图区域参数（弹出窗口只用 TOP/HEIGHT，主窗口全用）
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

    # OpenAI 配置（从环境变量读取）
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_MAX_TOKENS: int = 500
    OPENAI_TEMPERATURE: float = 0.7

    # Gateway HTTP API 服务配置
    HTTP_API_HOST: str = "0.0.0.0"
    HTTP_API_PORT: int = 8000
    HTTP_API_TOKEN: str = ""  # Bearer Token 认证，为空则不启用认证
    HTTP_API_BASE_URL: str = "http://localhost:8000"  # 内部回调地址

    # Handler HTTP API 服务配置
    CORTEX_HTTP_HOST: str = "0.0.0.0"
    CORTEX_HTTP_PORT: int = 7999

    # 机器人名称列表（@名称 触发，替换为 "a " + 文本，支持多个名称，逗号分隔）
    BOT_NAMES: list[str] = []

    # Daemon 控制器配置
    DAEMON_PORT: int = 8001
    DAEMON_HOST: str = "0.0.0.0"
    DAEMON_RESTART_DELAY: int = 5
    DAEMON_MAX_RETRIES: int = 10
    DAEMON_LOG_BUFFER_LINES: int = 5000
    TTYD_HOST: str = "0.0.0.0"
    TTYD_PORT: int = 7681
    TTYD_CREDENTIALS: str = ""

    # 微信数据库解密/总结配置
    WECHAT_DB_INPUT_PATH: str = ""   # 加密数据库输入目录
    WECHAT_DB_OUTPUT_PATH: str = ""  # 解密后输出目录
    WECHAT_DB_KEY: str = ""          # 数据库解密密钥

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
