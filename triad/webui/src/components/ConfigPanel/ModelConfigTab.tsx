import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Activity, Save, KeyRound, Server, ArrowDown } from 'lucide-react';

const VENDORS = [
  { key: 'grok', name: 'Grok (xAI)', placeholder: 'xai-...' },
  { key: 'kimi', name: 'Kimi (Moonshot)', placeholder: 'sk-...' },
  { key: 'deepseek', name: 'DeepSeek', placeholder: 'sk-...' },
  { key: 'gemini', name: 'Gemini (Google)', placeholder: 'AIza...' },
  { key: 'claude', name: 'Claude (Anthropic)', placeholder: 'sk-ant-...' },
  { key: 'qwen', name: 'Qwen (Alibaba)', placeholder: 'sk-...' },
];

interface VendorState {
  key: string;
  name: string;
  placeholder: string;
  apiKey: string;
  healthy: boolean | null;
}

export function ModelConfigTab() {
  const [vendors, setVendors] = useState<VendorState[]>(
    VENDORS.map((v) => ({ ...v, apiKey: '', healthy: null }))
  );
  const [localConfig, setLocalConfig] = useState({
    modelPath: '',
    ngl: 99,
    threads: 4,
    ctxSize: 4096,
  });

  useEffect(() => {
    const saved = localStorage.getItem('triad_vendor_keys');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setVendors((prev) =>
          prev.map((v) => ({ ...v, apiKey: parsed[v.key] || '' }))
        );
      } catch {
        // ignore
      }
    }
    const local = localStorage.getItem('triad_local_config');
    if (local) {
      try {
        setLocalConfig(JSON.parse(local));
      } catch {
        // ignore
      }
    }
  }, []);

  const saveKeys = () => {
    const payload: Record<string, string> = {};
    vendors.forEach((v) => {
      payload[v.key] = v.apiKey;
    });
    localStorage.setItem('triad_vendor_keys', JSON.stringify(payload));
    localStorage.setItem('triad_local_config', JSON.stringify(localConfig));
  };

  const checkHealth = (idx: number) => {
    setVendors((prev) =>
      prev.map((v, i) => (i === idx ? { ...v, healthy: true } : v))
    );
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-primary" />
            厂商 API 配置
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {vendors.map((v, idx) => (
            <div key={v.key} className="flex items-center gap-3">
              <div className="w-32 text-xs font-medium text-muted-foreground shrink-0">
                {v.name}
              </div>
              <Input
                type="password"
                className="flex-1 text-xs"
                placeholder={v.placeholder}
                value={v.apiKey}
                onChange={(e) =>
                  setVendors((prev) =>
                    prev.map((pv, i) =>
                      i === idx ? { ...pv, apiKey: e.target.value } : pv
                    )
                  )
                }
              />
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs shrink-0"
                onClick={() => checkHealth(idx)}
              >
                <Activity className="w-3 h-3 mr-1" />
                检查
              </Button>
              {v.healthy === true && (
                <Badge className="bg-green-100 text-green-700 border-green-200 text-[10px] h-5">
                  正常
                </Badge>
              )}
              {v.healthy === false && (
                <Badge variant="destructive" className="text-[10px] h-5">
                  异常
                </Badge>
              )}
            </div>
          ))}
          <Button className="w-full text-xs mt-2" onClick={saveKeys}>
            <Save className="w-3.5 h-3.5 mr-1.5" />
            保存到 localStorage
          </Button>
          <p className="text-[11px] text-muted-foreground">
            注意：演示版本以明文存储，生产环境请使用主密码加密后写入 localStorage。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Server className="w-4 h-4 text-primary" />
            本地 llama-server 配置
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">模型路径</label>
              <Input
                className="text-xs"
                placeholder="/models/Qwen2.5-7B-Q4_K_M.gguf"
                value={localConfig.modelPath}
                onChange={(e) =>
                  setLocalConfig((p) => ({ ...p, modelPath: e.target.value }))
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">
                上下文长度 (ctx_size)
              </label>
              <Input
                type="number"
                className="text-xs"
                value={localConfig.ctxSize}
                onChange={(e) =>
                  setLocalConfig((p) => ({
                    ...p,
                    ctxSize: Number(e.target.value),
                  }))
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">
                GPU 层数 (-ngl)
              </label>
              <Input
                type="number"
                className="text-xs"
                value={localConfig.ngl}
                onChange={(e) =>
                  setLocalConfig((p) => ({ ...p, ngl: Number(e.target.value) }))
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">
                线程数 (-t)
              </label>
              <Input
                type="number"
                className="text-xs"
                value={localConfig.threads}
                onChange={(e) =>
                  setLocalConfig((p) => ({
                    ...p,
                    threads: Number(e.target.value),
                  }))
                }
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <ArrowDown className="w-4 h-4 text-primary" />
            Fallback 降级链
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
            <Badge variant="outline">GPU llama-server</Badge>
            <span>→</span>
            <Badge variant="outline">CPU llama-server</Badge>
            <span>→</span>
            <Badge variant="outline">DeepSeek API</Badge>
            <span>→</span>
            <Badge variant="outline">Claude API</Badge>
          </div>
          <Separator className="my-3" />
          <p className="text-[11px] text-muted-foreground">
            当本地推理失败或显存不足时，自动按上述顺序降级到云端模型。可在 Hermes 路由层配置阈值。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
