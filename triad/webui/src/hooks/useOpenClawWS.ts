// useOpenClawWS.ts — OpenClaw 原生 WebSocket 连接管理
//
// v3.0 变更：
//   - 不再连接自定义 Triad Gateway
//   - 直接对接 OpenClaw 原生 WebSocket
//   - 自动重连 + 指数退避
//   - 断连恢复（请求任务历史）

import { useState, useEffect, useRef, useCallback } from 'react';

type WSStatus = 'DISCONNECTED' | 'CONNECTING' | 'OPEN' | 'ERROR';

interface WSMessage {
  taskId?: string;
  stage?: string;
  message?: string;
  progress?: number;
  output?: string;
  status?: string;
  error?: string;
  systemStatus?: SystemStatusData;
  configUpdate?: ConfigUpdateData;
}

export interface SystemStatusData {
  gpu: {
    name: string;
    memoryUsed: number;
    memoryTotal: number;
    memoryPercent: number;
    gpuUtilization: number;
    temperature: number;
  };
  containers: Array<{
    name: string;
    status: string;
    health: 'healthy' | 'unhealthy' | 'unknown';
  }>;
  llama_server: {
    running: boolean;
    mode: 'GPU' | 'CPU' | 'unknown';
    speed: number;
  };
  cpu: { usage: number; cores: number };
  memory: { used: number; total: number; percent: number };
}

export interface ConfigUpdateData {
  type: 'provider' | 'role' | 'env' | 'skill';
  action: 'added' | 'updated' | 'deleted' | 'toggled';
  data: Record<string, unknown>;
}

interface UseOpenClawWSOptions {
  url?: string;
  onMessage?: (msg: WSMessage) => void;
  autoReconnect?: boolean;
  maxReconnects?: number;
}

export function useOpenClawWS(options: UseOpenClawWSOptions = {}) {
  const {
    url = 'ws://localhost:40088/ws',
    onMessage,
    autoReconnect = true,
    maxReconnects = 10,
  } = options;

  const [status, setStatus] = useState<WSStatus>('DISCONNECTED');
  const [systemStatus, setSystemStatus] = useState<SystemStatusData | null>(null);
  const [configEvents, setConfigEvents] = useState<ConfigUpdateData[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data));
      return true;
    }
    return false;
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus('CONNECTING');
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus('OPEN');
      reconnectCount.current = 0;
      // 订阅系统状态和配置变更
      send({ action: 'subscribe', channels: ['system_status', 'config_update'] });
      // 请求恢复任务
      send({ action: 'recover_tasks' });
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);

        // 系统状态
        if (msg.systemStatus) {
          setSystemStatus(msg.systemStatus);
        }

        // 配置变更
        if (msg.configUpdate) {
          setConfigEvents((prev) => [msg.configUpdate!, ...prev].slice(0, 100));
        }

        onMessage?.(msg);
      } catch {
        // 忽略非 JSON 消息
      }
    };

    ws.onclose = () => {
      setStatus('DISCONNECTED');
      if (autoReconnect && reconnectCount.current < maxReconnects) {
        const delay = Math.min(1000 * 2 ** reconnectCount.current, 30000);
        reconnectCount.current += 1;
        reconnectTimer.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      setStatus('ERROR');
    };
  }, [url, autoReconnect, maxReconnects, onMessage, send]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return {
    status,
    send,
    systemStatus,
    configEvents,
    reconnect: connect,
  };
}
