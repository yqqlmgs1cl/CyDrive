---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'eb0d0e53-a64e-468a-a708-931e8060ebc1'
  PropagateID: 'eb0d0e53-a64e-468a-a708-931e8060ebc1'
  ReservedCode1: 'e7bf65e6-130e-4ea2-9165-76b9b2105868'
  ReservedCode2: 'e7bf65e6-130e-4ea2-9165-76b9b2105868'
---

# CyDrive 改进日志

本项目 fork 自 [thecynetx/CyDrive](https://github.com/thecynetx/CyDrive)，在保留原作者功能的基础上，针对国内网络环境和实际使用体验做了以下改进。

## 主要改进

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