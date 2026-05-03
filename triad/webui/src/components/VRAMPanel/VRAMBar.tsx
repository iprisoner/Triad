import { VRAMInfo } from '@/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';

const COLORS = {
  embedding: '#3b82f6',
  llm: '#22c55e',
  comfyui: '#a855f7',
  free: '#94a3b8',
  system: '#475569',
};

const STATE_META: Record<string, { label: string; color: string }> = {
  IDLE: { label: 'IDLE', color: '#22c55e' },
  CPU_FALLBACK: { label: 'CPU_FALLBACK', color: '#eab308' },
  RENDERING: { label: 'RENDERING', color: '#3b82f6' },
  RECOVERING: { label: 'RECOVERING', color: '#a855f7' },
};

function VRAMSegment({
  label,
  mb,
  total,
  color,
  className,
}: {
  label: string;
  mb: number;
  total: number;
  color: string;
  className?: string;
}) {
  const pct = (mb / total) * 100;
  return (
    <div
      className={cn('flex flex-col gap-1', className)}
      style={{ width: `${pct}%`, minWidth: '40px' }}
    >
      <div
        className="h-6 rounded-md flex items-center justify-center text-[10px] font-medium text-white shadow-sm"
        style={{ backgroundColor: color }}
      >
        {mb >= 1024 ? `${(mb / 1024).toFixed(1)}GB` : `${mb}MB`}
      </div>
      <span className="text-[10px] text-center text-muted-foreground truncate">
        {label}
      </span>
    </div>
  );
}

interface VRAMBarProps {
  vram: VRAMInfo | null;
  totalMb?: number;
  metrics?: {
    tokPerSec?: number;
    renderStep?: string;
  };
}

export function VRAMBar({ vram, totalMb = 22528, metrics }: VRAMBarProps) {
  const safe = vram || {
    state: 'IDLE' as const,
    embeddingMb: 2048,
    llmMb: 9216,
    comfyuiMb: 0,
    freeMb: 9216,
  };

  const llmColor =
    safe.state === 'CPU_FALLBACK'
      ? '#94a3b8'
      : safe.state === 'RENDERING'
      ? '#e2e8f0'
      : COLORS.llm;
  const freeColor = safe.state === 'RENDERING' ? COLORS.comfyui : COLORS.free;
  const freeLabel = safe.state === 'RENDERING' ? 'ComfyUI' : '空闲缓冲';

  const usedMb = safe.embeddingMb + safe.llmMb + safe.comfyuiMb;
  const usedPct = Math.round((usedMb / totalMb) * 100);

  const stateMeta = STATE_META[safe.state] || STATE_META.IDLE;

  return (
    <Card className="w-full">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: stateMeta.color }}
            />
            VRAM 状态机
          </CardTitle>
          <Badge variant="secondary" className="text-[10px]">
            {stateMeta.label} | llama-server GPU (-ngl 99)
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex w-full gap-1 rounded-lg overflow-hidden p-1 bg-muted/30">
          <VRAMSegment
            label="Embedding"
            mb={safe.embeddingMb}
            total={totalMb}
            color={COLORS.embedding}
          />
          <VRAMSegment
            label="LLM GPU"
            mb={safe.llmMb}
            total={totalMb}
            color={llmColor}
          />
          <VRAMSegment
            label={freeLabel}
            mb={safe.freeMb}
            total={totalMb}
            color={freeColor}
          />
          <VRAMSegment label="系统" mb={2048} total={totalMb} color={COLORS.system} />
        </div>

        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>已用: {usedPct}%</span>
          <span>总计: {(totalMb / 1024).toFixed(1)}GB</span>
        </div>

        <Separator />

        <div className="grid grid-cols-2 gap-4 text-xs">
          <div className="space-y-1">
            <p className="text-muted-foreground">推理速度</p>
            <p className="font-medium text-foreground">
              {metrics?.tokPerSec ? `${metrics.tokPerSec} tok/s` : '--'}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-muted-foreground">渲染进度</p>
            <p className="font-medium text-foreground">
              {metrics?.renderStep || 'N/A'}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
