# Triad 多模态记忆总线设计文档

> 版本: 1.0.0  
> 作者: Triad System Architect  
> 硬件基准: 双路 E5-2673v3 + 魔改 22GB 2080Ti

---

## 1. 背景与动机

当前 `~/.triad/memory/` 结构仅支持 Markdown 文本资产，无法承载图像、视频、音频等多模态数据。随着执行层从纯代码（Claude Code）扩展到多模态（ComfyUI + SVD + TTS），记忆总线必须升级以支持：

1. **二进制媒体资产的持久化存储**
2. **文本与资产的统一索引与检索**
3. **资产版本链管理**（角色面部迭代、场景概念演进）
4. **流式状态推送中的二进制预览**（生成中的图像/视频帧实时上报）

---

## 2. 目录结构

```
~/.triad/memory/
├── facts/                      # 文本事实（增强：支持资产链接）
│   └── characters/
│       └── alice.md            # 包含 ![face_ref](asset://faces/alice_v3.png)
├── skills/                     # Markdown 技能定义
├── episodes/                   # Markdown 执行日志
├── vectors/                    # faiss 文本向量索引
└── assets/                     # ★ 新增：二进制媒体资产库
    ├── faces/                  # 角色面部参考图
    │   └── alice_v3.png
    │   └── alice_v3.png.meta.json
    ├── scenes/                 # 场景概念图
    ├── videos/                 # 生成的视频片段
    ├── audio/                  # TTS 语音资产
    └── thumbs/                 # 预览缩略图（512x512 JPEG，<500KB）
```

### 2.1 资产类型映射

| 类型 | 子目录 | 格式 | 典型尺寸 | 用途 |
|------|--------|------|----------|------|
| faces | `assets/faces/` | PNG / WEBP | 1024x1024 | 角色面部一致性参考 |
| scenes | `assets/scenes/` | PNG / WEBP | 1344x768 | 场景概念图 |
| videos | `assets/videos/` | MP4 / WEBM | 1024x576 | SVD 生成的视频片段 |
| audio | `assets/audio/` | WAV / MP3 | — | TTS 语音合成输出 |
| thumbs | `assets/thumbs/` | JPEG | 512x512 | 前端快速预览 |

---

## 3. 资产链接机制

### 3.1 URI 规范

```
asset://<type>/<asset_id>[.<ext>]
```

示例：
- `asset://faces/alice_v3.png` — 角色面部参考图
- `asset://scenes/forest_morning_v2.webp` — 场景概念图
- `asset://audio/alice_gentle.wav` — 语音样本

### 3.2 Markdown 嵌入语法

```markdown
# 角色：艾莉丝
- 年龄: 24岁
- 外貌: 银发紫瞳，身材纤细
- 参考图: ![正面照](asset://faces/alice_v3.png)
- 声音样本: [温柔音色](asset://audio/alice_gentle.wav)
```

### 3.3 前端渲染流程（OpenClaw TypeScript 层）

```typescript
// OpenClaw 渲染 Markdown 时
function renderAssetLink(uri: string): ReactNode {
  if (uri.startsWith("asset://")) {
    // 1. 向 Hermes Agent 请求 asset 元数据
    const meta = await hermes.getAssetMeta(uri);
    // 2. 如果尺寸 < 500KB，请求 base64 内联
    if (meta.file_size_bytes < 500 * 1024) {
      const inline = await hermes.getAssetInline(uri);
      return <img src={inline.data_uri} alt={meta.asset_id} />;
    }
    // 3. 大图返回缩略图 URL
    return <img src={`/api/assets/thumb/${meta.asset_id}`} loading="lazy" />;
  }
}
```

### 3.4 Python 解析器（asset_manager.py）

```python
from asset_manager import AssetManager

am = AssetManager()

# 提取 Markdown 中的所有资产链接
links = am.extract_asset_links(markdown_text)
# -> [AssetLink(uri="asset://faces/alice_v3.png", ...)]

# 将 Markdown 中的 asset:// 替换为 base64 data URI
inlined = await am.inline_markdown_assets(markdown_text, max_size_kb=500)
# -> "![正面照](data:image/png;base64,iVBORw0KGgo...)"

# 获取结构化数据（供 JSON API 使用）
structured = await am.resolve_markdown_for_json(markdown_text)
# -> {"text": "...", "assets": [{"uri": "...", "inline_base64": "..."}]}
```

---

## 4. 资产元数据索引

每个资产伴随 `.meta.json` 文件：

```json
{
  "asset_id": "alice_v3",
  "asset_type": "faces",
  "format": "png",
  "dimensions": [1024, 1024],
  "file_size_bytes": 3145728,
  "sha256": "a3f5c2d1e4b6...",
  "linked_entities": ["character:alice", "story:chapter_3"],
  "generation_params": {
    "model": "SDXL",
    "seed": 42,
    "prompt": "silver hair, purple eyes, detailed face, masterpiece",
    "negative": "lowres, bad anatomy",
    "width": 1024,
    "height": 1024,
    "style_preset": "anime"
  },
  "version": 3,
  "parent": "alice_v2",
  "created_at": "2024-12-15T08:23:17.312Z",
  "tags": ["character", "reference", "female"],
  "description": "角色艾莉丝的正面参考图（第三版，修正了瞳孔颜色）"
}
```

### 4.1 元数据字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| asset_id | string | Y | 唯一标识，也是文件名主干 |
| asset_type | string | Y | faces / scenes / videos / audio / thumbs |
| format | string | Y | 文件扩展名 |
| dimensions | [int, int] | N | 图像/视频分辨率 |
| file_size_bytes | int | Y | 文件大小 |
| sha256 | string | Y | 内容校验 |
| linked_entities | [string] | N | 关联实体（character:xxx, story:xxx） |
| generation_params | object | N | 生成参数（可重现） |
| version | int | Y | 版本号，从 1 开始 |
| parent | string | N | 父版本 asset_id |
| created_at | ISO8601 | Y | 创建时间 |
| tags | [string] | N | 标签 |
| description | string | N | 人工注释 |

### 4.2 版本链查询

```python
# 获取 alice_v3 的所有历史版本
chain = await am.get_version_chain("alice_v3")
# -> [alice_v3_meta, alice_v2_meta, alice_v1_meta]
```

版本备份文件命名：`{asset_id}_v{version}{ext}`，存放在同目录下。

---

## 5. 状态上报扩展（二进制预览）

当前流式推送仅支持文本状态。需要扩展 `StatusUpdate` protobuf：

```protobuf
syntax = "proto3";

message StatusUpdate {
  string task_id = 1;
  string agent_id = 2;
  float progress = 3;            // 0.0 ~ 1.0
  string status_message = 4;
  int64 timestamp_ms = 5;

  oneof preview {
    TextPreview text = 10;       // 原有：文本状态
    ImagePreview image = 11;     // ★ 新增：生成中的预览图
    VideoFrame frame = 12;       // ★ 新增：视频生成关键帧
  }
}

message TextPreview {
  string message = 1;
  int32 step = 2;
  int32 total_steps = 3;
}

message ImagePreview {
  bytes data = 1;                // JPEG 编码，建议质量 70%
  string mime = 2;               // "image/jpeg"
  int32 step = 3;
  int32 total_steps = 4;
}

message VideoFrame {
  bytes data = 1;                // JPEG 编码的关键帧
  string mime = 2;
  int32 frame_index = 3;
  int32 total_frames = 4;
}
```

### 5.1 推送策略

| 生成阶段 | 推送间隔 | 内容 |
|----------|----------|------|
| 图像生成（SDXL） | 每 5 步 | `ImagePreview` + 当前步数 |
| 视频生成（SVD） | 每 3 帧 | `VideoFrame` + 帧索引 |
| TTS 合成 | 完成后 | `TextPreview` + duration |
| 面部交换 | 每节点完成 | `TextPreview` + 节点名 |

### 5.2 OpenClaw 前端消费

```typescript
// WebSocket 接收 StatusUpdate
ws.onmessage = (event) => {
  const msg: StatusUpdate = decodeProto(event.data);
  if (msg.preview.image) {
    // 实时显示生成中的预览图
    setPreviewImage(`data:image/jpeg;base64,${toBase64(msg.preview.image.data)}`);
  }
  if (msg.preview.frame) {
    // 视频帧序列：累积显示为动画
    appendVideoFrame(msg.preview.frame);
  }
};
```

---

## 6. 资产与向量索引的协同

多模态资产需要纳入语义检索：

### 6.1 图像 Embedding（CLIP）

```python
import faiss
import torch
from transformers import CLIPProcessor, CLIPModel

# 1. 加载 CLIP 模型（常驻 2GB VRAM）
clip = CLIPModel.from_pretrained("openai/clip-vit-large-patch14").cuda()
processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")

# 2. 计算图像 Embedding
for asset_path in am.list_assets_by_type("faces"):
    image = Image.open(asset_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt").to("cuda")
    embedding = clip.get_image_features(**inputs).detach().cpu().numpy()
    # 3. 存入 faiss
    index.add(embedding)
    # 4. 关联 asset_id
    vector_db[asset_id] = embedding
```

### 6.2 跨模态检索

用户查询 `"找艾莉丝的正面照"` → 文本 Embedding（BGE）检索 facts/characters/alice.md → 提取 asset://faces/alice_v3.png → CLIP 图像相似度排序 → 返回最佳匹配。

---

## 7. 安全与权限

### 7.1 资产访问控制

```python
# 未来扩展：基于实体的访问权限
asset_acl = {
    "alice_v3": {
        "read": ["agent:hermes", "agent:openclaw"],
        "write": ["agent:comfyui_bridge"],
    }
}
```

### 7.2 沙箱路径校验

```python
def validate_asset_path(path: Path) -> bool:
    base = Path.home() / ".triad" / "memory" / "assets"
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False  # 路径逃逸攻击
```

---

## 8. 性能基准

| 操作 | 预期耗时 | 说明 |
|------|----------|------|
| 资产存储（1MB PNG） | < 50ms | 含 SHA256 计算 |
| 缩略图生成（1024→512） | < 200ms | Pillow LANCZOS |
| Markdown 内联转换 | < 100ms | base64 编码 |
| 索引重建（1000 资产） | < 2s | 全目录扫描 |
| 版本链查询 | < 10ms | 内存索引 |

---

## 9. 与 ComfyUI MCP Bridge 的交互

```
ComfyUI Bridge 生成图像
  │
  ├─ 1. 调用 vram_scheduler.acquire_render_context()
  ├─ 2. 执行 ComfyUI 工作流
  ├─ 3. 下载输出文件
  ├─ 4. asset_manager.store_asset() → 存入 assets/faces/
  ├─ 5. 生成 .meta.json（含 generation_params）
  ├─ 6. 更新 facts/characters/alice.md 中的 asset:// 链接
  └─ 7. vram_scheduler.release → LLM 热恢复
```

---

## 10. 待办扩展

- [ ] 资产去重（感知哈希 / CLIP 相似度去重）
- [ ] 资产压缩策略（PNG→WEBP 自动转换）
- [ ] 分布式资产同步（多节点 Triad 集群）
- [ ] 资产生命周期策略（自动清理旧版本）
