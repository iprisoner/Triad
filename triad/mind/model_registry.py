"""
动态模型注册表。
CRUD 操作 ~/.triad/memory/config/providers.json。
支持无限添加模型，按 tags 路由。
"""
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict

@dataclass
class ProviderConfig:
    id: str
    name: str
    base_url: str
    api_key: str
    context_window: int
    tags: List[str]  # e.g. ["reasoning", "longform", "uncensored"]
    enabled: bool = True
    temperature_default: float = 0.7
    max_tokens_default: int = 4096
    description: str = ""

class ModelRegistry:
    """动态模型注册表，支持无限添加模型"""
    
    CONFIG_DIR = Path.home() / ".triad" / "memory" / "config"
    PROVIDERS_FILE = CONFIG_DIR / "providers.json"
    
    def __init__(self):
        import threading
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._providers: Dict[str, ProviderConfig] = {}
        self._lock = threading.Lock()
        self._load()
    
    def _load(self):
        """从 JSON 文件加载 providers"""
        if self.PROVIDERS_FILE.exists():
            try:
                # 检查文件权限
                file_stat = os.stat(self.PROVIDERS_FILE)
                mode = stat.S_IMODE(file_stat.st_mode)
                if mode & (stat.S_IRWXG | stat.S_IRWXO):
                    print(f"[SECURITY WARNING] providers.json has overly permissive permissions ({oct(mode)}). "
                          f"Run: chmod 600 {self.PROVIDERS_FILE}")
                with open(self.PROVIDERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for pid, pdict in data.items():
                        self._providers[pid] = ProviderConfig(**pdict)
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                print(f"[ModelRegistry] providers.json 损坏，重新初始化: {exc}")
                self._init_defaults()
        else:
            # 首次加载：创建默认 providers（从 .env 读取）
            self._init_defaults()
    
    def _init_defaults(self):
        """从 .env 初始化默认厂商"""
        import os
        defaults = {
            "grok": ProviderConfig(
                id="grok", name="Grok (xAI)",
                base_url=os.getenv("GROK_BASE_URL", "https://api.x.ai/v1"),
                api_key=os.getenv("GROK_API_KEY", ""),
                context_window=128000,
                tags=["creative", "uncensored", "brainstorming"],
                enabled=bool(os.getenv("GROK_API_KEY", "")),
            ),
            "deepseek": ProviderConfig(
                id="deepseek", name="DeepSeek",
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                context_window=64000,
                tags=["reasoning", "code", "logic"],
                enabled=bool(os.getenv("DEEPSEEK_API_KEY", "")),
            ),
            "kimi": ProviderConfig(
                id="kimi", name="Kimi (Moonshot)",
                base_url=os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
                api_key=os.getenv("KIMI_API_KEY", ""),
                context_window=200000,
                tags=["longform", "chinese", "chat"],
                enabled=bool(os.getenv("KIMI_API_KEY", "")),
            ),
            "claude": ProviderConfig(
                id="claude", name="Claude (Anthropic)",
                base_url=os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1"),
                api_key=os.getenv("CLAUDE_API_KEY", ""),
                context_window=200000,
                tags=["reasoning", "review", "creative"],
                enabled=bool(os.getenv("CLAUDE_API_KEY", "")),
            ),
            "gemini": ProviderConfig(
                id="gemini", name="Gemini (Google)",
                base_url=os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
                api_key=os.getenv("GEMINI_API_KEY", ""),
                context_window=1048576,
                tags=["longform", "multimodal", "creative"],
                enabled=bool(os.getenv("GEMINI_API_KEY", "")),
            ),
            "openai": ProviderConfig(
                id="openai", name="OpenAI (兼容)",
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                context_window=128000,
                tags=["chat", "general"],
                enabled=bool(os.getenv("OPENAI_API_KEY", "")),
            ),
            "qwen": ProviderConfig(
                id="qwen", name="Qwen (本地)",
                base_url=f"http://{os.getenv('LLAMA_HOST', 'localhost')}:{os.getenv('LLAMA_PORT', '18000')}/v1",
                api_key="not-needed",
                context_window=int(os.getenv("LLAMA_CTX_SIZE", "8192")),
                tags=["local", "privacy", "cost_efficient", "chinese"],
                enabled=True,
            ),
        }
        self._providers = defaults
        self._save()
    
    def _save(self):
        """保存到 JSON 文件（原子写入 + 安全权限）"""
        data = {pid: asdict(p) for pid, p in self._providers.items()}
        # 原子写入：先写临时文件，再重命名
        tmp_file = self.PROVIDERS_FILE.with_suffix('.tmp')
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # 设置安全权限（仅所有者可读写）
            os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR)
            # 原子重命名
            os.replace(tmp_file, self.PROVIDERS_FILE)
        except Exception as exc:
            if tmp_file.exists():
                tmp_file.unlink()
            raise RuntimeError(f"Failed to save providers: {exc}") from exc
    
    # CRUD 操作
    def list(self, tag_filter: Optional[str] = None, enabled_only: bool = True) -> List[ProviderConfig]:
        results = []
        for p in self._providers.values():
            if enabled_only and not p.enabled:
                continue
            if tag_filter and tag_filter not in p.tags:
                continue
            results.append(p)
        return results
    
    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        return self._providers.get(provider_id)
    
    def add(self, provider: ProviderConfig) -> bool:
        with self._lock:
            if provider.id in self._providers:
                return False
            self._providers[provider.id] = provider
            self._save()
            return True
    
    def update(self, provider_id: str, updates: Dict[str, Any]) -> bool:
        with self._lock:
            if provider_id not in self._providers:
                return False
            p = self._providers[provider_id]
            for key, value in updates.items():
                if hasattr(p, key):
                    setattr(p, key, value)
            self._save()
            return True
    
    def delete(self, provider_id: str) -> bool:
        with self._lock:
            if provider_id not in self._providers:
                return False
            del self._providers[provider_id]
            self._save()
            return True
    
    def toggle(self, provider_id: str) -> bool:
        with self._lock:
            if provider_id not in self._providers:
                return False
            p = self._providers[provider_id]
            p.enabled = not p.enabled
            self._save()
            return True
    
    def find_by_strategy(self, strategy: str) -> List[ProviderConfig]:
        """根据策略查找匹配的 providers"""
        strategy_tag_map = {
            "CREATIVE": ["creative", "brainstorming", "uncensored"],
            "REASONING": ["reasoning", "code", "logic"],
            "LONGFORM": ["longform", "context"],
            "REVIEW": ["reasoning", "review"],
            "CHAT": ["chat", "chinese"],
            "LOCAL": ["local", "privacy"],
        }
        tags = strategy_tag_map.get(strategy.upper(), [])
        results = []
        for p in self._providers.values():
            if not p.enabled:
                continue
            if any(tag in p.tags for tag in tags):
                results.append(p)
        return results
