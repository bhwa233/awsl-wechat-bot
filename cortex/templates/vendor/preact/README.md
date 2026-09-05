# Preact 10.29.8

本地固定版本，无运行时 CDN 请求、无构建步骤。

- 来源：https://www.npmjs.com/package/preact/v/10.29.8
- `preact.module.js`：官方 `dist/preact.module.js`
- `hooks.module.js`：官方 `hooks/dist/hooks.module.js`，保留上游 `preact` 导入
- 去除了指向未附带文件的 source map 注释
- 许可证：同目录 `LICENSE`（MIT）

Hooks 属于 Preact 包，不引入其他框架或模板库。

`index.html` 的本地 Import Map 统一映射 `preact` 和 `preact/hooks`，确保应用与 Hooks 使用同一个核心实例。升级时同时替换两个文件并更新映射中的版本号，无需修改 vendor 导入。
