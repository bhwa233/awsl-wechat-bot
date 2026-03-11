const WebSocket = require("ws");

module.exports = {
  name: "awsl-gateway",
  async setup(api) {
    const wsUrl = api.config.wsUrl;
    const groupFilter = api.config.groupFilter || "";
    const messageFilter = api.config.messageFilter || "";
    let ws;

    function connect() {
      ws = new WebSocket(wsUrl);

      ws.on("open", () => {
        ws.send(
          JSON.stringify({
            type: "subscribe",
            group_filter: groupFilter,
            message_filter: messageFilter,
          })
        );
        console.log("[awsl-gateway] 已连接并订阅");
      });

      ws.on("message", (raw) => {
        const data = JSON.parse(raw);
        if (data.type === "message") {
          api.dispatch({
            channelId: data.group_name,
            senderId: data.group_name,
            text: data.content,
          });
        }
      });

      ws.on("close", () => {
        console.log("[awsl-gateway] 断开，2s 后重连");
        setTimeout(connect, 2000);
      });

      ws.on("error", (err) => {
        console.error("[awsl-gateway] WS 错误:", err.message);
      });
    }

    connect();

    api.onReply(async (msg) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "send_message",
            group_name: msg.channelId,
            message: msg.text,
          })
        );
      }
    });
  },
};
