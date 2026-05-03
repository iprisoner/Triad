import { useState, useCallback, useRef } from 'react';
import { TaskStreamMessage, ChatMessage, VRAMInfo, Strategy } from '@/types';

export function useTaskStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [currentStage, setCurrentStage] = useState<string>('');
  const [progress, setProgress] = useState(0);
  const [vramInfo, setVramInfo] = useState<VRAMInfo | null>(null);
  const [modelRoute, setModelRoute] = useState<{ vendor: string; model: string }[]>([]);
  const messageMapRef = useRef<Record<string, ChatMessage>>({});

  const appendStreamMessage = useCallback((raw: MessageEvent) => {
    try {
      const data: TaskStreamMessage = JSON.parse(raw.data);

      setCurrentTaskId(data.taskId);
      setCurrentStage(data.stage);
      if (data.progress !== undefined) setProgress(data.progress);
      if (data.vramInfo) setVramInfo(data.vramInfo);

      if (data.modelInfo) {
        setModelRoute((prev) => {
          const exists = prev.find((m) => m.vendor === data.modelInfo!.vendor);
          if (exists) return prev;
          return [...prev, { vendor: data.modelInfo!.vendor, model: data.modelInfo!.model }];
        });
      }

      setMessages((prev) => {
        const key = `${data.taskId}-${data.stage}`;
        const existing = messageMapRef.current[key];

        const base: ChatMessage = {
          id: key,
          role: 'assistant',
          content: data.message,
          stage: data.stage,
          taskId: data.taskId,
          timestamp: Date.now(),
          modelInfo: data.modelInfo,
          previews: data.preview ? [data.preview] : undefined,
        };

        if (existing && data.stage !== 'COMPLETED' && data.stage !== 'FAILED') {
          const updated: ChatMessage = {
            ...existing,
            content: data.message,
            previews: data.preview
              ? [...(existing.previews || []), data.preview]
              : existing.previews,
          };
          messageMapRef.current[key] = updated;
          return prev.map((m) => (m.id === key ? updated : m));
        } else {
          messageMapRef.current[key] = base;
          const last = prev[prev.length - 1];
          if (last && last.taskId === data.taskId && last.stage === data.stage) {
            return [...prev.slice(0, -1), base];
          }
          return [...prev, base];
        }
      });
    } catch (e) {
      console.error('Failed to parse stream message', e);
    }
  }, []);

  const sendUserMessage = useCallback(
    (content: string, _strategy: Strategy, _attachments?: File[]) => {
      const id = `user-${Date.now()}`;
      const msg: ChatMessage = {
        id,
        role: 'user',
        content,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, msg]);

      const payload = {
        type: 'task.create',
        content,
        strategy: _strategy,
        attachmentCount: _attachments?.length ?? 0,
      };
      return payload;
    },
    []
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    messageMapRef.current = {};
    setCurrentTaskId(null);
    setCurrentStage('');
    setProgress(0);
    setVramInfo(null);
    setModelRoute([]);
  }, []);

  return {
    messages,
    currentTaskId,
    currentStage,
    progress,
    vramInfo,
    modelRoute,
    appendStreamMessage,
    sendUserMessage,
    clearMessages,
  };
}
