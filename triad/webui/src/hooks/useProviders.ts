import { useState, useCallback, useEffect } from 'react';

export interface Provider {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  context_window: number;
  tags: string[];
  enabled: boolean;
  description: string;
}

export interface TestResult {
  success: boolean;
  latency_ms: number;
  response: string;
}

export interface ToastState {
  message: string;
  type: 'success' | 'error';
  visible: boolean;
}

export function useProviders(initialProviders?: Provider[]) {
  const [providers, setProviders] = useState<Provider[]>(initialProviders ?? []);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<ToastState>({ message: '', type: 'success', visible: false });

  const showToast = useCallback((message: string, type: 'success' | 'error') => {
    setToast({ message, type, visible: true });
    setTimeout(() => {
      setToast((prev) => ({ ...prev, visible: false }));
    }, 3000);
  }, []);

  // ── Fetch ──
  const fetchProviders = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/models');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProviders(data.providers || []);
    } catch (err: any) {
      showToast(`加载失败：${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  // ── Add ──
  const addProvider = useCallback(async (provider: Provider) => {
    setLoading(true);
    try {
      const res = await fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(provider),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success && data.provider) {
        setProviders((prev) => [...prev, data.provider]);
        showToast(`模型「${provider.name}」添加成功`, 'success');
        return data.provider as Provider;
      }
      throw new Error('添加失败');
    } catch (err: any) {
      showToast(`添加失败：${err.message}`, 'error');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  // ── Update ──
  const updateProvider = useCallback(async (id: string, updates: Partial<Provider>) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/models/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success) {
        setProviders((prev) =>
          prev.map((p) => (p.id === id ? { ...p, ...updates } : p))
        );
        showToast('更新成功', 'success');
      } else {
        throw new Error('更新失败');
      }
    } catch (err: any) {
      showToast(`更新失败：${err.message}`, 'error');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  // ── Delete ──
  const deleteProvider = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/models/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success) {
        setProviders((prev) => prev.filter((p) => p.id !== id));
        showToast('已删除模型', 'success');
      } else {
        throw new Error('删除失败');
      }
    } catch (err: any) {
      showToast(`删除失败：${err.message}`, 'error');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  // ── Toggle ──
  const toggleProvider = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/models/${id}/toggle`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success) {
        setProviders((prev) =>
          prev.map((p) => (p.id === id ? { ...p, enabled: data.enabled } : p))
        );
        showToast(data.enabled ? '已启用' : '已停用', 'success');
        return data.enabled as boolean;
      }
      throw new Error('操作失败');
    } catch (err: any) {
      showToast(`操作失败：${err.message}`, 'error');
      throw err;
    }
  }, [showToast]);

  // ── Test ──
  const testProvider = useCallback(async (id: string): Promise<TestResult> => {
    try {
      const res = await fetch(`/api/models/${id}/test`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success) {
        showToast(`连接成功，延迟 ${data.latency_ms}ms`, 'success');
        return data as TestResult;
      }
      throw new Error(data.response || '测试失败');
    } catch (err: any) {
      showToast(`连接失败：${err.message}`, 'error');
      throw err;
    }
  }, [showToast]);

  return {
    providers,
    loading,
    toast,
    fetchProviders,
    addProvider,
    updateProvider,
    deleteProvider,
    toggleProvider,
    testProvider,
    showToast,
    dismissToast: () => setToast((prev) => ({ ...prev, visible: false })),
  };
}
