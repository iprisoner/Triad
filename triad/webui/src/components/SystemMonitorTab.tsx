import React, { useState, useEffect, useCallback } from 'react';
import { Activity, Server, Cpu, HardDrive, Thermometer, Zap } from 'lucide-react';
import clsx from 'clsx';

/* ------------------------------------------------------------------ */
/*  类型定义                                                           */
/* ------------------------------------------------------------------ */

interface GPUStatus {
  name: string;
  memoryUsed: number;      // MB
  memoryTotal: number;     // MB
  memoryPercent: number;   // 0-100
  gpuUtilization: number;  // 0-100
  temperature: number;     // °C
}

interface ContainerInfo {
  name: string;
  status: string;
  health: 'healthy' | 'unhealthy' | 'unknown';
  ports: string;
}

interface LlamaServerStatus {
  running: boolean;
  mode: string;
  speed: number;
}

interface CPUStatus {
  usage: number;
  cores: number;
}

interface MemoryStatus {
  used: number;
  total: number;
  percent: number;
}

interface SystemStatus {
  timestamp: string;
  gpu: GPUStatus;
  containers: ContainerInfo[];
  llama_server: LlamaServerStatus;
  cpu: CPUStatus;
  memory: MemoryStatus;
}

/* ------------------------------------------------------------------ */
/*  辅助组件：进度条                                                   */
/* ------------------------------------------------------------------ */

function ProgressBar({
  value,
  colorClass,
}: {
  value: number;
  colorClass: string;
}) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="w-full bg-slate-800 rounded-full h-4 overflow-hidden">
      <div
        className={clsx('h-full transition-all duration-500 rounded-full', colorClass)}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  主组件                                                             */
/* ------------------------------------------------------------------ */

export function SystemMonitorTab() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /**
   * 拉取后端 /api/system/status 数据
   */
  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/system/status');
      const data = await res.json();

      if (data.success && data.data) {
        setStatus(data.data);
        setError(null);
      } else {
        setError(data.error || '服务端返回异常');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '连接失败');
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * 3 秒轮询 + 清理
   */
  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 3000);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  /* --------------------------- 加载 / 错误状态 --------------------------- */

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        <Cpu className="w-5 h-5 mr-2 animate-spin" />
        系统监控数据加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-400">
        <Activity className="w-5 h-5 mr-2" />
        {error}
      </div>
    );
  }

  if (!status) return null;

  const { gpu, containers, llama_server, cpu, memory } = status;

  /* --------------------------- 渲染 ----------------------------------- */

  return (
    <div className="h-full p-6 space-y-6 overflow-y-auto">
      {/* 更新时间戳 */}
      <div className="text-xs text-slate-500 text-right">
        更新时间：{new Date(status.timestamp).toLocaleTimeString('zh-CN')}
      </div>

      {/* ===================== GPU 显存 + 利用率 + 温度 ===================== */}
      <div className="bg-slate-900 rounded-lg p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-cyan-400" />
            <h3 className="text-slate-200 font-medium">
              GPU 显存 ({gpu.name})
            </h3>
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Thermometer className="w-4 h-4" />
            <span>{gpu.temperature}°C</span>
          </div>
        </div>

        {/* 显存进度条 */}
        <ProgressBar value={gpu.memoryPercent} colorClass="bg-cyan-500" />
        <div className="flex justify-between text-sm text-slate-400 mt-1.5">
          <span>
            {gpu.memoryUsed} MB / {gpu.memoryTotal} MB
          </span>
          <span>{gpu.memoryPercent}%</span>
        </div>

        {/* GPU 利用率 */}
        <div className="mt-3">
          <div className="text-sm text-slate-400 mb-1">
            GPU 利用率：{gpu.gpuUtilization}%
          </div>
          <ProgressBar value={gpu.gpuUtilization} colorClass="bg-emerald-500" />
        </div>
      </div>

      {/* ===================== Docker 容器列表 ===================== */}
      <div className="bg-slate-900 rounded-lg p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <Server className="w-5 h-5 text-emerald-400" />
          <h3 className="text-slate-200 font-medium">
            Docker 容器 ({containers.length} 个)
          </h3>
        </div>

        {containers.length === 0 ? (
          <div className="text-sm text-slate-500">暂无运行中的容器</div>
        ) : (
          <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
            {containers.map((c) => (
              <div
                key={c.name}
                className="flex items-center justify-between text-sm py-1 border-b border-slate-800 last:border-0"
              >
                <div className="flex flex-col">
                  <span className="text-slate-300 font-medium">{c.name}</span>
                  {c.ports && (
                    <span className="text-xs text-slate-500">{c.ports}</span>
                  )}
                </div>
                <span
                  className={clsx(
                    'px-2 py-0.5 rounded text-xs font-medium',
                    c.health === 'healthy'
                      ? 'bg-emerald-900/60 text-emerald-400'
                      : c.health === 'unhealthy'
                      ? 'bg-red-900/60 text-red-400'
                      : 'bg-slate-700 text-slate-400'
                  )}
                >
                  {c.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ===================== llama-server 状态 ===================== */}
      <div className="bg-slate-900 rounded-lg p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <Activity className="w-5 h-5 text-amber-400" />
          <h3 className="text-slate-200 font-medium">llama-server 推理服务</h3>
        </div>

        <div className="flex items-center gap-3">
          <div
            className={clsx(
              'w-2.5 h-2.5 rounded-full',
              llama_server.running ? 'bg-emerald-500' : 'bg-red-500'
            )}
          />
          <span className="text-slate-300 text-sm">
            {llama_server.running
              ? `运行中（模式：${llama_server.mode}）`
              : '未运行'}
          </span>
          {llama_server.speed > 0 && (
            <span className="text-xs text-slate-500 ml-auto">
              速度：{llama_server.speed.toFixed(1)} tok/s
            </span>
          )}
        </div>
      </div>

      {/* ===================== CPU + 内存 双栏 ===================== */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* CPU */}
        <div className="bg-slate-900 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="w-4 h-4 text-blue-400" />
            <span className="text-slate-300 text-sm">
              CPU（{cpu.cores} 核）
            </span>
          </div>
          <div className="text-2xl font-mono text-slate-200 mb-2">
            {cpu.usage.toFixed(1)}%
          </div>
          <ProgressBar value={cpu.usage} colorClass="bg-blue-500" />
        </div>

        {/* 内存 */}
        <div className="bg-slate-900 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-2">
            <HardDrive className="w-4 h-4 text-purple-400" />
            <span className="text-slate-300 text-sm">内存</span>
          </div>
          <div className="text-2xl font-mono text-slate-200 mb-2">
            {memory.percent}%
          </div>
          <ProgressBar value={memory.percent} colorClass="bg-purple-500" />
          <div className="flex justify-between text-xs text-slate-500 mt-1.5">
            <span>{memory.used} MB</span>
            <span>{memory.total} MB</span>
          </div>
        </div>
      </div>
    </div>
  );
}
