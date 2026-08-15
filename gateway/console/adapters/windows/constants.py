import re

_WECHAT_WINDOW_CLASSES = {"mmui::MainWindow", "mmui::ChatSingleWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_CLASSES = {"mmui::MainWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}
_AT_PREFIX_RE = re.compile(r'^((?:@[^\s@,，;；:：]+\s+)+)(.*)', re.DOTALL)
_AT_POPOVER_TIMEOUT = 0.5
_AT_POPOVER_GONE_TIMEOUT = 0.3
_AT_POPOVER_INTERVAL = 0.05
_STATE_MAX_DEPTH = 22
_CHAT_SCROLL_WHEEL_TIMES = 5
