import { useState } from 'react';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useTaskStream } from '@/hooks/useTaskStream';
import { MessageList } from '@/components/ChatPanel/MessageList';
import { ModelRouterGraph } from '@/components/AgentCanvas/ModelRouterGraph';
import { VRAMBar } from '@/components/VRAMPanel/VRAMBar';
import { ModelConfigTab } from '@/components/ConfigPanel/ModelConfigTab';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Select } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  Send,
  Paperclip,
  Bot,
  Wifi,
  WifiOff,
  Layers,
  Cpu,
  Settings,
  Plus,
  Save,
  Play,
  Trash2,
  Terminal,
  Palette,
  Activity,
  Circle,
} from 'lucide-react';
import { Strategy } from '@/types';

/* ───────── Tab 标识 ───────── */
type TabId = 'lobster' | 'comfyui' | 'monitor';

/* ═══════════════════════════════════════════════
   🦞 Lobster Console — 现有 App.tsx 完整内容
   ═══════════════════════════════════════════════ */
function LobsterConsole() {
  const [strategy, setStrategy] = useState<Strategy>('AUTO');
  const [input, setInput] = useState('');
  const [activeTab, setActiveTab] = useState('model');

  const {
    messages,
    currentTaskId,
    currentStage,
    progress,
    vramInfo,
    modelRoute,
    appendStreamMessage,
    sendUserMessage,
    clearMessages,
  } = useTaskStream();

  const { status: wsStatus, send } = useWebSocket({
    url: 'ws://localhost:18080/ws/tasks',
    onMessage: appendStreamMessage,
  });

  const handleSend = () => {
    if (!input.trim()) return;
    const payload = sendUserMessage(input, strategy);
    try {
      send(JSON.stringify(payload));
    } catch {
      // offline mode: simulate stream for demo
      const taskId = `task-${Date.now()}`;
      const stages = [
        { stage: 'ANALYZING', message: '已收到请求，正在分析意图...', progress: 0.1, vendor: 'Grok' },
        { stage: 'READING_CODE', message: '正在读取相关代码上下文...', progress: 0.3, vendor: 'DeepSeek' },
        { stage: 'EDITING', message: '生成修改方案并编辑文件...', progress: 0.6, vendor: 'Kimi' },
        { stage: 'TESTING', message: '运行测试验证改动...', progress: 0.85, vendor: 'Claude' },
        { stage: 'COMPLETED', message: '任务完成，结果如下...', progress: 1.0, vendor: 'Claude' },
      ];
      stages.forEach((s, i) => {
        setTimeout(() => {
          appendStreamMessage({
            data: JSON.stringify({
              taskId,
              stage: s.stage,
              message: s.message,
              progress: s.progress,
              modelInfo: { vendor: s.vendor, model: `${s.vendor.toLowerCase()}-v1`, tokensIn: input.length, tokensOut: 128 },
              vramInfo: { state: 'IDLE' as const, embeddingMb: 2048, llmMb: 9216, comfyuiMb: 0, freeMb: 9216 },
            }),
          } as MessageEvent);
        }, i * 800);
      });
    }
    setInput('');
  };

  const wsConnected = wsStatus === 'OPEN';

  return (
    <div className="flex flex-col h-full bg-background text-foreground overflow-hidden">
      {/* Top Bar */}
      <header className="h-12 border-b bg-card flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center">
            <Bot className="w-4 h-4 text-primary-foreground" />
          </div>
          <h1 className="text-sm font-semibold tracking-tight">
            Triad Control Panel
          </h1>
          <Badge variant="secondary" className="text-[10px] ml-2">
            v2.0.0
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <Plus className="w-3.5 h-3.5" /> 新建
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <Save className="w-3.5 h-3.5" /> 保存
          </Button>
          <Button size="sm" className="h-8 text-xs gap-1">
            <Play className="w-3.5 h-3.5" /> 调试运行
          </Button>
          <Separator orientation="vertical" className="h-5 mx-1" />
          <div
            className={cn(
              'flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border',
              wsConnected
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-red-200 bg-red-50 text-red-700'
            )}
          >
            {wsConnected ? (
              <Wifi className="w-3 h-3" />
            ) : (
              <WifiOff className="w-3 h-3" />
            )}
            {wsConnected ? 'OpenClaw 已连接' : '离线模拟'}
          </div>
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <Settings className="w-4 h-4" />
          </Button>
        </div>
      </header>

      {/* Main Workspace */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Chat Panel */}
        <div className="w-[420px] min-w-[320px] flex flex-col border-r bg-card/30">
          <div className="flex items-center justify-between px-4 py-2 border-b bg-card/50">
            <span className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
              <Bot className="w-3.5 h-3.5" /> 对话面板
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-[11px] gap-1"
              onClick={clearMessages}
            >
              <Trash2 className="w-3 h-3" /> 清空
            </Button>
          </div>

          <MessageList
            messages={messages}
            currentStage={currentStage}
            progress={progress}
          />

          <div className="p-3 border-t bg-card/50 space-y-2 shrink-0">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px] h-5 px-1.5">
                任务 {currentTaskId ? `#${currentTaskId.slice(-3)}` : '--'}
              </Badge>
              <div className="flex-1" />
              <Select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value as Strategy)}
                className="h-7 text-[11px] w-32 py-0"
              >
                <option value="AUTO">AUTO</option>
                <option value="CREATIVE">CREATIVE</option>
                <option value="REASONING">REASONING</option>
                <option value="LONGFORM">LONGFORM</option>
                <option value="REVIEW">REVIEW</option>
              </Select>
            </div>
            <div className="flex gap-2">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="输入自然语言指令，Enter 发送，Shift+Enter 换行..."
                className="min-h-[60px] text-xs resize-none"
              />
              <div className="flex flex-col gap-2">
                <Button variant="outline" size="icon" className="h-8 w-8 shrink-0">
                  <Paperclip className="w-4 h-4" />
                </Button>
                <Button size="icon" className="h-8 w-8 shrink-0" onClick={handleSend}>
                  <Send className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Workspace */}
        <div className="flex-1 flex flex-col overflow-hidden bg-muted/20">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className="space-y-2">
              <h2 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5" /> Agent 集群 / 模型路由
              </h2>
              <ModelRouterGraph
                activeVendors={modelRoute}
                currentVendor={modelRoute[modelRoute.length - 1]?.vendor}
              />
            </div>

            <div className="space-y-2">
              <h2 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5" /> VRAM 状态机
              </h2>
              <VRAMBar
                vram={vramInfo}
                totalMb={22528}
                metrics={{ tokPerSec: 42.5, renderStep: 'Step 15/50' }}
              />
            </div>

            <div className="space-y-2">
              <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="text-xs">
                  <TabsTrigger value="model">模型配置</TabsTrigger>
                  <TabsTrigger value="vram">VRAM 调度</TabsTrigger>
                  <TabsTrigger value="skills">技能市场</TabsTrigger>
                  <TabsTrigger value="audit">审计日志</TabsTrigger>
                </TabsList>
                <TabsContent value="model">
                  <ModelConfigTab />
                </TabsContent>
                <TabsContent value="vram">
                  <Card className="p-6 text-xs text-muted-foreground">
                    VRAM 调度面板（占位）：显存分区拖动条、强制切换按钮。
                  </Card>
                </TabsContent>
                <TabsContent value="skills">
                  <Card className="p-6 text-xs text-muted-foreground">
                    技能市场 ClawHub（占位）：技能列表、启用/禁用、手动固化。
                  </Card>
                </TabsContent>
                <TabsContent value="audit">
                  <Card className="p-6 text-xs text-muted-foreground">
                    审计日志（占位）：时间线表格、过滤、导出 CSV。
                  </Card>
                </TabsContent>
              </Tabs>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom Status Bar */}
      <footer className="h-7 border-t bg-card flex items-center justify-between px-4 text-[11px] text-muted-foreground shrink-0">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            {wsConnected ? (
              <Wifi className="w-3 h-3 text-green-500" />
            ) : (
              <WifiOff className="w-3 h-3 text-red-500" />
            )}
            WebSocket: {wsConnected ? '已连接' : '未连接'}
          </span>
          <span className="flex items-center gap-1">
            <Layers className="w-3 h-3" />
            ClawPod: 3 运行中
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <Cpu className="w-3 h-3" />
            VRAM:{' '}
            {vramInfo
              ? `${Math.round(
                  (vramInfo.embeddingMb + vramInfo.llmMb + vramInfo.comfyuiMb) / 1024
                )}GB / 22GB`
              : '--'}
          </span>
        </div>
      </footer>
    </div>
  );
}

/* ═══════════════════════════════════════════════
   📊 系统监控占位面板
   ═══════════════════════════════════════════════ */
function SystemMonitorPlaceholder() {
  return (
    <div className="flex flex-col h-full bg-background text-foreground overflow-hidden">
      <div className="flex-1 flex items-center justify-center">
        <Card className="p-8 text-center space-y-4 max-w-md">
          <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mx-auto">
            <Activity className="w-8 h-8 text-muted-foreground" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">系统监控面板</h2>
            <p className="text-sm text-muted-foreground mt-2">
              SystemMonitorTab 将由另一个任务实现
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              预留容器：GPU 利用率 / 显存时序 / 进程列表 / 网络 I/O
            </p>
          </div>
          <div className="flex gap-2 justify-center pt-2">
            <Badge variant="outline" className="text-[10px]">GPU 监控</Badge>
            <Badge variant="outline" className="text-[10px]">显存追踪</Badge>
            <Badge variant="outline" className="text-[10px]">进程列表</Badge>
          </div>
        </Card>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════
   🌐 BrowserShell — 浏览器多标签外壳
   ═══════════════════════════════════════════════ */
interface TabConfig {
  id: TabId;
  label: string;
  icon: React.ReactNode;
}

const TABS: TabConfig[] = [
  { id: 'lobster', label: '龙虾控制台', icon: <Terminal className="w-4 h-4" /> },
  { id: 'comfyui', label: 'ComfyUI 画布', icon: <Palette className="w-4 h-4" /> },
  { id: 'monitor', label: '系统监控', icon: <Activity className="w-4 h-4" /> },
];

export function BrowserShell() {
  const [activeTab, setActiveTab] = useState<TabId>('lobster');
  const [systemHealthy] = useState(true);

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* ═══════ 顶部全局栏 ═══════ */}
      <header className="h-14 bg-slate-900 border-b border-slate-800 flex items-center justify-between px-5 shrink-0 select-none">
        {/* 左侧：Logo + 标题 */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center">
            <span className="text-lg">🦞</span>
          </div>
          <div className="flex flex-col">
            <h1 className="text-sm font-bold tracking-wide text-slate-100 leading-none">
              Triad Station
            </h1>
            <span className="text-[10px] text-slate-500 font-mono leading-none mt-0.5">
              v2.0.0-beta
            </span>
          </div>
        </div>

        {/* 中间：Tab 切换按钮 */}
        <nav className="flex items-center gap-1">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 text-sm font-medium transition-all duration-200 rounded-md relative',
                  isActive
                    ? 'text-cyan-400 bg-cyan-500/10'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                )}
              >
                {tab.icon}
                <span>{tab.label}</span>
                {isActive && (
                  <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-cyan-400 rounded-full" />
                )}
              </button>
            );
          })}
        </nav>

        {/* 右侧：系统状态 + 设置 */}
        <div className="flex items-center gap-3">
          {/* 系统状态指示灯 */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-slate-800/60 border border-slate-700/50">
            <Circle
              className={cn(
                'w-2.5 h-2.5 fill-current',
                systemHealthy ? 'text-green-400' : 'text-red-400'
              )}
            />
            <span className="text-xs text-slate-400">
              {systemHealthy ? '正常运行' : '系统异常'}
            </span>
          </div>

          <button
            className="w-8 h-8 flex items-center justify-center rounded-md text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            title="设置"
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* ═══════ Tab 内容区 ═══════
         所有 Tab 同时挂载，仅通过 CSS 显示/隐藏
         确保 iframe / WebSocket / 轮询状态不丢失
      ═══════ */}
      <main className="flex-1 overflow-hidden relative">
        {/* ── 🦞 龙虾控制台 ── */}
        <div
          className={cn(
            'absolute inset-0 overflow-hidden',
            activeTab === 'lobster' ? 'block' : 'hidden'
          )}
          aria-hidden={activeTab !== 'lobster'}
        >
          <LobsterConsole />
        </div>

        {/* ── 🎨 ComfyUI 画布 ── */}
        <div
          className={cn(
            'absolute inset-0 overflow-hidden',
            activeTab === 'comfyui' ? 'block' : 'hidden'
          )}
          aria-hidden={activeTab !== 'comfyui'}
        >
          <iframe
            src="http://localhost:18188"
            className="w-full h-full border-0"
            title="ComfyUI Canvas"
            allow="fullscreen"
          />
        </div>

        {/* ── 📊 系统监控 ── */}
        <div
          className={cn(
            'absolute inset-0 overflow-hidden',
            activeTab === 'monitor' ? 'block' : 'hidden'
          )}
          aria-hidden={activeTab !== 'monitor'}
        >
          <SystemMonitorPlaceholder />
        </div>
      </main>
    </div>
  );
}
