import os
import asyncio
import time
from typing import Optional, Callable, Dict, Any, List
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument
from telethon.errors import FloodWaitError, AccessTokenInvalidError, ApiIdInvalidError

from cydrive.config import CyDriveConfig
from cydrive.database import MetaDatabase
from cydrive.chunker import FileChunker
from cydrive.cache_manager import sanitize_filename_component

class TelegramSyncEngine:
    """Telethon MTProto Engine for fast uploads, downloads, and bot commands."""

    def __init__(self, config: CyDriveConfig, db: MetaDatabase):
        self.config = config
        self.db = db
        self.client = self._create_telegram_client()
        self.is_connected = False
        self.loop = self.client.loop
        self.on_file_received_callback: Optional[Callable] = None

    def _create_telegram_client(self) -> TelegramClient:
        """Create TelegramClient with optional proxy from config."""
        proxy = None
        if getattr(self.config, "proxy_type", None):
            proxy_type = self.config.proxy_type.lower().strip()
            host = getattr(self.config, "proxy_host", "127.0.0.1") or "127.0.0.1"
            port = getattr(self.config, "proxy_port", 10808) or 10808
            rdns = getattr(self.config, "proxy_rdns", True)
            username = getattr(self.config, "proxy_username", None)
            password = getattr(self.config, "proxy_password", None)

            if username and password:
                proxy = (proxy_type, host, int(port), bool(rdns), username, password)
            else:
                proxy = (proxy_type, host, int(port), bool(rdns))

        return TelegramClient(
            "cynet_bot_session",
            self.config.api_id,
            self.config.api_hash,
            proxy=proxy,
            connection_retries=10,
            retry_delay=3,
            timeout=60,
            flood_sleep_threshold=120
        )

    async def start(self):
        """Starts the Telethon client with bot token."""
        try:
            self.loop = asyncio.get_running_loop()
            await self.client.start(bot_token=self.config.bot_token)
            self.is_connected = True
            self._register_handlers()

            # 预热实体缓存：bot 账号无法调用 GetDialogs（用户账号专属）。
            # 超级群组/频道(-100开头)用 GetChannelsRequest（bot 允许 access_hash=0 查询）；
            # 普通群组(-开头但非-100)直接 get_entity 即可。
            try:
                chat_id_int = int(self.config.chat_id)
                if chat_id_int <= -1000000000000:
                    # -100 开头：超级群组/频道，剥离 -100 前缀得到原始 channel_id
                    raw_channel_id = abs(chat_id_int) - 10**12
                    from telethon.tl.types import InputChannel
                    from telethon.tl.functions.channels import GetChannelsRequest
                    result = await self.client(GetChannelsRequest(channel=[InputChannel(raw_channel_id, 0)]))
                    if result.chats:
                        title = result.chats[0].title or str(raw_channel_id)
                        print(f"🎯 [Telegram] Target chat verified: {title} ({self.config.chat_id})")
                    else:
                        raise ValueError("channel not found in response")
                elif chat_id_int < -1000000000000 or chat_id_int < 0:
                    # 普通群组（-开头，非-100前缀）或用户
                    entity = await self.client.get_entity(self.config.chat_id)
                    title = getattr(entity, "title", None) or str(entity.id)
                    print(f"🎯 [Telegram] Target chat verified: {title} ({self.config.chat_id})")
                else:
                    entity = await self.client.get_entity(self.config.chat_id)
                    title = getattr(entity, "title", None) or str(entity.id)
                    print(f"🎯 [Telegram] Target chat verified: {title} ({self.config.chat_id})")
            except Exception as e:
                self.is_connected = False
                print(f"❌ [Telegram Error] Cannot access target chat {self.config.chat_id}: {e}")
                print("   请检查: 1) 新 bot 是否已加入该频道（需以管理员身份把 bot 添加进频道）")
                print("           2) chat_id 是否就是该频道的 -100 开头 ID")
                print("           3) 若刚把 bot 拉进频道，重启一次程序让其缓存频道信息")

            me = await self.client.get_me()
            print(f"🤖 [Telegram] Connected as @{me.username} (ID: {me.id})")
        except AccessTokenInvalidError:            print("❌ [Telegram Error] The provided Bot Token is invalid! Run `python main.py setup` to update.")
        except ApiIdInvalidError:
            print("❌ [Telegram Error] Invalid Telegram API ID/Hash.")
        except Exception as e:
            print(f"⚠️ [Telegram Warning] Could not connect to Telegram MTProto: {e}")

    def _register_handlers(self):
        """Registers message handlers and bot commands."""
        
        # 1. Handle incoming files from Telegram
        @self.client.on(events.NewMessage(chats=self.config.chat_id))
        async def handle_incoming(event):
            if event.message and event.message.media:
                await self._process_incoming_file(event)
            elif event.message and event.message.text:
                await self._process_bot_command(event)

        # 2. Handle deleted messages from Telegram (best-effort sync)
        # 注意：Telegram 对 bot 的删除事件通知范围有限（通常仅限 bot 自己发的消息），
        # 用户在客户端删除自己上传的文件时 bot 可能收不到通知，此监听仅覆盖能收到的部分。
        @self.client.on(events.MessageDeleted())
        async def handle_deleted(event):
            try:
                deleted_ids = set(event.deleted_ids or [])
                if not deleted_ids:
                    return
                removed = self.db.delete_by_msg_ids(deleted_ids)
                if removed:
                    print(f"🗑️ [TG Delete Sync] Removed {removed} local record(s) matching deleted Telegram messages: {sorted(deleted_ids)}")
            except Exception as e:
                print(f"⚠️ [TG Delete Sync] Error handling deleted messages: {e}")

    async def _process_incoming_file(self, event):
        """Indexes incoming Telegram media metadata without downloading payload to disk (Pure Virtual Cloud)."""
        msg = event.message
        file_name = None
        file_size = 0
        mime_type = None

        if hasattr(msg, "file") and msg.file:
            file_name = getattr(msg.file, "name", None)
            file_size = getattr(msg.file, "size", 0) or 0
            mime_type = getattr(msg.file, "mime_type", None)

        if not file_name:
            ext = getattr(msg.file, "ext", ".bin") if hasattr(msg, "file") else ".bin"
            file_name = f"Telegram_File_{msg.id}{ext}"

        rel_path = f"/{file_name}"
        
        print(f"☁️ [PURE CLOUD VFS] Indexed Telegram cloud file: {file_name} ({file_size // 1024} KB) - [0 Bytes Local Disk]")
        
        self.db.upsert_file(
            rel_path=rel_path,
            name=file_name,
            parent_dir="/",
            size=file_size,
            mtime=time.time(),
            telegram_msg_id=msg.id,
            is_uploaded=True,
            is_cached=False,
            mime_type=mime_type
        )

        try:
            size_mb = file_size / (1024 * 1024)
            size_str = f"{size_mb / 1024:.2f} GB" if size_mb >= 1024 else f"{size_mb:.2f} MB"
            await event.respond(f"☁️ File **'{file_name}'** ({size_str}) is now instantly available in your Virtual Drive ({self.config.drive_letter}) without using your local hard drive space!")
        except Exception:
            pass

    async def _process_bot_command(self, event):
        """Processes interactive bot commands from the user."""
        text = event.message.text.strip()
        
        if text.startswith("/start") or text.startswith("/help"):
            help_text = (
                "🚀 **CyDrive Cloud Storage Engine v2.0**\n"
                "Developed by Cynet Security Team (https://cynetx.ir)\n\n"
                "**Available Commands:**\n"
                "📊 `/stats` - View cloud storage analytics\n"
                "🔍 `/search <query>` - Search files in your drive\n"
                "📥 `/get <filename>` - Download a file directly\n"
                "ℹ️ Send any file to this chat to save it to your Windows Drive!"
            )
            await event.respond(help_text)

        elif text.startswith("/stats"):
            stats = self.db.get_stats()
            size_mb = stats["total_bytes"] / (1024 * 1024)
            size_gb = size_mb / 1024
            
            size_str = f"{size_gb:.2f} GB" if size_gb >= 1 else f"{size_mb:.2f} MB"
            stats_text = (
                "📊 **CyDrive Storage Statistics**\n\n"
                f"📁 **Total Files:** `{stats['total_files']}`\n"
                f"🗂️ **Total Folders:** `{stats['total_dirs']}`\n"
                f"☁️ **Total Cloud Storage:** `{size_str}`\n"
                f"✅ **Synced Files:** `{stats['uploaded_files']}`\n"
                f"🖥️ **Mapped Windows Drive:** `{self.config.drive_letter}`"
            )
            await event.respond(stats_text)

        elif text.startswith("/search"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.respond("⚠️ Please specify a search keyword. e.g. `/search document.pdf`")
                return
            query = parts[1]
            results = self.db.search_files(query)
            if not results:
                await event.respond(f"🔍 No files found matching: `{query}`")
            else:
                lines = ["🔍 **Search Results:**\n"]
                for item in results[:15]:
                    icon = "📁" if item["is_dir"] else "📄"
                    size_kb = item["size"] // 1024
                    lines.append(f"{icon} `{item['name']}` ({size_kb} KB)")
                await event.respond("\n".join(lines))

    async def upload_file(self, local_path: str, rel_path: str, progress_callback: Optional[Callable] = None, delete_source: bool = False, max_retries: int = 3) -> Optional[int]:
        """Uploads a local file to Telegram, supporting large file chunking (>2GB) and AES encryption.
        Failed uploads are retried up to max_retries times with increasing delay."""
        last_error = None
        for attempt in range(1, max_retries + 1):
            result = await self._upload_file_once(local_path, rel_path, progress_callback, delete_source)
            if result is not None:
                if attempt > 1:
                    print(f"✅ [Retry Success] {rel_path} uploaded on attempt {attempt}/{max_retries}")
                return result
            last_error = f"attempt {attempt}/{max_retries} returned no msg_id"
            if attempt < max_retries:
                wait_seconds = 10 * attempt
                print(f"⏳ [Upload Retry] {os.path.basename(rel_path)} failed ({last_error}). Retrying in {wait_seconds}s...")
                await asyncio.sleep(wait_seconds)
        print(f"❌ [Upload Failed Permanently] {rel_path} after {max_retries} attempts. Last error: {last_error}")
        return None

    async def _upload_file_once(self, local_path: str, rel_path: str, progress_callback: Optional[Callable] = None, delete_source: bool = False) -> Optional[int]:
        """Single upload attempt (called by upload_file with retry logic)."""
        self._upload_succeeded = False
        if not os.path.exists(local_path):
            return None

        from cydrive.crypto import CyCrypto
        from cydrive.chunker import FileChunker
        import tempfile
        import shutil

        file_size = os.path.getsize(local_path)
        clean_rel = "/" + rel_path.strip("/").replace("\\", "/")
        # 发往 Telegram 的文件名同样需要安全化（TG 允许 ? : 但保持与本地缓存/T: 盘显示一致）
        file_name = sanitize_filename_component(os.path.basename(clean_rel))
        if not file_name:
            file_name = os.path.basename(local_path)

        parent = "/" + os.path.dirname(clean_rel).strip("/").replace("\\", "/") if os.path.dirname(clean_rel).strip("/").replace("\\", "/") else "/"
        sha256 = FileChunker.calculate_sha256(local_path) if file_size <= 100 * 1024 * 1024 else None

        # Check if encryption is enabled
        upload_path = local_path
        is_encrypted = False
        temp_encrypted_file = None

        if self.config.enable_encryption and self.config.encryption_password:
            try:
                crypto = CyCrypto(self.config.encryption_password)
                temp_fd, temp_encrypted_file = tempfile.mkstemp(prefix="cydrive_enc_")
                os.close(temp_fd)
                crypto.encrypt_file(local_path, temp_encrypted_file)
                upload_path = temp_encrypted_file
                is_encrypted = True
                print(f"🔐 [AES-256-GCM] Encrypted {file_name} before cloud transmission.")
            except Exception as e:
                print(f"⚠️ [Encryption Warning] Encryption failed: {e}. Uploading standard.")

        try:
            # Check if file requires chunking (> chunk_size_mb)
            if FileChunker.needs_chunking(upload_path, self.config.chunk_size_mb):
                chunk_dir = tempfile.mkdtemp(prefix="cydrive_chunks_")
                try:
                    chunks = FileChunker.split_file(upload_path, chunk_dir, self.config.chunk_size_mb)
                    chunk_count = len(chunks)
                    print(f"🧩 [Large File Chunking] Split {file_name} ({file_size // (1024*1024)} MB) into {chunk_count} parts.")

                    # Insert parent record in DB
                    file_id = self.db.upsert_file(
                        rel_path=clean_rel,
                        name=file_name,
                        parent_dir=parent,
                        size=file_size,
                        mtime=time.time(),
                        sha256=sha256,
                        is_uploaded=False,
                        is_cached=False,
                        is_encrypted=is_encrypted,
                        chunk_count=chunk_count
                    )

                    first_msg_id = None
                    for idx, chunk_part in enumerate(chunks):
                        part_name = f"{file_name}.part{idx:03d}"
                        part_size = os.path.getsize(chunk_part)
                        part_caption = (
                            f"🚀 **CyDrive Multi-Part Cloud Archive**\n"
                            f"📁 File: `{clean_rel}`\n"
                            f"🧩 Part: `{idx + 1}/{chunk_count}` ({part_size // 1024} KB)"
                        )

                        msg = await self.client.send_file(
                            self.config.chat_id,
                            chunk_part,
                            caption=part_caption,
                            file_name=part_name,
                            progress_callback=progress_callback
                        )

                        if idx == 0:
                            first_msg_id = msg.id

                        self.db.upsert_chunk(
                            file_id=file_id,
                            chunk_index=idx,
                            telegram_msg_id=msg.id,
                            size=part_size
                        )

                    # Update parent file as fully uploaded
                    self.db.upsert_file(
                        rel_path=clean_rel,
                        name=file_name,
                        parent_dir=parent,
                        size=file_size,
                        mtime=time.time(),
                        sha256=sha256,
                        telegram_msg_id=first_msg_id,
                        is_uploaded=True,
                        is_cached=False,
                        is_encrypted=is_encrypted,
                        chunk_count=chunk_count
                    )

                    print(f"✅ [CLOUD UPLOAD] Successfully uploaded all {chunk_count} parts of {file_name} to Telegram Cloud!")
                    self._upload_succeeded = True
                    return first_msg_id
                finally:
                    shutil.rmtree(chunk_dir, ignore_errors=True)

            # Standard single-file upload
            caption = (
                f"🚀 **CyDrive Cloud Backup**\n"
                f"📁 Path: `{clean_rel}`\n"
                f"📦 Size: `{file_size // 1024} KB`" + 
                (" (🔒 AES Encrypted)" if is_encrypted else "")
            )

            msg = await self.client.send_file(
                self.config.chat_id,
                upload_path,
                caption=caption,
                file_name=file_name,
                progress_callback=progress_callback
            )
            
            # Record in SQLite VFS
            self.db.upsert_file(
                rel_path=clean_rel,
                name=file_name,
                parent_dir=parent,
                size=file_size,
                mtime=time.time(),
                sha256=sha256,
                telegram_msg_id=msg.id,
                is_uploaded=True,
                is_cached=False,
                is_encrypted=is_encrypted,
                chunk_count=1
            )
            print(f"✅ [CLOUD UPLOAD] Successfully uploaded {file_name} ({file_size // 1024} KB) to Telegram Cloud!")
            self._upload_succeeded = True
            return msg.id

        except FloodWaitError as e:
            print(f"⏳ [Telegram Rate Limit] FloodWait for {e.seconds}s. Auto-waiting...")
            await asyncio.sleep(e.seconds)
            return await self._upload_file_once(local_path, rel_path, progress_callback, delete_source)
        except Exception as e:
            print(f"❌ [Telegram Upload Error] Failed to upload {file_name}: {e}")
            # 上传失败时保留本地缓存文件，避免源文件被误删（成功时由 delete_source 决定是否删除）
            return None
        finally:
            # Clean up temp encrypted file if created
            if temp_encrypted_file and os.path.exists(temp_encrypted_file):
                try:
                    os.remove(temp_encrypted_file)
                except OSError:
                    pass

            # 仅在明确要求且上传成功时删除本地源文件
            if delete_source and os.path.exists(local_path) and self._upload_succeeded:
                try:
                    os.remove(local_path)
                    print(f"🧹 [Zero-Disk Storage] Local temporary buffer deleted. 0 Bytes used on your hard drive.")
                except OSError:
                    pass

    async def delete_file_messages(self, file_record: Dict[str, Any]) -> bool:
        """Delete Telegram message(s) associated with a file record."""
        file_id = file_record.get("id")
        chunk_count = file_record.get("chunk_count", 1) or 1
        msg_ids = set()

        if chunk_count > 1 and file_id:
            chunks = self.db.get_chunks_by_file_id(file_id)
            for chunk in chunks:
                if chunk.get("telegram_msg_id"):
                    msg_ids.add(chunk["telegram_msg_id"])

        main_msg_id = file_record.get("telegram_msg_id")
        if main_msg_id:
            msg_ids.add(main_msg_id)

        if not msg_ids:
            return True

        try:
            await self.client.delete_messages(self.config.chat_id, list(msg_ids))
            print(f"🗑️ [Telegram] Deleted {len(msg_ids)} message(s) for {file_record.get('name')}")
            return True
        except Exception as e:
            print(f"⚠️ [Telegram Delete Warning] Could not delete messages {msg_ids}: {e}")
            return False

    async def download_file_by_id(self, msg_id: int, dest_path: str, progress_callback: Optional[Callable] = None) -> bool:
        """Downloads a specific message media by its Telegram message ID."""
        try:
            msg = await self.client.get_messages(self.config.chat_id, ids=msg_id)
            if msg and msg.media:
                await msg.download_media(file=dest_path, progress_callback=progress_callback)
                return True
        except FloodWaitError as e:
            print(f"⏳ [Telegram Rate Limit] FloodWait for {e.seconds}s. Auto-waiting...")
            await asyncio.sleep(e.seconds)
            return await self.download_file_by_id(msg_id, dest_path, progress_callback)
        except Exception as e:
            print(f"❌ [Telegram Download Error] Failed to download msg {msg_id}: {e}")
        return False

    async def download_file_record(self, file_record: Dict[str, Any], dest_path: str, progress_callback: Optional[Callable] = None) -> bool:
        """Downloads and reconstitutes a file record (handling multi-part chunks and AES decryption)."""
        import tempfile
        import shutil
        from cydrive.crypto import CyCrypto
        from cydrive.chunker import FileChunker

        chunk_count = file_record.get("chunk_count", 1) or 1
        file_id = file_record.get("id")
        is_encrypted = bool(file_record.get("is_encrypted", 0))

        target_dest = dest_path if not is_encrypted else dest_path + ".enc_tmp"
        os.makedirs(os.path.dirname(os.path.abspath(target_dest)), exist_ok=True)

        success = False

        if chunk_count > 1 and file_id:
            chunks = self.db.get_chunks_by_file_id(file_id)
            if chunks and len(chunks) == chunk_count:
                temp_chunk_dir = tempfile.mkdtemp(prefix="cydrive_dl_chunks_")
                try:
                    chunk_paths = []
                    all_chunks_ok = True
                    for chunk in chunks:
                        part_file = os.path.join(temp_chunk_dir, f"part_{chunk['chunk_index']:03d}.tmp")
                        ok = await self.download_file_by_id(chunk["telegram_msg_id"], part_file, progress_callback)
                        if not ok or not os.path.exists(part_file):
                            all_chunks_ok = False
                            break
                        chunk_paths.append(part_file)

                    if all_chunks_ok:
                        FileChunker.merge_chunks(chunk_paths, target_dest)
                        success = True
                finally:
                    shutil.rmtree(temp_chunk_dir, ignore_errors=True)
            else:
                # Fallback to downloading single first message
                msg_id = file_record.get("telegram_msg_id")
                if msg_id:
                    success = await self.download_file_by_id(msg_id, target_dest, progress_callback)
        else:
            msg_id = file_record.get("telegram_msg_id")
            if msg_id:
                success = await self.download_file_by_id(msg_id, target_dest, progress_callback)

        if success and is_encrypted and self.config.encryption_password:
            try:
                crypto = CyCrypto(self.config.encryption_password)
                crypto.decrypt_file(target_dest, dest_path)
                if os.path.exists(target_dest) and target_dest != dest_path:
                    os.remove(target_dest)
                return True
            except Exception as e:
                print(f"❌ [Decryption Error] Failed to decrypt {dest_path}: {e}")
                return False

        return success
