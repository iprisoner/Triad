import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChatMessage } from '@/types';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Bot, User, Film, FileAudio, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface MessageListProps {
  messages: ChatMessage[];
  currentStage: string;
  progress: number;
}

function PreviewRenderer({ preview }: { preview: { type: string; data: string; metadata?: Record<string, any> } }) {
  if (preview.type === 'image') {
    return (
      <div className="mt-2 rounded-lg overflow-hidden border border-border max-w-xs">
        <img
          src={`data:image/jpeg;base64,${preview.data}`}
          alt="preview"
          className="w-full object-cover"
        />
      </div>
    );
  }
  if (preview.type === 'video_frame') {
    return (
      <div className="mt-2 rounded-lg overflow-hidden border border-border max-w-xs relative">
        <img
          src={`data:image/jpeg;base64,${preview.data}`}
          alt="video frame"
          className="w-full object-cover"
        />
        <div className="absolute bottom-1 right-1 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded flex items-center gap-1">
          <Film className="w-3 h-3" /> Video Frame
        </div>
      </div>
    );
  }
  if (preview.type === 'text') {
    return (
      <div className="mt-2 p-3 rounded-md bg-muted/50 text-xs font-mono text-muted-foreground border border-border">
        {preview.data}
      </div>
    );
  }
  if (preview.metadata?.uri?.startsWith('asset://')) {
    return (
      <div className="mt-2 p-3 rounded-md bg-accent border border-border flex items-start gap-3 cursor-pointer hover:bg-accent/80 transition-colors">
        <FileAudio className="w-5 h-5 text-primary mt-0.5" />
        <div>
          <p className="text-sm font-medium">资产卡片</p>
          <p className="text-xs text-muted-foreground">{preview.metadata.uri}</p>
          {preview.metadata.refImage && (
            <img
              src={preview.metadata.refImage}
              alt="ref"
              className="mt-2 rounded w-24 h-24 object-cover border"
            />
          )}
        </div>
      </div>
    );
  }
  return null;
}

function MessageItem({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const stageLabel = message.stage && message.stage !== 'COMPLETED' ? message.stage : null;

  return (
    <div className={cn('flex gap-3 mb-6 animate-slide-in', isUser ? 'flex-row-reverse' : 'flex-row')}>
      <Avatar
        className={cn(
          'h-8 w-8 mt-1',
          isUser ? 'bg-primary/10' : 'bg-secondary'
        )}
      >
        <AvatarFallback>
          {isUser ? (
            <User className="w-4 h-4 text-primary" />
          ) : (
            <Bot className="w-4 h-4 text-muted-foreground" />
          )}
        </AvatarFallback>
      </Avatar>

      <div
        className={cn(
          'flex flex-col max-w-[80%]',
          isUser ? 'items-end' : 'items-start'
        )}
      >
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm shadow-sm',
            isUser
              ? 'bg-primary text-primary-foreground rounded-tr-sm'
              : 'bg-card border rounded-tl-sm'
          )}
        >
          {message.content ? (
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>处理中...</span>
            </div>
          )}
        </div>

        {!isUser && message.previews &&
          message.previews.map((p, i) => (
            <PreviewRenderer key={i} preview={p} />
          ))}

        {!isUser && message.modelInfo && (
          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4">
              {message.modelInfo.vendor} · {message.modelInfo.model}
            </Badge>
            <span>in: {message.modelInfo.tokensIn}</span>
            <span>out: {message.modelInfo.tokensOut}</span>
          </div>
        )}

        {!isUser && stageLabel && (
          <Badge className="mt-1.5 text-[10px] h-4 px-1.5 bg-amber-100 text-amber-700 border-amber-200 hover:bg-amber-100">
            {stageLabel}
          </Badge>
        )}
      </div>
    </div>
  );
}

export function MessageList({ messages, currentStage, progress }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentStage, progress]);

  return (
    <ScrollArea ref={scrollRef} className="flex-1 p-4">
      <div className="space-y-2">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
            <Bot className="w-10 h-10 mb-2 opacity-20" />
            <p className="text-sm">暂无消息，开始一个任务吧</p>
          </div>
        )}
        {messages.map((msg) => (
          <MessageItem key={msg.id} message={msg} />
        ))}
        {currentStage && currentStage !== 'COMPLETED' && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground ml-11 mb-4">
            <Loader2 className="w-3 h-3 animate-spin" />
            <span>当前阶段: {currentStage}</span>
            <span className="text-muted-foreground/60">
              ({Math.round(progress * 100)}%)
            </span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
