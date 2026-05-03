import { useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  useNodesState,
  useEdgesState,
  Position,
  Handle,
  NodeProps,
  NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Badge } from '@/components/ui/badge';
import { Cpu, Loader2, AlertCircle, PowerOff } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ModelNodeData } from '@/types';

const vendorColors: Record<string, string> = {
  Grok: '#ea580c',
  Kimi: '#2563eb',
  DeepSeek: '#16a34a',
  Gemini: '#9333ea',
  Claude: '#dc2626',
  Qwen: '#0891b2',
};

function ModelNode(props: NodeProps<Node<ModelNodeData>>) {
  const { data } = props;
  const isActive = data.status === 'processing';
  const isError = data.status === 'error';
  const isUnconfigured = data.status === 'unconfigured';

  return (
    <div
      className={cn(
        'relative w-36 rounded-lg border bg-card p-3 shadow-sm transition-all',
        isActive && 'ring-2 ring-primary ring-offset-2 animate-pulse-slow shadow-md',
        isError && 'border-destructive bg-destructive/5',
        isUnconfigured && 'opacity-50 grayscale'
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-muted-foreground !w-2 !h-2"
      />
      <div className="flex items-center gap-2 mb-1">
        <div
          className="w-2 h-2 rounded-full"
          style={{ backgroundColor: vendorColors[data.vendor] || '#64748b' }}
        />
        <span className="text-xs font-semibold truncate">{data.vendor}</span>
      </div>
      <div className="text-[10px] text-muted-foreground truncate mb-2">
        {data.label}
      </div>
      <div className="flex items-center justify-between">
        {isActive && (
          <Loader2 className="w-3 h-3 text-primary animate-spin" />
        )}
        {isError && (
          <AlertCircle className="w-3 h-3 text-destructive" />
        )}
        {isUnconfigured && (
          <PowerOff className="w-3 h-3 text-muted-foreground" />
        )}
        {!isActive && !isError && !isUnconfigured && (
          <Cpu className="w-3 h-3 text-muted-foreground" />
        )}
        <Badge variant="outline" className="text-[9px] h-4 px-1">
          {data.tokensOut ? `${data.tokensOut}tok` : 'idle'}
        </Badge>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!bg-muted-foreground !w-2 !h-2"
      />
    </div>
  );
}

const nodeTypes: NodeTypes = { modelNode: ModelNode };

interface ModelRouterGraphProps {
  activeVendors: { vendor: string; model: string }[];
  currentVendor?: string;
}

export function ModelRouterGraph({ activeVendors, currentVendor }: ModelRouterGraphProps) {
  const allVendors = ['Grok', 'DeepSeek', 'Kimi', 'Claude', 'Gemini', 'Qwen'];
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);

  useEffect(() => {
    const nodeList: Node[] = allVendors.map((v, i) => {
      const active = activeVendors.find((a) => a.vendor === v);
      const isCurrent = currentVendor === v;
      const status: ModelNodeData['status'] = isCurrent
        ? 'processing'
        : active
        ? 'idle'
        : 'unconfigured';

      return {
        id: v,
        type: 'modelNode',
        position: { x: i * 180, y: 40 + (i % 2) * 40 },
        data: {
          id: v,
          vendor: v,
          label: active?.model || `${v}-default`,
          status,
          tokensIn: 0,
          tokensOut: isCurrent ? 128 : 0,
        },
      } as Node;
    });

    const edgeList: Edge[] = [];
    for (let i = 0; i < nodeList.length - 1; i++) {
      edgeList.push({
        id: `e-${nodeList[i].id}-${nodeList[i + 1].id}`,
        source: nodeList[i].id,
        target: nodeList[i + 1].id,
        animated: nodeList[i].id === currentVendor,
        label: 'context',
        style: { stroke: '#94a3b8', strokeWidth: 1.5 },
      });
    }

    setNodes(nodeList);
    setEdges(edgeList);
  }, [activeVendors, currentVendor, setNodes, setEdges]);

  return (
    <div className="h-64 w-full border rounded-xl bg-card/50 overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-left"
      >
        <Background gap={16} size={1} color="#e2e8f0" />
        <MiniMap
          className="!bg-background/80 !border-border"
          nodeStrokeWidth={3}
          zoomable
          pannable
        />
        <Controls className="!bg-background !border-border" />
      </ReactFlow>
    </div>
  );
}
