/**
 * TriadPanel.tsx — 龙虾工作站主面板 (v3.0 OpenClaw Native)
 *
 * 改造要点:
 *   - 抛弃自定义 Gateway，直连 OpenClaw 原生 WebSocket
 *   - 左边聊天面板 + 右边监控/配置面板
 *   - 模型在对话中可通过 exec/gateway 工具直接改配置
 *   - 右侧面板实时刷新，无需手动刷
 */

import { useState, useEffect, useCallback } from 'react';
import { useOpenClawWS, SystemStatusData, ConfigUpdateData } from '@/hooks/useOpenClawWS';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import {
  Send,
  Bot,
  Wifi,
  WifiOff,
  Cpu,
  HardDrive,
  Activity,
  Layers,
  Settings,
  Play,
  Trash2,
  Pause,
  RotateCw,
} from 'lucide-react';

// ── Tab 类型 ──
type ConfigTab = 'vram' | 'models' | 'roles' | 'skills';
type Strategy = 'AUTO' | 'CREATIVE' | 'REASONING' | 'LONGFORM' | 'REVIEW';

// ── 消息类型 ──
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

// ═══════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════
export default function TriadPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [strategy, setStrategy] = useState<Strategy>('AUTO');
  const [configTab, setConfigTab] = useState<ConfigTab>('vram');
  const [configEvents, setConfigEvents] = useState<ConfigUpdateData[]>([]);

  const { status: wsStatus, send, systemStatus } = useOpenClawWS({
    url: 'ws://localhost:40088/ws',
    onMessage: (msg) => {
      if (msg.configUpdate) {
        setConfigEvents((prev) => [msg.configUpdate!, ...prev].slice(0, 100));
      }
    },
  });

  const wsConnected = wsStatus === 'OPEN';

  const handleSend = useCallback(() => {
    if (!input.trim()) return;

    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: input,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg]);

    // 发送到 OpenClaw（使用标准消息格式）
    const payload = {
      action: 'submit_task',
      prompt: input.trim(),
      strategy,
      channel: 'web',
    };

    send(payload);
    setInput('');
  }, [input, strategy, send]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* ═══ 左边: 聊天面板 ═══ */}
      <div className="w-[440px] min-w-[360px] flex flex-col border-r border-slate-800 bg-slate-900/50">
        <ChatHeader wsConnected={wsConnected} onClear={() => setMessages([])} />
        <ChatMessages messages={messages} />
        <ChatInput
          input={input}
          setInput={setInput}
          strategy={strategy}
          setStrategy={setStrategy}
          onSend={handleSend}
          onKeyDown={handleKeyDown}
          disabled={!wsConnected}
        />
      </div>

      {/* ═══ 右边: 监控 + 配置面板 ═══ */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* 系统状态概览 */}
          <SystemStatusCard status={systemStatus} />

          {/* VRAM 状态条 */}
          <VRAMStatusBar status={systemStatus} />

          {/* 配置面板 */}
          <Tabs value={configTab} onValueChange={(v) => setConfigTab(v as ConfigTab)}>
            <TabsList className="text-xs">
              <TabsTrigger value="vram">
                <Cpu className="w-3 h-3 mr-1" />VRAM 调度
              </TabsTrigger>
              <TabsTrigger value="models">
                <Layers className="w-3 h-3 mr-1" />模型路由
              </TabsTrigger>
              <TabsTrigger value="roles">
                <Bot className="w-3 h-3 mr-1" />角色配置
              </TabsTrigger>
              <TabsTrigger value="skills">
                <Play className="w-3 h-3 mr-1" />技能市场
              </TabsTrigger>
            </TabsList>

            <TabsContent value="vram">
              <VRAMPanel status={systemStatus} />
            </TabsContent>
            <TabsContent value="models">
              <ModelPanel configEvents={configEvents} />
            </TabsContent>
            <TabsContent value="roles">
              <RolePanel configEvents={configEvents} />
            </TabsContent>
            <TabsContent value="skills">
              <SkillPanel />
            </TabsContent>
          </Tabs>
        </div>

        {/* 底部状态栏 */}
        <StatusBar wsConnected={wsConnected} systemStatus={systemStatus} />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════
// 聊天头部
// ═══════════════════════════════════════════════════
function ChatHeader({ wsConnected, onClear }: { wsConnected: boolean; onClear: () => void }) {
  return (
    <header className="h-12 border-b border-slate-800 bg-slate-900 flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center">
          <span className="text-sm">🦞</span>
        </div>
        <div>
          <h1 className="text-sm font-bold tracking-tight text-slate-100">Triad Station</h1>
          <span className="text-[10px] text-slate-500 font-mono">v3.0</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Badge
          variant="secondary"
          className={cn(
            'text-[10px] h-5',
            wsConnected ? 'bg-green-500/10 text-green-400 border-green-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20',
          )}
        >
          {wsConnected ? <Wifi className="w-3 h-3 mr-1" /> : <WifiOff className="w-3 h-3 mr-1" />}
          {wsConnected ? '已连接' : '未连接'}
        </Badge>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClear}>
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </div>
    </header>
  );
}

// ═══════════════════════════════════════════════════
// 聊天消息列表
// ═══════════════════════════════════════════════════
function ChatMessages({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef?.[1]?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <ScrollArea className="flex-1 px-4 py-3">
      <div className="space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-slate-500 text-sm mt-20">
            <div className="w-14 h-14 mx-auto mb-3 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center">
              <span className="text-2xl">🦞</span>
            </div>
            <p className="font-semibold text-slate-400">Triad Station v3.0</p>
            <p className="text-slate-600 mt-1 text-xs">
              对话中可直接修改系统配置。<br />VRAM、模型路由、角色参数——说出来就改。
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              'flex gap-2 text-sm',
              msg.role === 'user' ? 'justify-end' : 'justify-start',
            )}
          >
            {msg.role === 'assistant' && (
              <div className="w-6 h-6 rounded-md bg-cyan-500/20 flex items-center justify-center shrink-0 mt-0.5">
                <Bot className="w-3.5 h-3.5 text-cyan-400" />
              </div>
            )}
            <div
              className={cn(
                'max-w-[85%] rounded-lg px-3 py-2 text-xs leading-relaxed',
                msg.role === 'user'
                  ? 'bg-cyan-600 text-white'
                  : msg.role === 'system'
                    ? 'bg-slate-800 text-slate-300 border border-slate-700'
                    : 'bg-slate-800/50 text-slate-300',
              )}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>
              <span className="text-[10px] text-slate-500 mt-1 block">
                {new Date(msg.timestamp).toLocaleTimeString()}
              </span>
            </div>
          </div>
        ))}
        <div ref={setRef} />
      </div>
    </ScrollArea>
  );
}

function setRef(el: HTMLDivElement | null) {
  // scroll anchor
}

// ═══════════════════════════════════════════════════
// 聊天输入区
// ═══════════════════════════════════════════════════
function ChatInput({
  input, setInput, strategy, setStrategy, onSend, onKeyDown, disabled,
}: {
  input: string;
  setInput: (v: string) => void;
  strategy: Strategy;
  setStrategy: (v: Strategy) => void;
  onSend: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  disabled: boolean;
}) {
  return (
    <div className="p-3 border-t border-slate-800 bg-slate-900/50 space-y-2 shrink-0">
      <div className="flex items-center gap-2">
        <select
          value={strategy}
          onChange={(e) => setStrategy(e.target.value as Strategy)}
          className="h-7 text-[11px] bg-slate-800 border border-slate-700 rounded px-2 text-slate-300"
        >
          <option value="AUTO">AUTO</option>
          <option value="CREATIVE">CREATIVE</option>
          <option value="REASONING">REASONING</option>
          <option value="LONGFORM">LONGFORM</option>
          <option value="REVIEW">REVIEW</option>
        </select>
      </div>
      <div className="flex gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="输入指令，Enter 发送...（模型会直接改配置）"
          className="min-h-[50px] text-xs resize-none bg-slate-800/50 border-slate-700 text-slate-200"
        />
        <Button size="icon" className="h-8 w-8 shrink-0 bg-cyan-600 hover:bg-cyan-500" onClick={onSend} disabled={disabled}>
          <Send className="w-4 h-4" />
        </Button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════
// 系统状态卡片
// ═══════════════════════════════════════════════════
function SystemStatusCard({ status }: { status: SystemStatusData | null }) {
  if (!status) {
    return (
      <Card className="p-4 bg-slate-800/30 border-slate-700/50">
        <p className="text-xs text-slate-500">等待系统状态...</p>
      </Card>
    );
  }

  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-3">
      <div className="flex items-center gap-2">
        <Activity className="w-4 h-4 text-cyan-400" />
        <h2 className="text-xs font-semibold text-slate-300">系统状态</h2>
      </div>

      <div className="grid grid-cols-4 gap-3 text-[11px]">
        <StatusItem
          label="GPU"
          value={`${status.gpu.memoryUsed}MB / ${status.gpu.memoryTotal}MB`}
          sub={`${status.gpu.memoryPercent}%`}
          color={status.gpu.memoryPercent > 90 ? 'red' : 'green'}
        />
        <StatusItem
          label="LLM"
          value={status.llama_server.running ? status.llama_server.mode : 'OFF'}
          sub={status.llama_server.running ? `${status.llama_server.speed} tok/s` : '停止'}
          color={status.llama_server.mode === 'GPU' ? 'green' : 'yellow'}
        />
        <StatusItem
          label="CPU"
          value={`${status.cpu.usage}%`}
          sub={`${status.cpu.cores} 核`}
          color="cyan"
        />
        <StatusItem
          label="内存"
          value={`${status.memory.used}MB`}
          sub={`${status.memory.percent}%`}
          color="blue"
        />
      </div>
    </Card>
  );
}

function StatusItem({
  label, value, sub, color,
}: {
  label: string; value: string; sub: string; color: string;
}) {
  const colorMap: Record<string, string> = {
    green: 'text-green-400 border-green-500/20 bg-green-500/5',
    yellow: 'text-yellow-400 border-yellow-500/20 bg-yellow-500/5',
    red: 'text-red-400 border-red-500/20 bg-red-500/5',
    cyan: 'text-cyan-400 border-cyan-500/20 bg-cyan-500/5',
    blue: 'text-blue-400 border-blue-500/20 bg-blue-500/5',
  };

  return (
    <div className={cn('rounded border px-3 py-2', colorMap[color])}>
      <div className="text-slate-500 mb-0.5">{label}</div>
      <div className="font-mono font-semibold">{value}</div>
      <div className="text-slate-500">{sub}</div>
    </div>
  );
}

// ═══════════════════════════════════════════════════
// VRAM 状态条
// ═══════════════════════════════════════════════════
function VRAMStatusBar({ status }: { status: SystemStatusData | null }) {
  if (!status) return null;

  const total = status.gpu.memoryTotal;
  const used = status.gpu.memoryUsed;
  const free = total - used;
  const pct = status.gpu.memoryPercent;

  // 分区估算
  const embed = Math.min(2048, used * 0.1);
  const llm = status.llama_server.mode === 'GPU' ? Math.min(9216, used * 0.45) : 0;
  const comfy = Math.max(0, used - embed - llm - 2048); // 剩余 → ComfyUI

  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-2">
      <div className="flex items-center gap-2">
        <HardDrive className="w-4 h-4 text-cyan-400" />
        <h2 className="text-xs font-semibold text-slate-300">VRAM 显存</h2>
        <span className="text-[10px] text-slate-500 ml-auto">{used}MB / {total}MB ({pct}%)</span>
      </div>

      {/* 显存条 */}
      <div className="h-6 bg-slate-800 rounded overflow-hidden flex">
        <div
          className="bg-purple-500/60 text-[9px] flex items-center justify-center font-mono text-purple-100"
          style={{ width: `${(embed / total) * 100}%` }}
        >
          {embed > 200 ? 'EMB' : ''}
        </div>
        <div
          className="bg-blue-500/60 text-[9px] flex items-center justify-center font-mono text-blue-100"
          style={{ width: `${(llm / total) * 100}%` }}
        >
          {llm > 200 ? 'LLM' : ''}
        </div>
        <div
          className="bg-green-500/60 text-[9px] flex items-center justify-center font-mono text-green-100"
          style={{ width: `${(comfy / total) * 100}%` }}
        >
          {comfy > 200 ? 'ComfyUI' : ''}
        </div>
        <div className="flex-1" />
      </div>

      <div className="flex gap-4 text-[10px] text-slate-500">
        <span>🟣 Embed: {Math.round(embed)}MB</span>
        <span>🔵 LLM: {Math.round(llm)}MB</span>
        <span>🟢 ComfyUI: {Math.round(comfy)}MB</span>
        <span>⬜ 空闲: {free}MB</span>
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════
// VRAM 调度面板
// ═══════════════════════════════════════════════════
function VRAMPanel({ status }: { status: SystemStatusData | null }) {
  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-3">
      <p className="text-xs text-slate-400 font-medium">VRAM 模式控制</p>
      <p className="text-[11px] text-slate-500">
        当前: {status?.llama_server.mode || '未知'}<br />
        对话中说出 &quot;LLM 切 CPU&quot; 或 &quot;恢复 GPU 模式&quot; 即可自动执行。
      </p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" className="text-[11px] h-7 bg-slate-800 border-slate-700">
          <Pause className="w-3 h-3 mr-1" />切 CPU
        </Button>
        <Button size="sm" variant="outline" className="text-[11px] h-7 bg-slate-800 border-slate-700">
          <Play className="w-3 h-3 mr-1" />切 GPU
        </Button>
        <Button size="sm" variant="ghost" className="text-[11px] h-7">
          <RotateCw className="w-3 h-3 mr-1" />刷新
        </Button>
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════
// 模型路由面板
// ═══════════════════════════════════════════════════
function ModelPanel({ configEvents }: { configEvents: ConfigUpdateData[] }) {
  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400 font-medium">动态模型路由</p>
        <Button size="sm" variant="ghost" className="text-[11px] h-7">
          <RotateCw className="w-3 h-3 mr-1" />刷新
        </Button>
      </div>

      <p className="text-[11px] text-slate-500">
        不限厂商，Web UI 管理。对话中说出
        &quot;添加 Kimi&quot; 或 &quot;停用 Claude&quot; 即可自动执行。
      </p>

      {/* 最近配置变更 */}
      <div className="text-[10px] text-slate-500 space-y-1">
        <p className="font-medium text-slate-400">最近变更:</p>
        {configEvents.length === 0 ? (
          <p>暂无变更</p>
        ) : (
          configEvents.slice(0, 5).map((ev, i) => (
            <p key={i} className="border-l-2 border-cyan-500/30 pl-2">
              [{ev.type}] {ev.action}: {JSON.stringify(ev.data).slice(0, 60)}...
            </p>
          ))
        )}
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════
// 角色配置面板
// ═══════════════════════════════════════════════════
function RolePanel({ configEvents }: { configEvents: ConfigUpdateData[] }) {
  const roles = [
    { id: 'code_engineer', name: '代码工程师', temp: 0.3, model: 'REASONING' },
    { id: 'novelist', name: '小说家', temp: 0.8, model: 'CREATIVE' },
    { id: 'art_director', name: '美术导演', temp: 0.9, model: 'CREATIVE' },
    { id: 'devops_engineer', name: 'DevOps 工程师', temp: 0.3, model: 'REASONING' },
    { id: 'general', name: '通用助手', temp: 0.7, model: 'CHAT' },
  ];

  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-3">
      <p className="text-xs text-slate-400 font-medium">角色配置</p>
      <p className="text-[11px] text-slate-500">
        对话中说出 &quot;novelist 温度调到 0.9&quot; 即可自动修改。
      </p>

      <div className="space-y-2">
        {roles.map((role) => (
          <div key={role.id} className="flex items-center justify-between p-2 rounded bg-slate-800/50 border border-slate-700/30">
            <div>
              <span className="text-[11px] font-medium text-slate-300">@{role.name}</span>
              <span className="text-[10px] text-slate-500 ml-2">{role.id}</span>
            </div>
            <div className="flex items-center gap-3 text-[10px] text-slate-500">
              <span>temp: {role.temp}</span>
              <span>model: {role.model}</span>
              <div className="w-2 h-2 rounded-full bg-green-400" title="active" />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════
// 技能市场面板
// ═══════════════════════════════════════════════════
function SkillPanel() {
  return (
    <Card className="p-4 bg-slate-800/30 border-slate-700/50 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400 font-medium">技能市场（自动进化）</p>
        <Badge variant="secondary" className="text-[10px] bg-cyan-500/10 text-cyan-400 border-cyan-500/20">
          ClawHub Ready
        </Badge>
      </div>

      <p className="text-[11px] text-slate-500">
        蜂群任务评分 ≥ 8.0 自动固化配方。<br />
        语义去重 + 适者生存 + 配方进化。
      </p>

      <div className="grid grid-cols-2 gap-2 text-[10px]">
        {[
          { name: '悬疑桥段设计', score: 8.5, tags: ['suspense', 'foreshadowing'] },
          { name: '深度技术调研', score: 8.2, tags: ['research', 'tech'] },
          { name: '代码审查', score: 7.9, tags: ['code', 'review'] },
        ].map((skill, i) => (
          <div key={i} className="p-2 rounded bg-slate-800/50 border border-slate-700/30">
            <div className="font-medium text-slate-300">{skill.name}</div>
            <div className="text-slate-500 mt-0.5">
              <span className={skill.score >= 8 ? 'text-green-400' : 'text-yellow-400'}>
                {skill.score}
              </span>
              {' · '}
              {skill.tags.join(', ')}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════
// 底部状态栏
// ═══════════════════════════════════════════════════
function StatusBar({
  wsConnected,
  systemStatus,
}: {
  wsConnected: boolean;
  systemStatus: SystemStatusData | null;
}) {
  return (
    <footer className="h-7 border-t border-slate-800 bg-slate-900 flex items-center justify-between px-4 text-[11px] text-slate-500 shrink-0">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          {wsConnected ? (
            <Wifi className="w-3 h-3 text-green-400" />
          ) : (
            <WifiOff className="w-3 h-3 text-red-400" />
          )}
          OpenClaw {wsConnected ? '已连接' : '未连接'}
        </span>
        <span>LLM: {systemStatus?.llama_server.mode || '--'}</span>
      </div>
      <div className="flex items-center gap-3">
        <span>GPU: {systemStatus ? `${systemStatus.gpu.memoryPercent}%` : '--'}</span>
        <span>CPU: {systemStatus ? `${systemStatus.cpu.usage}%` : '--'}</span>
        <span>v3.0</span>
      </div>
    </footer>
  );
}
