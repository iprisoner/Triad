/**
 * OpenClaw Gateway REST API — 动态模型注册表管理
 *
 * 路由清单
 * --------
 * GET    /api/models              — 列出所有模型（支持 ?tag=xxx&enabled=true）
 * POST   /api/models              — 添加新模型
 * PUT    /api/models/:id          — 更新模型
 * DELETE /api/models/:id          — 删除模型
 * POST   /api/models/:id/toggle  — 启用/停用切换
 * POST   /api/models/:id/test    — 测试连接（发送 "Hello"）
 */

import { Router, Request, Response } from "express";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import axios, { AxiosError } from "axios";

const router = Router();

// ---------------------------------------------------------------------------
// 配置路径
// ---------------------------------------------------------------------------

const CONFIG_DIR = path.join(os.homedir(), ".triad", "memory", "config");
const PROVIDERS_FILE = path.join(CONFIG_DIR, "providers.json");

// 确保目录存在
if (!fs.existsSync(CONFIG_DIR)) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
}

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

interface ProviderConfig {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  context_window: number;
  tags: string[];
  enabled: boolean;
  temperature_default: number;
  max_tokens_default: number;
  description: string;
}

type ProviderDict = Record<string, ProviderConfig>;

// ---------------------------------------------------------------------------
// 工具函数 — 读写 providers.json
// ---------------------------------------------------------------------------

function loadProviders(): ProviderDict {
  if (!fs.existsSync(PROVIDERS_FILE)) {
    // 首次启动：从环境变量生成默认 providers
    const defaults = buildDefaultProviders();
    saveProviders(defaults);
    return defaults;
  }
  const raw = fs.readFileSync(PROVIDERS_FILE, "utf-8");
  return JSON.parse(raw) as ProviderDict;
}

function saveProviders(data: ProviderDict): void {
  fs.writeFileSync(PROVIDERS_FILE, JSON.stringify(data, null, 2), "utf-8");
}

function buildDefaultProviders(): ProviderDict {
  return {
    grok: {
      id: "grok",
      name: "Grok (xAI)",
      base_url: process.env.GROK_BASE_URL || "https://api.x.ai/v1",
      api_key: process.env.GROK_API_KEY || "",
      context_window: 128000,
      tags: ["creative", "uncensored", "brainstorming"],
      enabled: !!process.env.GROK_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "xAI Grok 系列模型",
    },
    deepseek: {
      id: "deepseek",
      name: "DeepSeek",
      base_url: process.env.DEEPSEEK_BASE_URL || "https://api.deepseek.com/v1",
      api_key: process.env.DEEPSEEK_API_KEY || "",
      context_window: 64000,
      tags: ["reasoning", "code", "logic"],
      enabled: !!process.env.DEEPSEEK_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "DeepSeek 推理模型",
    },
    kimi: {
      id: "kimi",
      name: "Kimi (Moonshot)",
      base_url: process.env.KIMI_BASE_URL || "https://api.moonshot.cn/v1",
      api_key: process.env.KIMI_API_KEY || "",
      context_window: 200000,
      tags: ["longform", "chinese", "chat"],
      enabled: !!process.env.KIMI_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "Moonshot Kimi 长文本模型",
    },
    claude: {
      id: "claude",
      name: "Claude (Anthropic)",
      base_url: process.env.CLAUDE_BASE_URL || "https://api.anthropic.com/v1",
      api_key: process.env.CLAUDE_API_KEY || "",
      context_window: 200000,
      tags: ["reasoning", "review", "creative"],
      enabled: !!process.env.CLAUDE_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "Anthropic Claude 系列",
    },
    gemini: {
      id: "gemini",
      name: "Gemini (Google)",
      base_url: process.env.GEMINI_BASE_URL || "https://generativelanguage.googleapis.com/v1beta",
      api_key: process.env.GEMINI_API_KEY || "",
      context_window: 1048576,
      tags: ["longform", "multimodal", "creative"],
      enabled: !!process.env.GEMINI_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "Google Gemini 系列",
    },
    openai: {
      id: "openai",
      name: "OpenAI (兼容)",
      base_url: process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
      api_key: process.env.OPENAI_API_KEY || "",
      context_window: 128000,
      tags: ["chat", "general"],
      enabled: !!process.env.OPENAI_API_KEY,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "OpenAI 兼容接口",
    },
    qwen: {
      id: "qwen",
      name: "Qwen (本地)",
      base_url: `http://localhost:${process.env.LLAMA_PORT || "18000"}/v1`,
      api_key: "not-needed",
      context_window: parseInt(process.env.LLAMA_CTX_SIZE || "8192", 10),
      tags: ["local", "privacy", "cost_efficient", "chinese"],
      enabled: true,
      temperature_default: 0.7,
      max_tokens_default: 4096,
      description: "本地 Qwen 模型",
    },
  };
}

// ---------------------------------------------------------------------------
// 1. GET /api/models — 列出所有模型
// ---------------------------------------------------------------------------

router.get("/models", (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const tagFilter = req.query.tag as string | undefined;
    const enabledOnly = req.query.enabled !== "false"; // 默认只返回 enabled

    let results = Object.values(providers);

    if (enabledOnly) {
      results = results.filter((p) => p.enabled);
    }

    if (tagFilter) {
      results = results.filter((p) => p.tags.includes(tagFilter));
    }

    // 脱敏：返回时隐藏 api_key
    const sanitized = results.map((p) => ({
      ...p,
      api_key: p.api_key ? "***" : "",
    }));

    res.json({ success: true, count: sanitized.length, data: sanitized });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 2. POST /api/models — 添加新模型
// ---------------------------------------------------------------------------

router.post("/models", (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const body = req.body as Partial<ProviderConfig>;

    if (!body.id || !body.name || !body.base_url) {
      res.status(400).json({
        success: false,
        error: "Missing required fields: id, name, base_url",
      });
      return;
    }

    if (providers[body.id]) {
      res.status(409).json({
        success: false,
        error: `Provider '${body.id}' already exists`,
      });
      return;
    }

    const newProvider: ProviderConfig = {
      id: body.id,
      name: body.name,
      base_url: body.base_url,
      api_key: body.api_key || "",
      context_window: body.context_window || 4096,
      tags: body.tags || ["general"],
      enabled: body.enabled !== undefined ? body.enabled : true,
      temperature_default: body.temperature_default ?? 0.7,
      max_tokens_default: body.max_tokens_default ?? 4096,
      description: body.description || "",
    };

    providers[newProvider.id] = newProvider;
    saveProviders(providers);

    res.status(201).json({
      success: true,
      data: { ...newProvider, api_key: newProvider.api_key ? "***" : "" },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 3. PUT /api/models/:id — 更新模型
// ---------------------------------------------------------------------------

router.put("/models/:id", (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const id = req.params.id;

    if (!providers[id]) {
      res.status(404).json({
        success: false,
        error: `Provider '${id}' not found`,
      });
      return;
    }

    const body = req.body as Partial<ProviderConfig>;
    const allowedFields: (keyof ProviderConfig)[] = [
      "name",
      "base_url",
      "api_key",
      "context_window",
      "tags",
      "enabled",
      "temperature_default",
      "max_tokens_default",
      "description",
    ];

    for (const key of allowedFields) {
      if (body[key] !== undefined) {
        (providers[id] as any)[key] = body[key];
      }
    }

    saveProviders(providers);

    res.json({
      success: true,
      data: { ...providers[id], api_key: providers[id].api_key ? "***" : "" },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 4. DELETE /api/models/:id — 删除模型
// ---------------------------------------------------------------------------

router.delete("/models/:id", (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const id = req.params.id;

    if (!providers[id]) {
      res.status(404).json({
        success: false,
        error: `Provider '${id}' not found`,
      });
      return;
    }

    delete providers[id];
    saveProviders(providers);

    res.json({ success: true, message: `Provider '${id}' deleted` });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 5. POST /api/models/:id/toggle — 启用/停用切换
// ---------------------------------------------------------------------------

router.post("/models/:id/toggle", (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const id = req.params.id;

    if (!providers[id]) {
      res.status(404).json({
        success: false,
        error: `Provider '${id}' not found`,
      });
      return;
    }

    providers[id].enabled = !providers[id].enabled;
    saveProviders(providers);

    res.json({
      success: true,
      data: {
        id,
        enabled: providers[id].enabled,
        name: providers[id].name,
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 6. POST /api/models/:id/test — 测试连接（发送 "Hello"）
// ---------------------------------------------------------------------------

router.post("/models/:id/test", async (req: Request, res: Response) => {
  try {
    const providers = loadProviders();
    const id = req.params.id;

    if (!providers[id]) {
      res.status(404).json({
        success: false,
        error: `Provider '${id}' not found`,
      });
      return;
    }

    const p = providers[id];
    if (!p.enabled) {
      res.status(400).json({
        success: false,
        error: `Provider '${id}' is disabled`,
      });
      return;
    }

    // 构建 OpenAI-compatible 测试请求
    const payload = {
      model: p.id,
      messages: [{ role: "user", content: "Hello" }],
      max_tokens: 16,
      temperature: 0.7,
    };

    const t0 = Date.now();
    try {
      const response = await axios.post(
        `${p.base_url}/chat/completions`,
        payload,
        {
          headers: {
            Authorization: `Bearer ${p.api_key}`,
            "Content-Type": "application/json",
          },
          timeout: 30000,
        }
      );
      const latency = Date.now() - t0;

      res.json({
        success: true,
        data: {
          id,
          name: p.name,
          latency_ms: latency,
          status: response.status,
          response_preview:
            response.data?.choices?.[0]?.message?.content?.substring(0, 100) || "",
        },
      });
    } catch (err: any) {
      const axiosErr = err as AxiosError;
      const latency = Date.now() - t0;
      res.status(502).json({
        success: false,
        error: axiosErr.message,
        data: {
          id,
          name: p.name,
          latency_ms: latency,
          status: axiosErr.response?.status,
          response_body: axiosErr.response?.data,
        },
      });
    }
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

export default router;
export { loadProviders, saveProviders, buildDefaultProviders };
