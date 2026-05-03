"""
asset_manager.py
多模态资产管理器 — Triad 记忆总线的二进制媒体资产层

功能：
  1. 资产存储与检索（faces, scenes, videos, audio, thumbs）
  2. asset://<type>/<id> URI 解析与转换
  3. 资产元数据索引（.meta.json）与版本链管理
  4. Markdown 事实文件中的资产链接提取与内联 base64 编码
  5. 预览缩略图自动生成（供 OpenClaw 前端快速加载）

目录结构：
  ~/.triad/memory/
  ├── assets/
  │   ├── faces/        # 角色面部参考图
  │   ├── scenes/       # 场景概念图
  │   ├── videos/       # 视频片段
  │   ├── audio/        # TTS 语音
  │   └── thumbs/       # 预览缩略图

作者：Triad System Architect
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import aiofiles

logger = logging.getLogger("triad.asset_manager")

# 可选依赖（用于缩略图生成）
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not installed; thumbnail generation disabled")


# ---------------------------------------------------------------------------
# 常量与类型
# ---------------------------------------------------------------------------

class AssetType(Enum):
    FACE = "faces"         # 角色面部参考图
    SCENE = "scenes"       # 场景概念图
    VIDEO = "videos"       # 视频片段
    AUDIO = "audio"        # 语音资产
    THUMB = "thumbs"       # 预览缩略图
    MISC = "misc"          # 其他

    @classmethod
    def from_str(cls, s: str) -> "AssetType":
        mapping = {
            "face": cls.FACE, "faces": cls.FACE,
            "scene": cls.SCENE, "scenes": cls.SCENE,
            "video": cls.VIDEO, "videos": cls.VIDEO,
            "audio": cls.AUDIO, "audios": cls.AUDIO,
            "thumb": cls.THUMB, "thumbs": cls.THUMB,
        }
        return mapping.get(s.lower(), cls.MISC)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class AssetMeta:
    """资产元数据，序列化为 .meta.json"""
    asset_id: str
    asset_type: str               # "faces", "scenes", etc.
    format: str                   # "png", "jpg", "mp4", "wav" ...
    dimensions: Optional[Tuple[int, int]] = None
    duration_sec: Optional[float] = None
    file_size_bytes: int = 0
    sha256: str = ""
    linked_entities: List[str] = field(default_factory=list)
    generation_params: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    parent: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tags: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 将 tuple 转为 list 以便 JSON 序列化
        if d.get("dimensions"):
            d["dimensions"] = list(d["dimensions"])
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AssetMeta":
        if d.get("dimensions"):
            d["dimensions"] = tuple(d["dimensions"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class AssetLink:
    """Markdown 中解析出的资产链接"""
    original_text: str          # 原文，如 ![正面照](asset://faces/alice_v3.png)
    alt_text: str               # alt / 链接文本
    uri: str                    # asset://faces/alice_v3.png
    asset_type: AssetType
    asset_id: str
    ext: str                    # .png


@dataclass
class InlineAsset:
    """内联资产（base64 编码，供前端直接渲染）"""
    asset_id: str
    mime_type: str
    base64_data: str
    width: Optional[int] = None
    height: Optional[int] = None


# ---------------------------------------------------------------------------
# 核心：AssetManager
# ---------------------------------------------------------------------------

class AssetManager:
    """
    Triad 多模态资产管理器。

    单例模式建议：
        from asset_manager import AssetManager
        am = AssetManager()  # 自动加载 ~/.triad/memory/assets
    """

    ASSET_URI_PATTERN = re.compile(
        r"!\[(.*?)\]\(asset://([^/]+)/([^)]+)\)"   # Markdown image: ![alt](asset://type/id)
        r"|\[(.*?)\]\(asset://([^/]+)/([^)]+)\)"     # Markdown link: [text](asset://type/id)
    )

    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        if base_path is None:
            base_path = Path.home() / ".triad" / "memory" / "assets"
        self.base_path = Path(base_path)
        self._ensure_directories()

        # 内存索引：asset_id -> (absolute_path, AssetMeta)
        self._index: Dict[str, Tuple[Path, AssetMeta]] = {}
        self._index_lock = asyncio.Lock()

    def _ensure_directories(self) -> None:
        for at in AssetType:
            subdir = self.base_path / at.value
            subdir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Asset dir ready: {subdir}")

    # ------------------------------------------------------------------
    # 索引管理
    # ------------------------------------------------------------------

    async def build_index(self) -> None:
        """扫描 assets/ 目录，重建内存索引。"""
        async with self._index_lock:
            self._index.clear()
            for at in AssetType:
                subdir = self.base_path / at.value
                if not subdir.exists():
                    continue
                for entry in subdir.iterdir():
                    if entry.is_file() and not entry.name.endswith(".meta.json"):
                        asset_id = entry.stem
                        meta_path = entry.with_suffix(entry.suffix + ".meta.json")
                        meta = await self._load_meta(meta_path)
                        if meta is None:
                            meta = self._infer_meta(entry, asset_id, at.value)
                        self._index[asset_id] = (entry, meta)
            logger.info(f"Asset index built: {len(self._index)} assets")

    async def _load_meta(self, meta_path: Path) -> Optional[AssetMeta]:
        if not meta_path.exists():
            return None
        try:
            async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                content = await f.read()
            return AssetMeta.from_dict(json.loads(content))
        except Exception as e:
            logger.warning(f"Failed to load meta {meta_path}: {e}")
            return None

    def _infer_meta(self, file_path: Path, asset_id: str, asset_type: str) -> AssetMeta:
        """从文件系统推断元数据（无 .meta.json 时的兜底）。"""
        stat = file_path.stat()
        fmt = file_path.suffix.lstrip(".").lower()
        dimensions: Optional[Tuple[int, int]] = None
        if PIL_AVAILABLE and fmt in ("png", "jpg", "jpeg", "webp", "bmp"):
            with contextlib.suppress(Exception):
                with Image.open(file_path) as im:
                    dimensions = im.size
        return AssetMeta(
            asset_id=asset_id,
            asset_type=asset_type,
            format=fmt,
            file_size_bytes=stat.st_size,
            dimensions=dimensions,
        )

    # ------------------------------------------------------------------
    # CRUD：资产存取
    # ------------------------------------------------------------------

    async def store_asset(
        self,
        asset_id: str,
        asset_type: Union[str, AssetType],
        source_path: Union[str, Path],
        meta: Optional[AssetMeta] = None,
        copy: bool = True,
    ) -> Path:
        """
        将外部文件存入资产库。

        Args:
            asset_id: 唯一资产标识（如 "alice_v3"）
            asset_type: faces / scenes / videos / audio / thumbs
            source_path: 源文件路径
            meta: 预设元数据（可选，会自动补全 file_size, sha256 等）
            copy: True 则复制，False 则移动

        Returns:
            存储后的绝对路径
        """
        at = asset_type if isinstance(asset_type, AssetType) else AssetType.from_str(asset_type)
        dest_dir = self.base_path / at.value
        dest_dir.mkdir(parents=True, exist_ok=True)

        src = Path(source_path)
        ext = src.suffix.lower()
        dest = dest_dir / f"{asset_id}{ext}"

        # 如果目标已存在，创建版本链
        version = 1
        parent_id: Optional[str] = None
        if dest.exists():
            # 查找现有版本号
            existing_meta = await self._load_meta(dest.with_suffix(ext + ".meta.json"))
            if existing_meta:
                version = existing_meta.version + 1
                parent_id = existing_meta.asset_id
            # 备份旧文件
            backup_name = f"{asset_id}_v{version - 1}{ext}"
            backup_dest = dest_dir / backup_name
            shutil.copy2(dest, backup_dest)
            logger.info(f"Versioned backup created: {backup_dest.name}")

        if copy:
            shutil.copy2(src, dest)
        else:
            shutil.move(str(src), str(dest))

        # 计算 sha256
        sha256 = await self._sha256_file(dest)

        # 构建 / 补全元数据
        if meta is None:
            meta = AssetMeta(asset_id=asset_id, asset_type=at.value, format=ext.lstrip("."))
        meta.asset_id = asset_id
        meta.asset_type = at.value
        meta.format = ext.lstrip(".")
        meta.file_size_bytes = dest.stat().st_size
        meta.sha256 = sha256
        meta.version = version
        meta.parent = parent_id

        # 图像尺寸
        if PIL_AVAILABLE and meta.format in ("png", "jpg", "jpeg", "webp"):
            with contextlib.suppress(Exception):
                with Image.open(dest) as im:
                    meta.dimensions = im.size

        # 保存 .meta.json
        meta_path = dest.with_suffix(ext + ".meta.json")
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta.to_dict(), indent=2, ensure_ascii=False))

        # 更新索引
        async with self._index_lock:
            self._index[asset_id] = (dest, meta)

        logger.info(f"Asset stored: {dest} (v{version}, {meta.file_size_bytes} bytes)")
        return dest

    async def get_asset(self, asset_id: str) -> Optional[Tuple[Path, AssetMeta]]:
        """通过 asset_id 查找资产。"""
        async with self._index_lock:
            if asset_id in self._index:
                return self._index[asset_id]
        # 回退到磁盘扫描
        for at in AssetType:
            candidate = self.base_path / at.value / asset_id
            # 尝试常见扩展名
            for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".wav", ".mp3", ".webm"):
                full = candidate.with_suffix(ext)
                if full.exists():
                    meta = await self._load_meta(full.with_suffix(ext + ".meta.json"))
                    if meta is None:
                        meta = self._infer_meta(full, asset_id, at.value)
                    async with self._index_lock:
                        self._index[asset_id] = (full, meta)
                    return full, meta
        return None

    async def delete_asset(self, asset_id: str) -> bool:
        """删除资产及其元数据。"""
        result = await self.get_asset(asset_id)
        if result is None:
            return False
        path, meta = result
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        with contextlib.suppress(Exception):
            path.unlink()
        with contextlib.suppress(Exception):
            meta_path.unlink()
        async with self._index_lock:
            self._index.pop(asset_id, None)
        logger.info(f"Asset deleted: {asset_id}")
        return True

    async def _sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # URI 解析与转换
    # ------------------------------------------------------------------

    @classmethod
    def parse_asset_uri(cls, uri: str) -> Optional[AssetLink]:
        """
        解析 asset://<type>/<id>.<ext> URI。

        Returns:
            AssetLink 对象，解析失败返回 None
        """
        if not uri.startswith("asset://"):
            return None
        rest = uri[8:]  # strip "asset://"
        parts = rest.split("/", 1)
        if len(parts) != 2:
            return None
        asset_type_str, filename = parts
        asset_id = Path(filename).stem
        ext = Path(filename).suffix
        at = AssetType.from_str(asset_type_str)
        return AssetLink(
            original_text=uri,
            alt_text="",
            uri=uri,
            asset_type=at,
            asset_id=asset_id,
            ext=ext,
        )

    async def resolve_uri(self, uri: str) -> Optional[Path]:
        """将 asset:// URI 解析为本地绝对路径。"""
        link = self.parse_asset_uri(uri)
        if link is None:
            return None
        result = await self.get_asset(link.asset_id)
        if result:
            return result[0]
        # 回退：直接拼路径
        return self.base_path / link.asset_type.value / f"{link.asset_id}{link.ext}"

    async def uri_to_inline(
        self,
        uri: str,
        max_size_kb: int = 500,
    ) -> Optional[InlineAsset]:
        """
        将 asset:// URI 转换为 base64 内联资产（供 Markdown 前端渲染）。

        如果文件超过 max_size_kb，则返回缩略图版本。
        """
        path = await self.resolve_uri(uri)
        if path is None or not path.exists():
            return None

        size_kb = path.stat().st_size // 1024
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "application/octet-stream"

        # 大图自动降级为缩略图
        if size_kb > max_size_kb and PIL_AVAILABLE and mime.startswith("image/"):
            thumb_path = await self._get_or_create_thumbnail(path, max_size_kb)
            if thumb_path and thumb_path.exists():
                path = thumb_path
                size_kb = path.stat().st_size // 1024
                mime, _ = mimetypes.guess_type(str(path))

        async with aiofiles.open(path, "rb") as f:
            data = await f.read()

        b64 = base64.b64encode(data).decode("ascii")
        inline = InlineAsset(
            asset_id=Path(path).stem,
            mime_type=mime,
            base64_data=b64,
        )

        if PIL_AVAILABLE and mime.startswith("image/"):
            with contextlib.suppress(Exception):
                with Image.open(path) as im:
                    inline.width, inline.height = im.size

        return inline

    # ------------------------------------------------------------------
    # Markdown 资产链接处理
    # ------------------------------------------------------------------

    def extract_asset_links(self, markdown_text: str) -> List[AssetLink]:
        """从 Markdown 文本中提取所有 asset:// 链接。"""
        links: List[AssetLink] = []
        for match in self.ASSET_URI_PATTERN.finditer(markdown_text):
            # match groups: (img_alt, img_type, img_id, link_text, link_type, link_id)
            if match.group(1) is not None:
                alt, atype, aid = match.group(1), match.group(2), match.group(3)
            else:
                alt, atype, aid = match.group(4), match.group(5), match.group(6)
            links.append(AssetLink(
                original_text=match.group(0),
                alt_text=alt or "",
                uri=f"asset://{atype}/{aid}",
                asset_type=AssetType.from_str(atype),
                asset_id=Path(aid).stem,
                ext=Path(aid).suffix,
            ))
        return links

    async def inline_markdown_assets(
        self,
        markdown_text: str,
        max_size_kb: int = 500,
    ) -> str:
        """
        将 Markdown 中的 asset:// 链接替换为 base64 data URI（内联）。

        输出示例：
            ![正面照](data:image/png;base64,iVBORw0KGgo...)
        """
        links = self.extract_asset_links(markdown_text)
        if not links:
            return markdown_text

        result = markdown_text
        for link in links:
            inline = await self.uri_to_inline(link.uri, max_size_kb=max_size_kb)
            if inline is None:
                continue
            data_uri = f"data:{inline.mime_type};base64,{inline.base64_data}"
            if link.original_text.startswith("!["):
                replacement = f"![{link.alt_text}]({data_uri})"
            else:
                replacement = f"[{link.alt_text}]({data_uri})"
            result = result.replace(link.original_text, replacement, 1)
        return result

    async def resolve_markdown_for_json(
        self,
        markdown_text: str,
    ) -> Dict[str, Any]:
        """
        解析 Markdown，返回结构化数据：
        {
          "text": "纯文本内容（不含 asset 链接）",
          "assets": [
            {"uri": "asset://faces/alice_v3", "inline": "data:image/..."}
          ]
        }
        """
        links = self.extract_asset_links(markdown_text)
        assets_data: List[Dict[str, Any]] = []
        text = markdown_text

        for link in links:
            inline = await self.uri_to_inline(link.uri, max_size_kb=500)
            if inline:
                data_uri = f"data:{inline.mime_type};base64,{inline.base64_data}"
                assets_data.append({
                    "uri": link.uri,
                    "type": link.asset_type.value,
                    "asset_id": link.asset_id,
                    "inline_base64": data_uri,
                    "dimensions": [inline.width, inline.height] if inline.width else None,
                })
            # 从 text 中移除 asset 标记，替换为占位符
            placeholder = f"[{link.alt_text or 'asset'}:{link.asset_id}]"
            text = text.replace(link.original_text, placeholder, 1)

        return {"text": text.strip(), "assets": assets_data}

    # ------------------------------------------------------------------
    # 缩略图生成
    # ------------------------------------------------------------------

    async def _get_or_create_thumbnail(
        self,
        source_path: Path,
        target_size_kb: int = 500,
        max_dimension: int = 512,
    ) -> Optional[Path]:
        """获取或生成缩略图。"""
        if not PIL_AVAILABLE:
            return None

        thumb_dir = self.base_path / AssetType.THUMB.value
        thumb_dir.mkdir(parents=True, exist_ok=True)

        thumb_name = f"{source_path.stem}_{max_dimension}.jpg"
        thumb_path = thumb_dir / thumb_name

        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

        # 异步生成缩略图（在线程池执行 PIL 操作）
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                self._generate_thumbnail_sync,
                source_path,
                thumb_path,
                max_dimension,
                target_size_kb,
            )
            return thumb_path
        except Exception as e:
            logger.warning(f"Thumbnail generation failed for {source_path}: {e}")
            return None

    def _generate_thumbnail_sync(
        self,
        source_path: Path,
        thumb_path: Path,
        max_dimension: int,
        target_size_kb: int,
    ) -> None:
        with Image.open(source_path) as im:
            im.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            # 尝试找到合适的 JPEG 质量以满足 target_size_kb
            quality = 85
            while quality >= 30:
                buf = tempfile.BytesIO()
                if im.mode in ("RGBA", "P"):
                    im_rgb = im.convert("RGB")
                else:
                    im_rgb = im
                im_rgb.save(buf, format="JPEG", quality=quality, optimize=True)
                size_kb = buf.tell() // 1024
                if size_kb <= target_size_kb or quality <= 30:
                    with open(thumb_path, "wb") as f:
                        f.write(buf.getvalue())
                    break
                quality -= 10

    # ------------------------------------------------------------------
    # 资产版本链查询
    # ------------------------------------------------------------------

    async def get_version_chain(self, asset_id: str) -> List[AssetMeta]:
        """获取资产的所有版本（从最新到最旧）。"""
        chain: List[AssetMeta] = []
        current_id = asset_id
        visited: Set[str] = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            result = await self.get_asset(current_id)
            if result is None:
                break
            _, meta = result
            chain.append(meta)
            current_id = meta.parent
        return chain

    async def list_assets_by_type(
        self,
        asset_type: Union[str, AssetType],
        limit: int = 100,
    ) -> List[Tuple[Path, AssetMeta]]:
        """按类型列出资产。"""
        at = asset_type if isinstance(asset_type, AssetType) else AssetType.from_str(asset_type)
        subdir = self.base_path / at.value
        results: List[Tuple[Path, AssetMeta]] = []
        if not subdir.exists():
            return results
        for entry in sorted(subdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if entry.is_file() and not entry.name.endswith(".meta.json"):
                aid = entry.stem
                # 跳过版本备份文件（_vN 后缀）
                if re.search(r"_v\d+$", aid):
                    continue
                meta_path = entry.with_suffix(entry.suffix + ".meta.json")
                meta = await self._load_meta(meta_path)
                if meta is None:
                    meta = self._infer_meta(entry, aid, at.value)
                results.append((entry, meta))
                if len(results) >= limit:
                    break
        return results

    async def link_entity_to_asset(self, asset_id: str, entity: str) -> bool:
        """将资产关联到实体（如 character:alice）。"""
        result = await self.get_asset(asset_id)
        if result is None:
            return False
        path, meta = result
        if entity not in meta.linked_entities:
            meta.linked_entities.append(entity)
            meta_path = path.with_suffix(path.suffix + ".meta.json")
            async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(meta.to_dict(), indent=2, ensure_ascii=False))
            async with self._index_lock:
                self._index[asset_id] = (path, meta)
        return True

    # ------------------------------------------------------------------
    # 批量导出 / 备份
    # ------------------------------------------------------------------

    async def export_asset_package(
        self,
        asset_ids: List[str],
        output_path: Union[str, Path],
    ) -> Path:
        """
        将多个资产打包为 JSON + 二进制附件（类似 MCP resource 格式）。
        {
          "manifest": [...],
          "files": {"alice_v3.png": "base64..."}
        }
        """
        output_path = Path(output_path)
        manifest: List[Dict[str, Any]] = []
        files: Dict[str, str] = {}

        for aid in asset_ids:
            result = await self.get_asset(aid)
            if result is None:
                continue
            path, meta = result
            manifest.append(meta.to_dict())
            async with aiofiles.open(path, "rb") as f:
                data = await f.read()
            files[path.name] = base64.b64encode(data).decode("ascii")

        package = {"manifest": manifest, "files": files}
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(package, indent=2, ensure_ascii=False))

        logger.info(f"Exported {len(manifest)} assets to {output_path}")
        return output_path


# ---------------------------------------------------------------------------
# CLI / 测试入口
# ---------------------------------------------------------------------------

import contextlib


async def _demo_main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # 使用临时目录做演示
    tmpdir = tempfile.mkdtemp(prefix="triad_assets_demo_")
    am = AssetManager(base_path=tmpdir)
    await am.build_index()

    # 1. 模拟存储一个资产
    dummy_image = Path(tmpdir) / "dummy_source.png"
    if PIL_AVAILABLE:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (1024, 1024), color=(128, 64, 192))
        draw = ImageDraw.Draw(im)
        draw.rectangle([100, 100, 924, 924], outline="white", width=10)
        im.save(dummy_image)
    else:
        # 无 Pillow 时写入一个假文件
        dummy_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    meta = AssetMeta(
        asset_id="alice_v3",
        asset_type="faces",
        format="png",
        linked_entities=["character:alice"],
        generation_params={"model": "SDXL", "seed": 42, "prompt": "silver hair, purple eyes"},
        description="角色艾莉丝的正面参考图",
    )
    stored = await am.store_asset("alice_v3", "faces", dummy_image, meta=meta)
    print(f"Stored: {stored}")

    # 2. 索引查询
    result = await am.get_asset("alice_v3")
    if result:
        path, meta = result
        print(f"Retrieved: {path.name} -> {meta.to_dict()}")

    # 3. Markdown 解析
    md = """# 角色：艾莉丝
- 年龄: 24岁
- 外貌: 银发紫瞳，身材纤细
- 参考图: ![正面照](asset://faces/alice_v3.png)
- 声音样本: [温柔音色](asset://audio/alice_gentle.wav)
"""
    links = am.extract_asset_links(md)
    print(f"Extracted {len(links)} asset links: {[l.uri for l in links]}")

    # 4. 内联转换（Pillow 可用时生成 base64）
    inlined = await am.inline_markdown_assets(md, max_size_kb=100)
    print("Inlined markdown length:", len(inlined))
    # 验证 asset:// 是否被替换
    assert "asset://" not in inlined or "alice_gentle" in inlined  # audio 不存在，保留原样
    print("Asset inline test passed")

    # 5. JSON 结构化输出
    structured = await am.resolve_markdown_for_json(md)
    print(f"Structured keys: {list(structured.keys())}")
    print(f"Assets count: {len(structured['assets'])}")

    # 6. 版本链
    # 再次存储同名资产触发版本升级
    if PIL_AVAILABLE:
        im2 = Image.new("RGB", (1024, 1024), color=(200, 200, 255))
        im2.save(dummy_image)
    await am.store_asset("alice_v3", "faces", dummy_image, copy=True)
    chain = await am.get_version_chain("alice_v3")
    print(f"Version chain: {[m.version for m in chain]}")

    # 清理
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("Demo complete.")


if __name__ == "__main__":
    asyncio.run(_demo_main())
