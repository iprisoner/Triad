"""
Triad 全局配置管理单例。
启动时一次性读取 .env，运行时暴露给所有模块。
支持动态重载（SIGHUP 信号）。
"""
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv

class ConfigManager:
    _instance = None
    _config: Dict[str, Any] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance
    
    def _load(self):
        """从 .env 和 providers.json 加载配置"""
        # 1. 加载 .env
        dotenv_path = Path.home() / ".triad" / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path)
        
        # 2. 基础配置
        self._config = {
            "triad_root": os.getenv("TRIAD_ROOT", str(Path.home() / ".triad")),
            "gateway_port": int(os.getenv("GATEWAY_PORT", "8080")),
            "llama_port": int(os.getenv("LLAMA_PORT", "18000")),
            "llama_model_path": os.getenv("LLAMA_MODEL_PATH", ""),
            "comfyui_host": os.getenv("COMFYUI_HOST", "host.docker.internal"),
            "comfyui_port": int(os.getenv("COMFYUI_PORT", "18188")),
            "hf_endpoint": os.getenv("HF_ENDPOINT", "https://huggingface.co"),
        }
        
        # 3. API Key 映射
        self._config["api_keys"] = {
            "grok": os.getenv("GROK_API_KEY", ""),
            "deepseek": os.getenv("DEEPSEEK_API_KEY", ""),
            "kimi": os.getenv("KIMI_API_KEY", ""),
            "claude": os.getenv("CLAUDE_API_KEY", ""),
            "gemini": os.getenv("GEMINI_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
        }
        
        # 4. MCP 配置
        self._config["mcp"] = {
            "brave_api_key": os.getenv("BRAVE_API_KEY", ""),
            "github_token": os.getenv("GITHUB_TOKEN", ""),
            "apify_token": os.getenv("APIFY_TOKEN", ""),
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)
    
    def get_api_key(self, vendor: str) -> str:
        return self._config.get("api_keys", {}).get(vendor.lower(), "")
    
    def get_mcp_key(self, name: str) -> str:
        return self._config.get("mcp", {}).get(name, "")
    
    def reload(self):
        self._load()

# 全局单例
config = ConfigManager()
