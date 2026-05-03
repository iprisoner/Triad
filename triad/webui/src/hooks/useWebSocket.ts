import { useEffect, useRef, useState, useCallback } from 'react';

export type WebSocketStatus = 'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED';

interface UseWebSocketOptions {
  url: string;
  onMessage?: (event: MessageEvent) => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
  reconnectInterval?: number;
  heartbeatInterval?: number;
}

export function useWebSocket({
  url,
  onMessage,
  onOpen,
  onClose,
  onError,
  reconnectInterval = 3000,
  heartbeatInterval = 15000,
}: UseWebSocketOptions) {
  const [status, setStatus] = useState<WebSocketStatus>('CLOSED');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionallyClosed = useRef(false);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    intentionallyClosed.current = false;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus('CONNECTING');

    ws.onopen = () => {
      setStatus('OPEN');
      onOpen?.();
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, heartbeatInterval);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'pong') return;
      } catch {
        // not json, pass through
      }
      onMessage?.(event);
    };

    ws.onclose = (event) => {
      setStatus('CLOSED');
      wsRef.current = null;
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      onClose?.(event);

      if (!intentionallyClosed.current && !event.wasClean) {
        reconnectTimerRef.current = setTimeout(() => {
          connect();
        }, reconnectInterval);
      }
    };

    ws.onerror = (event) => {
      setStatus('CLOSED');
      onError?.(event);
    };
  }, [url, onMessage, onOpen, onClose, onError, reconnectInterval, heartbeatInterval]);

  const send = useCallback(
    (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(data);
        return true;
      }
      return false;
    },
    []
  );

  const close = useCallback(() => {
    intentionallyClosed.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
    wsRef.current?.close();
    wsRef.current = null;
    setStatus('CLOSED');
  }, []);

  useEffect(() => {
    connect();
    return () => {
      intentionallyClosed.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { status, send, close, connect };
}
