import sys


def get_login_adapter(adapter, console, *, cancelled=lambda: False):
    if sys.platform == "win32":
        from gateway.startup.adapters.windows import WindowsLoginAdapter

        return WindowsLoginAdapter(adapter, console, cancelled=cancelled)
    if sys.platform.startswith("linux"):
        from gateway.startup.adapters.linux import LinuxLoginAdapter

        return LinuxLoginAdapter(adapter, console, cancelled=cancelled)
    raise RuntimeError("当前平台不支持微信启动看护")
