---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '8636ae72-0f53-41b1-82be-e084f940bb1e'
  PropagateID: '8636ae72-0f53-41b1-82be-e084f940bb1e'
  ReservedCode1: '3d965b06-be6c-4b2c-aace-cced8fd176d3'
  ReservedCode2: '3d965b06-be6c-4b2c-aace-cced8fd176d3'
---

# CyDrive 改进日志

本项目 fork 自 [thecynetx/CyDrive](https://github.com/thecynetx/CyDrive)，在保留原作者功能的基础上，针对国内网络环境和实际使用体验做了以下改进。

## 主要改进

### 0a. WebDAV 文件名安全化（2026-09 新增）
- 修复部分客户端上传含半角 `? : " < > | * \` 等字符的文件名时后端 500（`OSError(22)`）的问题。
- 典型场景：Windows 上 rclone local 后端读 RaiDrive 等虚拟盘时，会把源端呈现的全角 `？：＂` 还原成半角非法字符再 PUT，原版 CyDrive 直接 `open()` 建缓存文件即崩溃。
- 现在所有本地缓存路径逐级安全化：非法半角字符自动替换为对应全角字符（`?` → `？`），Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）加前缀兜底，结尾空格/点清除。
- 发往 Telegram 的文件名与标题同步安全化，TG 端文件名与虚拟盘显示一致。
- `begin_write` 失败时返回 403 可读错误并附原因，不再返回盲目重试也无法解决的 500。
- 相关文件：`cydrive/cache_manager.py`, `cydrive/telegram_client.py`, `cydrive/webdav_server.py`。

### 0b. 上传队列反压与自动重试（2026-09 新增）
- 反压：WebDAV 写入完成后触发的上传，在途数量 ≥8 时自动等待（最长 600s），防止 rclone/NAS 批量拷贝淹没本地缓存。
- 重试：单文件上传失败自动重试 3 次（间隔 10/20/30s），FloodWait 按服务端要求等待后重试。
- 连接韧性：`TelegramClient` 增加 `connection_retries=10, retry_delay=3, timeout=60, flood_sleep_threshold=120`。
- 相关文件：`cydrive/webdav_server.py`, `cydrive/telegram_client.py`。

### 0. 启动频道验证与双向删除同步（2026-09 新增）
- 启动时用 bot 兼容 API 验证目标频道/群组可达，杜绝 `Could not find the input entity` 首次上传报错。
- 同时支持普通群组 ID（`-` 开头）与超级群组/频道 ID（`-100` 开头）。
- 上传失败时保留本地缓存源文件（原先会被无条件删除）。
- 新增 Telegram 删除事件监听：TG 端"为所有人删除"消息后，虚拟盘对应记录自动清除；数据库层支持按消息 ID 批量删除（含多分块大文件的 chunk 记录）。与原有的"虚拟盘删除同步删 TG 消息"构成双向闭环。

### 1. SOCKS5 代理支持（可配置）
- 将 Telegram MTProto 连接代理从硬编码改为读取 `config.json`。
- 新增配置项：`proxy_type`, `proxy_host`, `proxy_port`, `proxy_username`, `proxy_password`, `proxy_rdns`。
- 默认配置示例使用 `socks5://127.0.0.1:10808`，方便配合 Clash/V2RayN/SSR 等工具翻墙使用。
- 相关文件：`cydrive/config.py`, `cydrive/telegram_client.py`, `config.example.json`。

### 2. WebDAV 端口冲突处理
- 将默认 WebDAV 端口从 `8080` 改为 `6060`，避免与常见本地服务（如 node/前端开发服务器）冲突。
- 若需其他端口，可在 `config.json` 中修改 `webdav_port`。

### 3. 目录/文件重命名支持
- 修复了从资源管理器重命名文件或目录失败的问题。
- 新增 `MetaDatabase.rename_path()`，递归更新目录下所有子文件/子目录的路径和父目录关系。
- 为 `VirtualTelegramFile` 和 `VirtualTelegramFolder` 实现 `handle_move()`。
- 相关文件：`cydrive/database.py`, `cydrive/webdav_server.py`。

### 4. WebDAV 上传状态同步修复
- 修复了从 `T:` 盘拖入文件后，Web 面板一直显示 `Syncing` 的问题。
- 上传成功后，通过 Future 回调确认结果，数据库状态会正确更新为 `is_uploaded=1`。
- 未成功上传的文件现在显示为 `Pending`，不再误导用户。
- 相关文件：`cydrive/webdav_server.py`, `cydrive/telegram_client.py`, `cydrive/web_ui/static/js/app.js`。

### 5. 删除文件同步删除 Telegram 消息
- 修复了从 `T:` 盘或 Web 面板删除文件后，TG 频道里文件消息仍然存在的问题。
- 删除时先删除关联的 Telegram 消息（含分块文件的所有消息），再删除本地数据库记录。
- 目录删除会递归删除目录下所有文件对应的 TG 消息。
- 相关文件：`cydrive/telegram_client.py`, `cydrive/webdav_server.py`, `cydrive/web_ui/app.py`, `cydrive/database.py`。

### 6. Web 面板删除按钮修复
- 修复了面板上删除按钮点击无效的问题。
- 统一处理带 `/` 和不带 `/` 的路径格式，确保能正确匹配数据库记录。
- 增加调试日志，便于排查删除异常。

### 7. Web 面板媒体预览关闭修复
- 修复了高分辨率视频播放时，关闭按钮被顶出视口无法点击的问题。
- 关闭按钮改为固定定位在浏览器窗口右上角，不再随内容滚动。
- 限制视频/图片最大高度为 `75vh`，避免撑满屏幕。
- 增加点击模态框背景和按 `Esc` 键关闭的功能。
- 相关文件：`cydrive/web_ui/templates/index.html`, `cydrive/web_ui/static/css/style.css`, `cydrive/web_ui/static/js/app.js`。

### 8. 配置示例更新
- `config.example.json` 补充了代理相关配置项示例。

## 使用提示

- 首次使用请复制 `config.example.json` 为 `config.json`，填入自己的 `bot_token` 和 `chat_id`。
- `chat_id` 必须是 bot 已加入的频道/群组 ID。超级群组/频道 ID 通常以 `-100` 开头。
- 如果不需要代理，可将 `proxy_type` 设为 `null`。

## 致谢

- 原项目：[thecynetx/CyDrive](https://github.com/thecynetx/CyDrive)
- 原作者：[Cynet Security Team](https://cynetx.ir)

> AI生成