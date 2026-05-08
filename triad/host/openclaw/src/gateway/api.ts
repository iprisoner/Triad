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

import { Router, Request, Response, NextFunction } from "express";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import axios from "axios";
import crypto from "crypto";

const router = Router();

// v2.3.1: 简单的 rate limiting（内存级，生产环境建议用 Redis）
const rateLimitMap = new Map<string, { count: number; resetTime: number }>();
const RATE_LIMIT_WINDOW_MS = 60_000; // 1分钟
const RATE_LIMIT_MAX = 60; // 每IP每分钟60请求

function rateLimit(req: Request, res: Response, next: NextFunction): void {
  const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
  const now = Date.now();
  const record = rateLimitMap.get(clientIp);
  if (!record || now > record.resetTime) {
    rateLimitMap.set(clientIp, { count: 1, resetTime: now + RATE_LIMIT_WINDOW_MS });
    next();
    return;
  }
  if (record.count >= RATE_LIMIT_MAX) {
    res.status(429).json({ success: false, error: 'Rate limit exceeded' });
    return;
  }
  record.count++;
  next();
}

router.use(rateLimit);
router.use(apiKeyAuth);

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

const ENCRYPTION_KEY = process.env.TRIAD_ENCRYPTION_KEY || '';
const PBKDF2_ITERATIONS = 100000;
const PBKDF2_KEYLEN = 32;
const PBKDF2_DIGEST = 'sha256';

function _deriveKey(): Buffer {
  if (!ENCRYPTION_KEY) {
    throw new Error('TRIAD_ENCRYPTION_KEY not configured');
  }
  // 使用密钥哈希作为固定 salt（确保同一密钥始终派生同一密钥）
  const salt = crypto.createHash('sha256').update(ENCRYPTION_KEY).digest().slice(0, 16);
  return crypto.pbkdf2Sync(ENCRYPTION_KEY, salt, PBKDF2_ITERATIONS, PBKDF2_KEYLEN, PBKDF2_DIGEST);
}

function encryptApiKey(plain: string): string {
  if (!ENCRYPTION_KEY || plain.startsWith('enc:') || !plain) return plain;
  try {
    const key = _deriveKey();
    const iv = crypto.randomBytes(16);
    const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
    let encrypted = cipher.update(plain, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    const authTag = cipher.getAuthTag().toString('hex');
    return `enc:${iv.toString('hex')}:${authTag}:${encrypted}`;
  } catch (err) {
    throw new Error(`Failed to encrypt API key: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function decryptApiKey(encrypted: string): string {
  if (!encrypted || !encrypted.startsWith('enc:')) return encrypted;
  if (!ENCRYPTION_KEY) throw new Error('TRIAD_ENCRYPTION_KEY not configured');
  try {
    const parts = encrypted.split(':');
    if (parts.length !== 4) throw new Error('Invalid encrypted format');
    const iv = Buffer.from(parts[1], 'hex');
    const authTag = Buffer.from(parts[2], 'hex');
    const key = _deriveKey();
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
    decipher.setAuthTag(authTag);
    let decrypted = decipher.update(parts[3], 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    return decrypted;
  } catch (err) {
    throw new Error(`Failed to decrypt API key: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function apiKeyAuth(req: Request, res: Response, next: NextFunction): void {
  // 仅豁免特定的 GET 请求路径
  const exemptGetPaths = ['/api/system/status', '/api/models'];
  if (req.method === 'GET' && exemptGetPaths.includes(req.path)) {
    next();
    return;
  }
  const apiKey = req.headers['x-api-key'] || req.headers.authorization?.replace('Bearer ', '');
  const expectedKey = process.env.TRIAD_API_KEY;
  if (!expectedKey) {
    // 未配置 API Key：拒绝所有非豁免请求（生产安全模式）
    res.status(401).json({ success: false, error: 'TRIAD_API_KEY not configured on server' });
    return;
  }
  if (!apiKey || apiKey !== expectedKey) {
    res.status(401).json({ success: false, error: 'Unauthorized' });
    return;
  }
  next();
}

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
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
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
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
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
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
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
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
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
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
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

    // SSRF 防护：校验 base_url 禁止内网地址
    try {
      const parsed = new URL(p.base_url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        res.status(400).json({ success: false, error: "Invalid protocol" });
        return;
      }
      const blocked = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."];
      if (blocked.some(h => parsed.hostname === h || parsed.hostname.startsWith(h))) {
        res.status(400).json({ success: false, error: "Internal addresses are not allowed" });
        return;
      }
    } catch {
      res.status(400).json({ success: false, error: "Invalid base_url" });
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
    } catch (err) {
      const axiosErr = err as any;
      const latency = Date.now() - t0;
      res.status(502).json({
        success: false,
        error: "Provider test failed",
        data: {
          id,
          name: p.name,
          latency_ms: latency,
          status: axiosErr.response?.status,
        },
      });
    }
  } catch (err) {
    res.status(500).json({ success: false, error: (err instanceof Error ? err.message : String(err)) });
  }
});

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

export default router;
export { loadProviders, saveProviders, buildDefaultProviders };
