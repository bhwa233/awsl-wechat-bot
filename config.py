"""
AWSL 微信机器人配置 - 使用 Pydantic Settings
支持从环境变量读取配置
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """配置类 - 自动从环境变量读取"""

    # 群聊名称（macOS 单群模式使用）
    GROUP_NAME: str = ""

    # 触发关键词
    TRIGGER_KEYWORD: str = "awsl"

    # OCR 自定义词汇表
    OCR_CUSTOM_WORDS: list[str] = ["awsl"]

    # API 地址
    API_URL: str = "https://awsl.api.awsl.icu/v2/random_json"

    # 命令 API 地址
    COMMAND_API_BASE_URL: str = "https://your-api-domain.com/api"

    # 检查消息间隔（秒）
    CHECK_INTERVAL: int = 3

    # 触发冷却时间（秒）- 群级冷却
    TRIGGER_COOLDOWN: int = 3

    # 并发处理配置
    MAX_CONCURRENT_TASKS: int = 5       # 线程池大小
    GLOBAL_SEND_INTERVAL: float = 10.0  # 全局发送间隔（秒）

    # 调试模式（输出每次检测到的所有消息）
    DEBUG: bool = False

    # AppleScript 超时配置（秒）
    APPLESCRIPT_TIMEOUT_SHORT: int = 10   # 简单操作: 窗口位置、图片复制
    APPLESCRIPT_TIMEOUT_MEDIUM: int = 20  # 中等操作: 消息获取
    APPLESCRIPT_TIMEOUT_LONG: int = 30    # 复杂操作: 深度UI遍历、窗口激活

    # 截图区域参数（暂时不使用，Accessibility API 不需要）
    SCREENSHOT_LEFT_RATIO: float = 0.30
    SCREENSHOT_TOP_RATIO: float = 0.06
    SCREENSHOT_WIDTH_RATIO: float = 0.65
    SCREENSHOT_HEIGHT_RATIO: float = 0.75

    # OpenAI 配置（从环境变量读取）
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_MAX_TOKENS: int = 500
    OPENAI_TEMPERATURE: float = 0.7

    # HTTP API 服务配置（默认启用）
    HTTP_API_HOST: str = "0.0.0.0"
    HTTP_API_PORT: int = 8000
    HTTP_API_TOKEN: str = ""  # Bearer Token 认证，为空则不启用认证
    HTTP_API_BASE_URL: str = "http://localhost:8000"  # 内部回调地址

    # 机器人名称列表（@名称 触发，替换为 "a " + 文本，支持多个名称，逗号分隔）
    BOT_NAMES: list[str] = []

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
