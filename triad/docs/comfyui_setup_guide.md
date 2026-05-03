# ComfyUI 傻瓜式操作指南

> 本指南面向 Triad 用户，说明如何在 ComfyUI 网页界面中搭建标准 SDXL 工作流，并导出 API 格式 JSON 供 Triad `hand/` 模块调用。

---

## 一、前置准备

1. **启动 ComfyUI**（宿主机原生运行，非 Docker）
   ```bash
   cd ~/ComfyUI
   python main.py --listen 0.0.0.0 --port 18188
   ```
2. 浏览器打开 `http://localhost:18188`
3. 确保已下载至少一个 **SDXL Checkpoint**（如 `sd_xl_base_1.0.safetensors`），放到 `ComfyUI/models/checkpoints/` 目录

---

## 二、标准 SDXL 文生图工作流（6 步搭建）

### Step 1 — Load Checkpoint（加载模型）

1. 右键画布空白处 → `Add Node` → ` loaders` → `Load Checkpoint`
2. 或者按 `Ctrl + A` 搜索 "Checkpoint"
3. 在节点中点击 `ckpt_name` 下拉框，选择你的 SDXL 模型（如 `sd_xl_base_1.0.safetensors`）

> 节点输出：`MODEL`（UNet）、`CLIP`（文本编码器）、`VAE`（图像解码器）

```
+--------------------------------------------------+
|  Load Checkpoint                                 |
|  ckpt_name: [sd_xl_base_1.0.safetensors ▼]      |
|  ├─ MODEL  ──────────────►  KSampler            |
|  ├─ CLIP   ──────────────►  CLIP Text Encode    |
|  └─ VAE    ──────────────►  VAE Decode          |
+--------------------------------------------------+
```

---

### Step 2 — CLIP Text Encode (Positive)（正向提示词）

1. `Add Node` → `conditioning` → `CLIP Text Encode`
2. 将 `Load Checkpoint` 的 `CLIP` 输出连接到此节点的 `clip` 输入
3. 在 `text` 框中输入你的正向提示词：
   ```
   masterpiece, best quality, 1girl, silver hair, cyberpunk city, neon lights, 
   detailed eyes, futuristic armor, night scene, cinematic lighting
   ```

```
+--------------------------------------------------+
|  CLIP Text Encode (Positive)                     |
|  clip ◄─── Load Checkpoint.CLIP                  |
|  text: "masterpiece, best quality, 1girl..."     |
|  ├─ CONDITIONING ────────►  KSampler.positive     |
+--------------------------------------------------+
```

---

### Step 3 — CLIP Text Encode (Negative)（负向提示词）

1. 再添加一个 `CLIP Text Encode` 节点
2. 同样连接 `Load Checkpoint` 的 `CLIP` 输出
3. 在 `text` 框中输入负向提示词：
   ```
   lowres, bad anatomy, bad hands, text, error, missing fingers, 
   extra digit, fewer digits, cropped, worst quality, low quality
   ```

```
+--------------------------------------------------+
|  CLIP Text Encode (Negative)                     |
|  clip ◄─── Load Checkpoint.CLIP                  |
|  text: "lowres, bad anatomy, bad hands..."       |
|  ├─ CONDITIONING ────────►  KSampler.negative     |
+--------------------------------------------------+
```

---

### Step 4 — KSampler（采样器，核心生成节点）

1. `Add Node` → `sampling` → `KSampler`
2. 连接方式：
   - `model` ← `Load Checkpoint.MODEL`
   - `positive` ← `CLIP Text Encode (Positive).CONDITIONING`
   - `negative` ← `CLIP Text Encode (Negative).CONDITIONING`
3. 参数设置（SDXL 推荐）：
   - `seed`: `-1`（随机）或固定值
   - `steps`: `30`
   - `cfg`: `7.0`
   - `sampler_name`: `dpmpp_2m`
   - `scheduler`: `karras`
   - `denoise`: `1.0`

```
+--------------------------------------------------+
|  KSampler                                        |
|  model    ◄── Load Checkpoint.MODEL               |
|  positive ◄── CLIP Text Encode+.CONDITIONING    |
|  negative ◄── CLIP Text Encode-.CONDITIONING    |
|  seed: -1  | steps: 30  | cfg: 7.0              |
|  sampler: dpmpp_2m | scheduler: karras           |
|  ├─ LATENT ─────────────►  VAE Decode           |
+--------------------------------------------------+
```

---

### Step 5 — VAE Decode（VAE 解码为像素图）

1. `Add Node` → `latent` → `VAE Decode`
2. 连接：
   - `samples` ← `KSampler.LATENT`
   - `vae` ← `Load Checkpoint.VAE`
3. 输出为 `IMAGE`

```
+--------------------------------------------------+
|  VAE Decode                                      |
|  samples ◄── KSampler.LATENT                   |
|  vae     ◄── Load Checkpoint.VAE               |
|  ├─ IMAGE ───────────────►  Save Image          |
+--------------------------------------------------+
```

---

### Step 6 — Save Image（保存图像）

1. `Add Node` → `image` → `Save Image`
2. `images` ← `VAE Decode.IMAGE`
3. `filename_prefix`: `ComfyUI`（保存到 `ComfyUI/output/` 目录）
4. 点击右侧侧边栏的 **Queue Prompt** 按钮（或按 `Ctrl + Enter`）运行工作流

---

## 三、完整工作流拓扑图

```
Load Checkpoint
    ├─ MODEL ──────────────► KSampler.model
    ├─ CLIP ───────────────► CLIP Text Encode+.clip
    │                        └─ CONDITIONING ──► KSampler.positive
    ├─ CLIP ───────────────► CLIP Text Encode-.clip
    │                        └─ CONDITIONING ──► KSampler.negative
    └─ VAE ────────────────► VAE Decode.vae
                                 ▲
KSampler.LATENT ─────────────────┘
    │
VAE Decode.IMAGE ────────────────► Save Image.images
```

---

## 四、启用开发模式（导出 API 格式）

ComfyUI 默认隐藏 API 导出功能，需要手动开启：

1. 点击右上角 **Settings**（齿轮图标）
2. 在设置面板中找到 **Comfy** 标签页
3. 勾选 **"Enable Dev mode Options"**（启用开发模式选项）
4. 关闭设置面板

> 勾选后，画布右下角会出现 **"Save (API Format)"** 按钮（与普通的 Save 按钮并列）

```
+------------------------------------------+
|  [Queue Prompt]  [Save]  [Save(API)]    |  ◄── 勾选后新增
+------------------------------------------+
```

---

## 五、导出 API JSON

1. 确保你的 6 节点工作流已经正确连接
2. 点击 **"Save (API Format)"** 按钮
3. 浏览器会自动下载一个 JSON 文件（如 `workflow_api.json`）
4. **重命名**该文件为 `character_concept_api.json`
5. 将其移动到 Triad 项目的 `hand/` 目录：
   ```bash
   mv ~/Downloads/workflow_api.json /mnt/agents/output/triad/hand/character_concept_api.json
   ```

---

## 六、第二个工作流：场景生成 (`scene_api.json`)

场景工作流结构与人物概念图完全一致，仅需修改 **正向提示词**：

1. 复用上面的 6 节点拓扑（或点击 Save 保存后 Load 加载）
2. 修改 `CLIP Text Encode (Positive)` 的文本为场景描述：
   ```
   masterpiece, best quality, cyberpunk city street at night, 
   rain, neon signs reflecting on wet pavement, 
   towering skyscrapers, flying cars, volumetric fog, 
   highly detailed, cinematic composition, 8k uhd
   ```
3. 可适当调整 `KSampler` 的 `seed` 和 `steps`
4. 点击 **"Save (API Format)"**
5. 重命名为 `scene_api.json` 并放入 `hand/` 目录

---

## 七、第三个工作流：视频片段 (`video_clip_api.json`)

视频生成需要 ComfyUI 的 **AnimateDiff / SVD / Wan 视频模型** 扩展支持：

### 前置条件
- 安装 `ComfyUI-AnimateDiff-Evolved` 或 `ComfyUI-VideoHelperSuite` 插件
- 下载视频模型（如 `svd_xt_1_1.safetensors` 或 `Wan2.1-T2V-14B`）

### 工作流差异点（在标准 6 节点基础上）

```
标准文生图工作流
    │
    ▼
KSampler.LATENT ──────► [新增] Video Linear CFG Guided ──► [新增] SVD/AnimateDiff Loader
                              │                                    │
                              └──────────── LATENT ────────────────┘
                                                        │
                                              [新增] Video VAE Decode
                                                        │
                                              [新增] Video Combine/Save
                                                        │
                                              video_clip_api.json
```

1. 在 `KSampler` 之后插入 **AnimateDiff / SVD 采样节点**（而非直接 VAE Decode）
2. 使用 **Video VAE Decode** 替代普通 VAE Decode
3. 使用 **Video Combine** 节点将帧序列合并为 MP4/GIF
4. 导出 API 格式并重命名为 `video_clip_api.json`
5. 放入 `hand/` 目录

> 若使用 Wan 等最新视频模型，工作流结构可能不同，请参考对应模型的 ComfyUI 示例工作流。

---

## 八、目录结构总览

```
/mnt/agents/output/triad/hand/
├── character_concept_api.json    # 人物概念图 API 工作流
├── scene_api.json                # 场景生成 API 工作流
└── video_clip_api.json           # 视频片段 API 工作流
```

Triad 的 `hand/` 模块会在运行时读取这些 JSON，通过 HTTP POST 发送给 ComfyUI 的 `/prompt` 端点，实现全自动图像/视频生成。

---

## 九、常见问题

| 问题 | 解决 |
|------|------|
| 找不到 "Save (API Format)" 按钮 | 检查 Settings → Comfy → "Enable Dev mode Options" 是否已勾选 |
| 节点连接不上 | 确保 `Load Checkpoint` 的 `MODEL`/`CLIP`/`VAE` 三个输出都已正确连接 |
| 生成黑图 | 检查模型文件是否完整下载，VAE 是否正确连接 |
| 提示词太长报错 | SDXL 提示词建议不超过 77 token，可拆分为多个 `CLIP Text Encode` 用 `Conditioning Concatenate` 合并 |
| API JSON 运行失败 | 检查 JSON 中的 `ckpt_name` 路径是否与你 ComfyUI 服务器上的模型名一致 |

---

**完成！** 你现在拥有三个可直接被 Triad 调用的 ComfyUI API 工作流。
