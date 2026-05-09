import React, { useState, useMemo, useCallback, useEffect } from 'react';
import {
  Brain,
  Plus,
  Trash2,
  Eye,
  EyeOff,
  Zap,
  Check,
  X,
  Loader2,
  Server,
  KeyRound,
  Globe,
  Tag,
  AlignLeft,
  ChevronsUpDown,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useProviders, type Provider, type ToastState } from '@/hooks/useProviders';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────

const ALL_TAGS = [
  { value: 'reasoning', label: '推理', color: 'bg-blue-100 text-blue-700 border-blue-200' },
  { value: 'code', label: '代码', color: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  { value: 'creative', label: '创意', color: 'bg-purple-100 text-purple-700 border-purple-200' },
  { value: 'longform', label: '长文', color: 'bg-amber-100 text-amber-700 border-amber-200' },
  { value: 'chinese', label: '中文', color: 'bg-red-100 text-red-700 border-red-200' },
  { value: 'uncensored', label: '无审查', color: 'bg-slate-100 text-slate-700 border-slate-200' },
] as const;

const FILTER_TAGS = [
  { value: 'all', label: '全部' },
  { value: 'reasoning', label: '推理' },
  { value: 'code', label: '代码' },
  { value: 'creative', label: '创意' },
  { value: 'longform', label: '长文' },
  { value: 'chinese', label: '中文' },
];

const DEFAULT_PROVIDER: Provider = {
  id: '',
  name: '',
  base_url: 'http://0.0.0.0:18000/v1/chat/completions',
  api_key: '',
  context_window: 4096,
  tags: [],
  enabled: true,
  description: '',
};

// ─────────────────────────────────────────────
// Toast Component
// ─────────────────────────────────────────────

const Toast: React.FC<{ toast: ToastState; onDismiss: () => void }> = ({ toast, onDismiss }) => {
  useEffect(() => {
    if (toast.visible) {
      const timer = setTimeout(onDismiss, 3000);
      return () => clearTimeout(timer);
    }
  }, [toast.visible, onDismiss]);

  if (!toast.visible) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 animate-in slide-in-from-bottom-2 fade-in duration-300">
      <div
        className={cn(
          'flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg border text-sm font-medium',
          toast.type === 'success'
            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
            : 'bg-red-50 text-red-700 border-red-200'
        )}
      >
        <span
          className={cn(
            'w-2 h-2 rounded-full',
            toast.type === 'success' ? 'bg-emerald-500' : 'bg-red-500'
          )}
        />
        {toast.message}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────
// Provider Card (Left Panel Item)
// ─────────────────────────────────────────────

interface ProviderCardProps {
  provider: Provider;
  isActive: boolean;
  onClick: () => void;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}

const ProviderCard: React.FC<ProviderCardProps> = ({
  provider,
  isActive,
  onClick,
  onToggle,
  onDelete,
}) => {
  const [showDelete, setShowDelete] = useState(false);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setShowDelete(true);
    },
    []
  );

  const tagMap = useMemo(
    () =>
      Object.fromEntries(ALL_TAGS.map((t) => [t.value, { label: t.label, color: t.color }])),
    []
  );

  return (
    <Card
      className={cn(
        'group cursor-pointer border transition-all duration-200 overflow-hidden',
        isActive
          ? 'border-primary/50 shadow-md ring-1 ring-primary/20'
          : 'border-border/60 hover:border-border hover:shadow-sm'
      )}
      onClick={onClick}
      onContextMenu={handleContextMenu}
    >
      <CardContent className="p-4 flex flex-col gap-2">
        {/* Header Row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <span
              className={cn(
                'w-2.5 h-2.5 rounded-full shrink-0',
                provider.enabled ? 'bg-emerald-500' : 'bg-slate-300'
              )}
            />
            <div className="min-w-0">
              <h4 className="text-sm font-semibold text-foreground truncate leading-tight">
                {provider.name}
              </h4>
              <p className="text-[11px] text-muted-foreground truncate">{provider.id}</p>
            </div>
          </div>

          {/* Delete button appears on hover or context menu */}
          <div className="flex items-center gap-1 shrink-0">
            <button
              className={cn(
                'opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-50 text-muted-foreground hover:text-red-500',
                showDelete && 'opacity-100'
              )}
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm(`确定要删除模型「${provider.name}」吗？`)) {
                  onDelete(provider.id);
                }
                setShowDelete(false);
              }}
              title="删除"
              type="button"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Tags */}
        {provider.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {provider.tags.slice(0, 4).map((tag) => {
              const info = tagMap[tag];
              return (
                <Badge
                  key={tag}
                  variant="outline"
                  className={cn(
                    'text-[10px] px-1.5 py-0 h-4 font-normal',
                    info?.color || 'bg-muted text-muted-foreground border-border'
                  )}
                >
                  {info?.label || tag}
                </Badge>
              );
            })}
            {provider.tags.length > 4 && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 font-normal bg-muted">
                +{provider.tags.length - 4}
              </Badge>
            )}
          </div>
        )}

        {/* Footer: status + toggle */}
        <div className="flex items-center justify-between pt-1 border-t border-border/40">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span
              className={cn(
                'w-1.5 h-1.5 rounded-full',
                provider.enabled ? 'bg-emerald-500' : 'bg-slate-300'
              )}
            />
            <span>{provider.enabled ? '已启用' : '已停用'}</span>
          </div>
          <Switch
            checked={provider.enabled}
            onCheckedChange={() => onToggle(provider.id)}
            onClick={(e) => e.stopPropagation()}
            aria-label={`${provider.name} 开关`}
          />
        </div>
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────
// Provider Form (Right Panel)
// ─────────────────────────────────────────────

interface ProviderFormProps {
  value: Provider;
  mode: 'add' | 'edit';
  onChange: (p: Provider) => void;
  onSave: () => void;
  onTest: () => void;
  onCancel: () => void;
  errors: Record<string, string>;
  saving: boolean;
  testing: boolean;
}

const ProviderForm: React.FC<ProviderFormProps> = ({
  value,
  mode,
  onChange,
  onSave,
  onTest,
  onCancel,
  errors,
  saving,
  testing,
}) => {
  const [showKey, setShowKey] = useState(false);

  const update = useCallback(
    (field: keyof Provider, val: any) => {
      onChange({ ...value, [field]: val });
    },
    [value, onChange]
  );

  const toggleTag = useCallback(
    (tag: string) => {
      const has = value.tags.includes(tag);
      update(
        'tags',
        has ? value.tags.filter((t) => t !== tag) : [...value.tags, tag]
      );
    },
    [value.tags, update]
  );

  return (
    <div className="flex flex-col gap-5">
      {/* Section Title */}
      <div className="flex items-center gap-2 pb-2 border-b border-border/60">
        {mode === 'add' ? (
          <>
            <Plus className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">添加模型</h3>
          </>
        ) : (
          <>
            <Server className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">编辑模型</h3>
          </>
        )}
      </div>

      {/* Model ID */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <Tag className="w-3.5 h-3.5 text-muted-foreground" />
          模型标识 <span className="text-red-500">*</span>
        </Label>
        <Input
          placeholder="如 qwen-local、my-gpt4"
          value={value.id}
          onChange={(e) => update('id', e.target.value)}
          disabled={mode === 'edit'}
          className={cn(errors.id && 'border-red-300 focus-visible:ring-red-200')}
        />
        {errors.id && <p className="text-xs text-red-500">{errors.id}</p>}
        {mode === 'edit' && (
          <p className="text-[11px] text-muted-foreground">模型标识不可修改</p>
        )}
      </div>

      {/* Name */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <AlignLeft className="w-3.5 h-3.5 text-muted-foreground" />
          显示名称 <span className="text-red-500">*</span>
        </Label>
        <Input
          placeholder="如 Qwen 14B Local"
          value={value.name}
          onChange={(e) => update('name', e.target.value)}
          className={cn(errors.name && 'border-red-300 focus-visible:ring-red-200')}
        />
        {errors.name && <p className="text-xs text-red-500">{errors.name}</p>}
      </div>

      {/* API URL */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <Globe className="w-3.5 h-3.5 text-muted-foreground" />
          API URL <span className="text-red-500">*</span>
        </Label>
        <Input
          placeholder="http://0.0.0.0:18000/v1/chat/completions"
          value={value.base_url}
          onChange={(e) => update('base_url', e.target.value)}
          className={cn(errors.base_url && 'border-red-300 focus-visible:ring-red-200')}
        />
        {errors.base_url && <p className="text-xs text-red-500">{errors.base_url}</p>}
      </div>

      {/* API Key */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <KeyRound className="w-3.5 h-3.5 text-muted-foreground" />
          API Key <span className="text-red-500">*</span>
        </Label>
        <div className="relative">
          <Input
            type={showKey ? 'text' : 'password'}
            placeholder="sk-... 或本地模型留空"
            value={value.api_key}
            onChange={(e) => update('api_key', e.target.value)}
            className={cn('pr-10', errors.api_key && 'border-red-300 focus-visible:ring-red-200')}
          />
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors p-1"
            onClick={() => setShowKey((v) => !v)}
            tabIndex={-1}
          >
            {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {errors.api_key && <p className="text-xs text-red-500">{errors.api_key}</p>}
      </div>

      {/* Context Window */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <ChevronsUpDown className="w-3.5 h-3.5 text-muted-foreground" />
          上下文窗口
        </Label>
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={256}
            max={200000}
            step={256}
            value={value.context_window}
            onChange={(e) => update('context_window', parseInt(e.target.value, 10) || 4096)}
            className="w-40"
          />
          <span className="text-sm text-muted-foreground">tokens</span>
        </div>
      </div>

      {/* Tags */}
      <div className="space-y-2">
        <Label className="flex items-center gap-1">
          <Tag className="w-3.5 h-3.5 text-muted-foreground" />
          能力标签（多选）
        </Label>
        <div className="flex flex-wrap gap-3">
          {ALL_TAGS.map((tag) => {
            const checked = value.tags.includes(tag.value);
            return (
              <label
                key={tag.value}
                className={cn(
                  'flex items-center gap-1.5 text-sm cursor-pointer select-none px-2 py-1 rounded-md border transition-colors',
                  checked
                    ? tag.color
                    : 'border-border/60 text-muted-foreground hover:text-foreground hover:bg-muted/50'
                )}
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => toggleTag(tag.value)}
                />
                <span>{tag.label}</span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Description */}
      <div className="space-y-1.5">
        <Label className="flex items-center gap-1">
          <AlignLeft className="w-3.5 h-3.5 text-muted-foreground" />
          描述
        </Label>
        <Textarea
          placeholder="可选：填写模型的用途说明…"
          value={value.description}
          onChange={(e) => update('description', e.target.value)}
          rows={3}
        />
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center gap-3 pt-2">
        <Button
          onClick={onSave}
          disabled={saving}
          className="flex items-center gap-1.5"
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Check className="w-4 h-4" />
          )}
          {saving ? '保存中…' : '保存'}
        </Button>

        <Button
          variant="outline"
          onClick={onTest}
          disabled={testing || !value.id}
          className="flex items-center gap-1.5"
        >
          {testing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Zap className="w-4 h-4" />
          )}
          {testing ? '测试中…' : '测试连接'}
        </Button>

        <Button
          variant="ghost"
          onClick={onCancel}
          disabled={saving}
          className="flex items-center gap-1.5"
        >
          <X className="w-4 h-4" />
          取消
        </Button>
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────
// Empty State
// ─────────────────────────────────────────────

const EmptyState: React.FC<{ onAdd: () => void }> = ({ onAdd }) => (
  <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
    <Brain className="w-10 h-10 mb-3 opacity-40" />
    <p className="text-sm">还没有添加任何模型</p>
    <p className="text-xs mt-1 opacity-60 mb-4">点击右上角按钮添加第一个模型</p>
    <Button onClick={onAdd} variant="outline" size="sm" className="gap-1.5">
      <Plus className="w-4 h-4" />
      添加模型
    </Button>
  </div>
);

// ─────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────

const ProviderManager: React.FC<{ initialProviders?: Provider[] }> = ({ initialProviders }) => {
  const {
    providers,
    loading,
    toast,
    addProvider,
    updateProvider,
    deleteProvider,
    toggleProvider,
    testProvider,
    showToast,
    dismissToast,
  } = useProviders(initialProviders);

  // UI State
  const [mode, setMode] = useState<'list' | 'add' | 'edit'>('list');
  const [formProvider, setFormProvider] = useState<Provider>(DEFAULT_PROVIDER);
  const [activeFilter, setActiveFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // Filtered providers
  const filteredProviders = useMemo(() => {
    if (activeFilter === 'all') return providers;
    return providers.filter((p) => p.tags.includes(activeFilter));
  }, [providers, activeFilter]);

  // Reset form
  const resetForm = useCallback(() => {
    setFormProvider(DEFAULT_PROVIDER);
    setErrors({});
    setMode('list');
    setSelectedId(null);
  }, []);

  // Validate
  const validate = useCallback((): boolean => {
    const errs: Record<string, string> = {};
    if (!formProvider.id.trim()) errs.id = '模型标识不能为空';
    if (!formProvider.name.trim()) errs.name = '显示名称不能为空';
    if (!formProvider.base_url.trim()) errs.base_url = 'API URL 不能为空';
    if (!formProvider.api_key.trim()) errs.api_key = 'API Key 不能为空';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  }, [formProvider]);

  // Start add
  const startAdd = useCallback(() => {
    setFormProvider(DEFAULT_PROVIDER);
    setErrors({});
    setMode('add');
    setSelectedId(null);
  }, []);

  // Start edit
  const startEdit = useCallback(
    (id: string) => {
      const p = providers.find((x) => x.id === id);
      if (p) {
        setFormProvider({ ...p });
        setErrors({});
        setMode('edit');
        setSelectedId(id);
      }
    },
    [providers]
  );

  // Handle save
  const handleSave = useCallback(async () => {
    if (!validate()) return;
    setSaving(true);
    try {
      if (mode === 'add') {
        await addProvider(formProvider);
      } else {
        await updateProvider(formProvider.id, formProvider);
      }
      resetForm();
    } catch {
      // error already shown via toast
    } finally {
      setSaving(false);
    }
  }, [mode, formProvider, validate, addProvider, updateProvider, resetForm]);

  // Handle test
  const handleTest = useCallback(async () => {
    if (!formProvider.base_url.trim()) {
      setErrors((prev) => ({ ...prev, base_url: 'API URL 不能为空' }));
      return;
    }
    setTesting(true);
    try {
      if (mode === 'edit' && formProvider.id) {
        await testProvider(formProvider.id);
      } else {
        // Add mode: direct test via frontend
        const start = performance.now();
        const res = await fetch(formProvider.base_url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(formProvider.api_key ? { Authorization: `Bearer ${formProvider.api_key}` } : {}),
          },
          body: JSON.stringify({ messages: [{ role: 'user', content: 'Hello' }] }),
        });
        const latency = Math.round(performance.now() - start);
        if (res.ok) {
          await res.json();
          showToast(`连接成功，延迟 ${latency}ms`, 'success');
        } else {
          showToast(`连接失败：HTTP ${res.status} ${res.statusText}`, 'error');
        }
      }
    } catch (err: any) {
      showToast(`连接失败：${err.message || '网络错误'}`, 'error');
    } finally {
      setTesting(false);
    }
  }, [mode, formProvider, testProvider, showToast]);

  // Handle toggle
  const handleToggle = useCallback(
    async (id: string) => {
      try {
        await toggleProvider(id);
      } catch {
        // error already shown
      }
    },
    [toggleProvider]
  );

  // Handle delete
  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProvider(id);
        if (selectedId === id) resetForm();
      } catch {
        // error already shown
      }
    },
    [deleteProvider, selectedId, resetForm]
  );

  // Mobile: when in edit/add mode, only show form
  const showFormOnly = mode !== 'list';

  return (
    <div className="w-full h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-foreground flex items-center gap-2">
          <Brain className="w-5 h-5 text-primary" />
          模型配置中心
        </h2>
        <div className="flex items-center gap-3">
          {loading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
          <Button
            onClick={startAdd}
            size="sm"
            className="flex items-center gap-1.5"
            disabled={mode === 'add'}
          >
            <Plus className="w-4 h-4" />
            添加模型
          </Button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="shrink-0 mb-4">
        <Tabs value={activeFilter} onValueChange={setActiveFilter}>
          <TabsList className="flex-wrap h-auto gap-1 p-1 bg-muted/50">
            {FILTER_TAGS.map((tag) => (
              <TabsTrigger
                key={tag.value}
                value={tag.value}
                className="text-xs px-3 py-1.5 h-7 data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                {tag.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Main Content: Left List + Right Form */}
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[320px_1fr] lg:grid-cols-[360px_1fr] gap-5">
        {/* Left: Provider List */}
        <div
          className={cn(
            'flex flex-col gap-3 overflow-y-auto min-h-0 pr-1',
            showFormOnly && 'hidden md:flex'
          )}
        >
          {providers.length === 0 ? (
            <EmptyState onAdd={startAdd} />
          ) : filteredProviders.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Server className="w-10 h-10 mb-3 opacity-40" />
              <p className="text-sm">当前筛选条件下无匹配模型</p>
            </div>
          ) : (
            filteredProviders.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                isActive={selectedId === provider.id}
                onClick={() => startEdit(provider.id)}
                onToggle={handleToggle}
                onDelete={handleDelete}
              />
            ))
          )}
        </div>

        {/* Right: Form */}
        <div
          className={cn(
            'flex flex-col overflow-y-auto min-h-0',
            !showFormOnly && 'hidden md:flex'
          )}
        >
          {mode === 'list' ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
              <Server className="w-12 h-12 mb-4 opacity-30" />
              <p className="text-sm">选择左侧模型进行编辑，或点击右上角「添加模型」</p>
            </div>
          ) : (
            <div className="bg-card border rounded-xl p-5 shadow-sm">
              <ProviderForm
                value={formProvider}
                mode={mode}
                onChange={setFormProvider}
                onSave={handleSave}
                onTest={handleTest}
                onCancel={resetForm}
                errors={errors}
                saving={saving}
                testing={testing}
              />
            </div>
          )}
        </div>
      </div>

      {/* Toast */}
      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
};

export default ProviderManager;
