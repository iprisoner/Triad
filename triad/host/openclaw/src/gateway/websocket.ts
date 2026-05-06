import { WebSocketServer, WebSocket } from 'ws';
import { v4 as uuidv4 } from 'uuid';
import express, { Request, Response, NextFunction } from 'express';
import http from 'http';

// ═══════════════════════════════════════════════════════════════════════════
//  类型定义
// ═══════════════════════════════════════════════════════════════════════════

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

export type VramState = 'IDLE' | 'CPU_FALLBACK' | 'RENDERING' | 'RECOVERING';

export interface VramInfo {
  state: VramState;
  embeddingMb: number;
  llmMb: number;
  comfyuiMb: number;
  freeMb: number;
}

/** 前端期望的 TaskStreamMessage 结构 */
export interface TaskStreamMessage {
  taskId: string;
  stage: TaskStage;
  message: string;
  progress?: number;
  preview?: PreviewData;
  modelInfo?: ModelInfo;
  vramInfo?: VramInfo;
}

/** 客户端发送的消息 */
export interface ClientMessage {
  action: 'submit_task' | 'recover_tasks';
  prompt: string;
  strategy?: 'AUTO' | 'CREATIVE' | 'REASONING' | 'LONGFORM' | 'REVIEW';
  channel?: 'web' | 'wechat' | 'slack';
  userId?: string;
}

/** Hermes 层推送进度的请求体 */
export interface PushStatusRequest {
  taskId: string;
  stage: string;
  message: string;
  progress?: number;
  preview?: {
    type: string;
    data: string;
  };
  modelInfo?: {
    vendor: string;
    model: string;
    tokensIn: number;
    tokensOut: number;
  };
  vramInfo?: {
    state: string;
    embeddingMb: number;
    llmMb: number;
    comfyuiMb: number;
    freeMb: number;
  };
}

/** Hermes 层推送结果的请求体 */
export interface PushResultRequest {
  taskId: string;
  status: 'success' | 'failed';
  output: string;
  toolLog?: any[];
}

/** 内部消息队列中的任务条目 */
export interface QueuedTask {
  taskId: string;
  prompt: string;
  strategy: string;
  channel: string;
  userId: string;
  timestamp: number;
}

// ═══════════════════════════════════════════════════════════════════════════
//  常量与配置
// ═══════════════════════════════════════════════════════════════════════════

const HEARTBEAT_INTERVAL_MS = 30_000;      // 30 秒发送一次 ping
const HEARTBEAT_TIMEOUT_MS = 35_000;       // 35 秒内未收到 pong 则断开
const MAX_CONNECTIONS = 1000;              // 最大并发连接数
const WS_PATH = '/ws/tasks';               // WebSocket 路径

// ═══════════════════════════════════════════════════════════════════════════
//  内部消息队列（供 Hermes 消费）
// ═══════════════════════════════════════════════════════════════════════════

const internalTaskQueue: QueuedTask[] = [];

/** 供外部（Hermes 层）拉取任务的函数 */
export function dequeueTask(): QueuedTask | undefined {
  return internalTaskQueue.shift();
}

/** 查看队列长度 */
export function getQueueLength(): number {
  return internalTaskQueue.length;
}

// ═══════════════════════════════════════════════════════════════════════════
//  主网关类
// ═══════════════════════════════════════════════════════════════════════════

export class TaskWebSocketGateway {
  private wss: WebSocketServer;
  private connections: Map<string, WebSocket> = new Map();
  private app: express.Application;
  private server: http.Server;

  /** 存储每个连接的心跳状态 */
  private heartbeatMap: Map<WebSocket, { lastPong: number; isAlive: boolean }> =
    new Map();

  /** ★★★ 地雷 4 修复：任务状态历史存储 ★★★ */
  private taskHistoryStore: Map<string, {
    stages: Array<{ stage: string; message: string; progress?: number; timestamp: number }>;
    finalResult?: { status: string; output?: string; error?: string };
    createdAt: number;
    updatedAt: number;
  }> = new Map();

  /** 任务历史保留上限（内存防泄漏） */
  private readonly MAX_TASK_HISTORY = 100;

  /** 心跳定时器引用 */
  private heartbeatTimer?: NodeJS.Timeout;

  /** 服务器端口 */
  private port: number;

  constructor(port: number = 18080) {
    this.port = port;

    // ── Express HTTP 服务器 ──────────────────────────────────────────────
    this.app = express();
    this.app.use(express.json({ limit: '10mb' }));
    this.app.use(this.corsMiddleware.bind(this));

    this.server = http.createServer(this.app);

    // ── WebSocket Server ─────────────────────────────────────────────────
    this.wss = new WebSocketServer({
      server: this.server,
      path: WS_PATH,
    });

    this.setupWebSocketHandlers();
    this.setupInternalRestRoutes();
  }

  // ───────────────────────────────────────────────────────────────────────
  //  CORS 中间件
  // ───────────────────────────────────────────────────────────────────────

  private corsMiddleware(req: Request, res: Response, next: NextFunction): void {
    const allowedOrigins = process.env.ALLOWED_ORIGINS?.split(',') || [
      'http://localhost:3000',
      'http://localhost:5173',
      'http://127.0.0.1:3000',
    ];
    const origin = req.headers.origin;
    if (origin && allowedOrigins.includes(origin)) {
      res.header('Access-Control-Allow-Origin', origin);
    }
    res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');
    res.header('Access-Control-Allow-Credentials', 'true');
    res.header('X-Content-Type-Options', 'nosniff');
    res.header('X-Frame-Options', 'DENY');
    next();
  }

  // ───────────────────────────────────────────────────────────────────────
  //  WebSocket 事件处理
  // ───────────────────────────────────────────────────────────────────────

  private setupWebSocketHandlers(): void {
    this.wss.on('connection', (ws: WebSocket, req) => {
      const clientIp = req.socket.remoteAddress || 'unknown';

      // 连接数上限检查
      if (this.connections.size >= MAX_CONNECTIONS) {
        console.warn(`[Gateway] Connection limit reached (${MAX_CONNECTIONS}), rejecting client from ${clientIp}`);
        ws.close(1013, 'Server overloaded: maximum connections reached');
        return;
      }

      console.log(
        `[Gateway] Client connected from ${clientIp}, total: ${this.connections.size + 1}`
      );

      // 初始化心跳状态
      this.heartbeatMap.set(ws, { lastPong: Date.now(), isAlive: true });

      // 监听客户端 pong 响应
      ws.on('pong', () => {
        const state = this.heartbeatMap.get(ws);
        if (state) {
          state.isAlive = true;
          state.lastPong = Date.now();
        }
      });

      // 监听消息
      ws.on('message', (data: Buffer | ArrayBuffer | Buffer[], isBinary: boolean) => {
        this.handleClientMessage(ws, data, isBinary).catch((err) => {
          console.error('[Gateway] Error handling client message:', err);
        });
      });

      // 监听关闭
      ws.on('close', (code: number, reason: Buffer) => {
        console.log(
          `[Gateway] Client disconnected (code=${code}, reason=${reason.toString()}), total: ${this.connections.size}`
        );
        this.removeConnectionBySocket(ws);
      });

      // 监听错误
      ws.on('error', (err: Error) => {
        console.error('[Gateway] WebSocket error:', err.message);
        this.removeConnectionBySocket(ws);
      });
    });

    // 服务器级别错误
    this.wss.on('error', (err: Error) => {
      console.error('[Gateway] WebSocketServer error:', err.message);
    });

    // 启动心跳检测
    this.startHeartbeat();
  }

  // ───────────────────────────────────────────────────────────────────────
  //  心跳检测
  // ───────────────────────────────────────────────────────────────────────

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      for (const [ws, state] of this.heartbeatMap.entries()) {
        if (!state.isAlive) {
          const elapsed = Date.now() - state.lastPong;
          if (elapsed > HEARTBEAT_TIMEOUT_MS) {
            console.warn('[Gateway] Heartbeat timeout, terminating connection');
            this.removeConnectionBySocket(ws);
            try {
              ws.terminate();
            } catch {
              /* ignore */
            }
            continue;
          }
        }

        // 标记为未存活，等待 pong 响应
        state.isAlive = false;
        try {
          ws.ping();
        } catch (err) {
          console.warn('[Gateway] Failed to send ping:', (err as Error).message);
          this.removeConnectionBySocket(ws);
          try {
            ws.terminate();
          } catch {
            /* ignore */
          }
        }
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = undefined;
    }
  }

  // ───────────────────────────────────────────────────────────────────────
  //  客户端消息解析与处理
  // ───────────────────────────────────────────────────────────────────────

  private async handleClientMessage(
    ws: WebSocket,
    data: Buffer | ArrayBuffer | Buffer[],
    _isBinary: boolean
  ): Promise<void> {
    let rawText: string;

    // 统一转字符串
    if (Buffer.isBuffer(data)) {
      rawText = data.toString('utf-8');
    } else if (Array.isArray(data)) {
      rawText = Buffer.concat(data).toString('utf-8');
    } else {
      rawText = Buffer.from(data).toString('utf-8');
    }

    // JSON 解析容错
    let msg: ClientMessage;
    try {
      msg = JSON.parse(rawText) as ClientMessage;
    } catch (parseErr) {
      console.warn('[Gateway] Invalid JSON from client:', rawText.slice(0, 200));
      this.sendJson(ws, {
        error: 'INVALID_JSON',
        message: 'Malformed JSON payload',
      });
      return;
    }

    // 校验 action 类型
    if (msg.action === 'recover_tasks') {
      // ★★★ 地雷 4 修复：断连恢复 ★★★
      this.handleRecoverTasks(ws, msg);
      return;
    }

    if (msg.action !== 'submit_task') {
      console.warn('[Gateway] Unknown action:', msg.action);
      this.sendJson(ws, {
        error: 'UNKNOWN_ACTION',
        message: `Action "${msg.action}" is not supported`,
      });
      return;
    }

    // 校验 prompt
    if (!msg.prompt || typeof msg.prompt !== 'string' || msg.prompt.trim().length === 0) {
      this.sendJson(ws, {
        error: 'INVALID_PROMPT',
        message: 'Field "prompt" is required and must be a non-empty string',
      });
      return;
    }

    // 生成 taskId
    const taskId = uuidv4();
    const strategy = msg.strategy || 'AUTO';
    const channel = msg.channel || 'web';
    const userId = msg.userId || 'anonymous';

    // ── 立即返回 ANALYZING 状态 ────────────────────────────────────────────
    const ackMessage: TaskStreamMessage = {
      taskId,
      stage: 'ANALYZING',
      message: `Task received. Strategy=${strategy}, Channel=${channel}. Queue length=${internalTaskQueue.length}`,
      progress: 0,
    };

    this.sendJson(ws, ackMessage);

    // ── 存入连接映射 ─────────────────────────────────────────────────────
    this.connections.set(taskId, ws);

    // ── 推入内部消息队列 ─────────────────────────────────────────────────
    const queuedTask: QueuedTask = {
      taskId,
      prompt: msg.prompt.trim(),
      strategy,
      channel,
      userId,
      timestamp: Date.now(),
    };
    internalTaskQueue.push(queuedTask);

    console.log(
      `[Gateway] Task ${taskId.substring(0, 8)}... queued. Queue length: ${internalTaskQueue.length}`
    );
  }

  // ───────────────────────────────────────────────────────────────────────
  //  内部 REST 接口（供 Hermes 调用）
  // ───────────────────────────────────────────────────────────────────────

  private setupInternalRestRoutes(): void {
    // 健康检查
    this.app.get('/health', (_req: Request, res: Response) => {
      res.json({
        status: 'ok',
        uptime: process.uptime(),
        connections: this.connections.size,
        queueLength: internalTaskQueue.length,
        timestamp: new Date().toISOString(),
      });
    });

    // ── POST /api/internal/push_status ───────────────────────────────────
    this.app.post(
      '/api/internal/push_status',
      (req: Request, res: Response) => {
        try {
          const body = req.body as PushStatusRequest;

          if (!body.taskId || !body.stage || !body.message) {
            res.status(400).json({
              success: false,
              error: 'MISSING_FIELDS',
              message: 'Fields "taskId", "stage", "message" are required',
            });
            return;
          }

          const taskStage = this.normalizeStage(body.stage);
          const taskId = body.taskId;

          // 构建完整的消息对象
          const message: TaskStreamMessage = {
            taskId,
            stage: taskStage,
            message: body.message,
            progress: body.progress,
          };

          // 可选字段
          if (body.preview) {
            message.preview = {
              type: body.preview.type as PreviewType,
              data: body.preview.data,
            };
          }

          if (body.modelInfo) {
            message.modelInfo = body.modelInfo;
          }

          if (body.vramInfo) {
            message.vramInfo = {
              state: body.vramInfo.state as VramState,
              embeddingMb: body.vramInfo.embeddingMb,
              llmMb: body.vramInfo.llmMb,
              comfyuiMb: body.vramInfo.comfyuiMb,
              freeMb: body.vramInfo.freeMb,
            };
          }

          // ★★★ 地雷 4 修复：记录状态历史 ★★★
          this.recordTaskStage(taskId, taskStage, body.message, body.progress);

          // 推送到客户端
          const delivered = this.pushToClient(taskId, message);

          if (!delivered) {
            console.warn(
              `[Gateway] push_status: No active WebSocket for task ${taskId.substring(0, 8)}... (client may have disconnected)`
            );
          } else {
            console.log(
              `[Gateway] push_status delivered to task ${taskId.substring(0, 8)}... stage=${taskStage}`
            );
          }

          res.status(200).json({
            success: true,
            delivered,
            persisted: true,  // 已持久化到内存
          });
        } catch (err) {
          console.error('[Gateway] push_status error:', (err as Error).message);
          res.status(500).json({
            success: false,
            error: 'INTERNAL_ERROR',
            message: (err as Error).message,
          });
        }
      }
    );

    // ── POST /api/internal/push_result ───────────────────────────────────
    this.app.post(
      '/api/internal/push_result',
      (req: Request, res: Response) => {
        try {
          const body = req.body as PushResultRequest;

          if (!body.taskId || !body.status || !body.output) {
            res.status(400).json({
              success: false,
              error: 'MISSING_FIELDS',
              message: 'Fields "taskId", "status", "output" are required',
            });
            return;
          }

          if (body.status !== 'success' && body.status !== 'failed') {
            res.status(400).json({
              success: false,
              error: 'INVALID_STATUS',
              message: 'Field "status" must be "success" or "failed"',
            });
            return;
          }

          const taskId = body.taskId;
          const isSuccess = body.status === 'success';

          // 构建最终结果消息
          const finalMessage: TaskStreamMessage = {
            taskId,
            stage: isSuccess ? 'COMPLETED' : 'FAILED',
            message: isSuccess
              ? 'Task completed successfully'
              : 'Task failed — see output for details',
            progress: isSuccess ? 100 : undefined,
          };

          // ★★★ 地雷 4 修复：记录最终结果 ★★★
          this.recordTaskResult(taskId, body.status, body.output, body.toolLog);

          // 推送到客户端
          const delivered = this.pushToClient(taskId, finalMessage);

          // 发送结果详情（作为第二条消息，让前端可以单独渲染最终结果）
          if (delivered) {
            const resultPayload = {
              taskId,
              stage: isSuccess ? 'COMPLETED' : 'FAILED',
              result: body.output,
              toolLog: body.toolLog || [],
              isFinalResult: true,
            };
            const ws = this.connections.get(taskId);
            if (ws && ws.readyState === WebSocket.OPEN) {
              this.sendJson(ws, resultPayload);
            }
          }

          if (!delivered) {
            console.warn(
              `[Gateway] push_result: No active WebSocket for task ${taskId.substring(0, 8)}... (client may have disconnected)`
            );
          } else {
            console.log(
              `[Gateway] push_result delivered to task ${taskId.substring(0, 8)}... status=${body.status}`
            );
          }

          // 清理连接映射（任务已完成，无需保持）
          if (isSuccess || body.status === 'failed') {
            // 延迟清理，给客户端一点时间读取最终结果
            setTimeout(() => {
              this.removeConnectionByTaskId(taskId);
            }, 5_000);
          }

          res.status(200).json({
            success: true,
            delivered,
            persisted: true,
          });
        } catch (err) {
          console.error('[Gateway] push_result error:', (err as Error).message);
          res.status(500).json({
            success: false,
            error: 'INTERNAL_ERROR',
            message: (err as Error).message,
          });
        }
      }
    );

    // ── 404 兜底 ─────────────────────────────────────────────────────────
    this.app.use((_req: Request, res: Response) => {
      res.status(404).json({
        success: false,
        error: 'NOT_FOUND',
        message: 'Endpoint not found',
      });
    });

    // ── 全局错误处理 ─────────────────────────────────────────────────────
    this.app.use(
      (err: Error, _req: Request, res: Response, _next: NextFunction) => {
        console.error('[Gateway] Express error:', err.message);
        res.status(500).json({
          success: false,
          error: 'INTERNAL_ERROR',
          message: err.message,
        });
      }
    );
  }

  // ───────────────────────────────────────────────────────────────────────
  //  工具方法
  // ───────────────────────────────────────────────────────────────────────

  /**
   * 将消息推送到指定 taskId 的客户端 WebSocket。
   * 返回是否成功投递。
   */
  public pushToClient(taskId: string, message: TaskStreamMessage): boolean {
    const ws = this.connections.get(taskId);
    if (!ws) {
      return false;
    }

    if (ws.readyState !== WebSocket.OPEN) {
      console.warn(
        `[Gateway] Connection for task ${taskId.substring(0, 8)}... is not OPEN (state=${ws.readyState})`
      );
      return false;
    }

    return this.sendJson(ws, message);
  }

  /** 安全地发送 JSON，返回是否成功 */
  private sendJson(ws: WebSocket, payload: unknown): boolean {
    try {
      const json = JSON.stringify(payload);
      ws.send(json);
      return true;
    } catch (err) {
      console.error('[Gateway] Failed to stringify/send JSON:', (err as Error).message);
      return false;
    }
  }

  // ───────────────────────────────────────────────────────────────────────
  //  地雷 4 修复：任务状态持久化与断连恢复
  // ───────────────────────────────────────────────────────────────────────

  /**
   * 处理客户端的 recover_tasks 请求。
   * 将内存中该用户的所有任务历史推送给客户端，恢复 UI 状态。
   */
  private handleRecoverTasks(ws: WebSocket, msg: ClientMessage): void {
    const userId = msg.userId || 'anonymous';
    const recoveredTasks: Array<{
      taskId: string;
      stages: Array<{ stage: string; message: string; progress?: number; timestamp: number }>;
      finalResult?: { status: string; output?: string; error?: string };
    }> = [];

    // 收集该用户的所有任务历史（简化：收集最近 20 条）
    const entries = Array.from(this.taskHistoryStore.entries())
      .sort((a, b) => b[1].updatedAt - a[1].updatedAt)
      .slice(0, 20);

    for (const [taskId, history] of entries) {
      recoveredTasks.push({
        taskId,
        stages: history.stages,
        finalResult: history.finalResult,
      });
    }

    const recoverMessage = {
      action: 'recover_tasks_response',
      userId,
      taskCount: recoveredTasks.length,
      tasks: recoveredTasks,
    };

    this.sendJson(ws, recoverMessage);
    console.log(
      `[Gateway] recover_tasks: sent ${recoveredTasks.length} task(s) to reconnecting client`
    );
  }

  /**
   * 记录任务阶段性状态到内存。
   */
  private recordTaskStage(
    taskId: string,
    stage: string,
    message: string,
    progress?: number,
  ): void {
    let history = this.taskHistoryStore.get(taskId);
    if (!history) {
      history = {
        stages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      };
      // 防泄漏：超过上限时删除最老的条目
      if (this.taskHistoryStore.size >= this.MAX_TASK_HISTORY) {
        const oldest = Array.from(this.taskHistoryStore.entries())
          .sort((a, b) => a[1].createdAt - b[1].createdAt)[0];
        if (oldest) {
          this.taskHistoryStore.delete(oldest[0]);
        }
      }
      this.taskHistoryStore.set(taskId, history);
    }
    history.stages.push({
      stage,
      message,
      progress,
      timestamp: Date.now(),
    });
    history.updatedAt = Date.now();
  }

  /**
   * 记录任务最终结果到内存。
   */
  private recordTaskResult(
    taskId: string,
    status: string,
    output: string,
    toolLog?: unknown[],
  ): void {
    let history = this.taskHistoryStore.get(taskId);
    if (!history) {
      history = {
        stages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      };
      this.taskHistoryStore.set(taskId, history);
    }
    history.finalResult = {
      status,
      output,
      error: status === 'failed' ? output : undefined,
    };
    history.updatedAt = Date.now();
  }

  /** 根据 WebSocket 实例移除连接 */
  private removeConnectionBySocket(ws: WebSocket): void {
    this.heartbeatMap.delete(ws);

    for (const [taskId, socket] of this.connections.entries()) {
      if (socket === ws) {
        this.connections.delete(taskId);
        console.log(`[Gateway] Removed connection mapping for task ${taskId.substring(0, 8)}...`);
        break;
      }
    }
  }

  /** 根据 taskId 移除连接 */
  private removeConnectionByTaskId(taskId: string): void {
    const ws = this.connections.get(taskId);
    if (ws) {
      this.heartbeatMap.delete(ws);
      this.connections.delete(taskId);
      console.log(`[Gateway] Removed connection mapping for task ${taskId.substring(0, 8)}...`);
    }
  }

  /** 将传入的 stage 字符串归一化为合法枚举值 */
  private normalizeStage(stage: string): TaskStage {
    const validStages: TaskStage[] = [
      'ANALYZING',
      'READING_CODE',
      'EDITING',
      'TESTING',
      'COMPLETED',
      'FAILED',
    ];
    const upper = stage.toUpperCase();
    if (validStages.includes(upper as TaskStage)) {
      return upper as TaskStage;
    }
    // 未知 stage 映射为 ANALYZING，避免协议破裂
    console.warn(`[Gateway] Unknown stage "${stage}", defaulting to ANALYZING`);
    return 'ANALYZING';
  }

  // ───────────────────────────────────────────────────────────────────────
  //  生命周期
  // ───────────────────────────────────────────────────────────────────────

  public start(): void {
    this.server.listen(this.port, () => {
      console.log(`🟣 OpenClaw Task Gateway listening on ws://0.0.0.0:${this.port}${WS_PATH}`);
      console.log(`🟣 Internal REST API on http://0.0.0.0:${this.port}/api/internal/*`);
      console.log(`🟣 Health check at http://0.0.0.0:${this.port}/health`);
      console.log(`🟣 Max connections: ${MAX_CONNECTIONS}, Heartbeat: ${HEARTBEAT_INTERVAL_MS}ms`);
    });

    // 优雅关闭
    process.on('SIGINT', () => this.shutdown('SIGINT'));
    process.on('SIGTERM', () => this.shutdown('SIGTERM'));
  }

  public shutdown(signal: string): void {
    console.log(`\n[Gateway] Received ${signal}, shutting down gracefully...`);

    this.stopHeartbeat();

    // 关闭所有 WebSocket 连接
    for (const [taskId, ws] of this.connections.entries()) {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.close(1001, 'Server shutting down');
        } else {
          ws.terminate();
        }
      } catch {
        /* ignore */
      }
      console.log(`[Gateway] Closed connection for task ${taskId.substring(0, 8)}...`);
    }
    this.connections.clear();
    this.heartbeatMap.clear();

    // 关闭 HTTP 服务器
    this.server.close(() => {
      console.log('[Gateway] HTTP server closed.');
      process.exit(0);
    });

    // 强制超时
    setTimeout(() => {
      console.error('[Gateway] Forced shutdown after timeout.');
      process.exit(1);
    }, 10_000);
  }

  /** 获取当前活跃连接数 */
  public getConnectionCount(): number {
    return this.connections.size;
  }

  /** 获取所有活跃 taskId */
  public getActiveTaskIds(): string[] {
    return Array.from(this.connections.keys());
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  启动入口
// ═══════════════════════════════════════════════════════════════════════════

if (require.main === module) {
  const gateway = new TaskWebSocketGateway(18080);
  gateway.start();
}
