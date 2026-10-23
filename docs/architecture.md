# 架构与线程模型

[返回 README](../README.md) · [总体架构图](../README.md#总体架构)

## 线程模型

| 线程 | 职责 | 阻塞安全 |
|------|------|----------|
| Gateway scan_loop | 窗口自动发现 (5s) | 独立线程 ✅ |
| Detector (per-group) | OCR/UIA/Chatlog 消息检测 | 独立线程 ✅ |
| VoiceSynthesisWorker | LocalAI / EdgeTTS 合成后回到发送队列 | 独立线程 ✅ |
| Sender | 串行发送 + 全局限速 | 独立线程 ✅ |
| Gateway consume_loop | 队列消费 + WS 广播 | 独立线程 ✅ |
| GatewayClient | async WS 收发 + listener 分发 | **禁止阻塞** ⚠️ |
| Cortex ThreadPool(4) | AI/命令等阻塞操作 | 独立线程 ✅ |
| Webhook worker（默认 5） | 有界队列 + HTTP POST / 可选回复 | 独立线程 ✅ |
| Scheduler | 定时任务轮询 (5s) | 独立线程 ✅ |
| Summary worker | LLM 总结 + 渲染 + 发送 | daemon 线程 ✅ |
| Chatlog 服务 | sync def 路由 (FastAPI 线程池) | 独立进程 ✅ |

`main.py` 为 Gateway、Chatlog、Cortex 各启动一个子进程，并用独立监督线程在异常退出后重启该服务。Daemon 是可选的外部控制入口，负责启动、停止、更新整个 Bot。
