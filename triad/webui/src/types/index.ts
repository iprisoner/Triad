export type TaskStage = 
  | 'ANALYZING' 
  | 'READING_CODE' 
  | 'EDITING' 
  | 'TESTING' 
  | 'COMPLETED' 
  | 'FAILED';

export type PreviewType = 'text' | 'image' | 'video_frame';

export interface PreviewData {
  type: PreviewType;
  data: string;
  metadata?: Record<string, any>;
}

export interface ModelInfo {
  vendor: string;
  model: string;
  tokensIn: number;
  tokensOut: number;
}

export interface VRAMInfo {
  state: 'IDLE' | 'CPU_FALLBACK' | 'RENDERING' | 'RECOVERING';
  embeddingMb: number;
  llmMb: number;
  comfyuiMb: number;
  freeMb: number;
}

export interface TaskStreamMessage {
  taskId: string;
  stage: TaskStage;
  message: string;
  progress?: number;
  preview?: PreviewData;
  modelInfo?: ModelInfo;
  vramInfo?: VRAMInfo;
}

export type Strategy = 'AUTO' | 'CREATIVE' | 'REASONING' | 'LONGFORM' | 'REVIEW';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  previews?: PreviewData[];
  modelInfo?: ModelInfo;
  stage?: TaskStage;
  timestamp: number;
  taskId?: string;
}

export interface ModelNodeData extends Record<string, unknown> {
  id: string;
  vendor: string;
  label: string;
  status: 'idle' | 'processing' | 'error' | 'unconfigured';
  tokensIn?: number;
  tokensOut?: number;
}

export interface ModelEdgeData {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated?: boolean;
}

export interface VRAMSegment {
  label: string;
  mb: number;
  color: string;
  key: string;
}

export interface SkillItem {
  id: string;
  name: string;
  type: 'NovelSkill' | 'CodeSkill';
  trigger: string;
  score: number;
  usageCount: number;
  enabled: boolean;
}

export interface AuditRecord {
  id: string;
  taskId: string;
  model: string;
  strategy: Strategy;
  durationMs: number;
  result: 'success' | 'failed' | 'retried';
  timestamp: number;
}
