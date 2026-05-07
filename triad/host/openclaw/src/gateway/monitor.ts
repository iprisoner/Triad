import { Router } from 'express';
import { exec } from 'child_process';
import { promisify } from 'util';
import * as os from 'os';

const execAsync = promisify(exec);

const monitorRouter = Router();

/* ------------------------------------------------------------------ */
/*  类型定义                                                           */
/* ------------------------------------------------------------------ */

interface GPUStatus {
    name: string;
    memoryUsed: number;      // MB
    memoryTotal: number;     // MB
    memoryPercent: number;   // 0-100
    gpuUtilization: number;  // 0-100
    temperature: number;     // °C
}

interface ContainerInfo {
    name: string;
    status: string;
    health: 'healthy' | 'unhealthy' | 'unknown';
    ports: string;
}

interface LlamaServerStatus {
    running: boolean;
    mode: 'GPU' | 'CPU' | 'unknown';
    speed: number;   // tok/s，当后端暴露 slots 接口时可获取
}

interface CPUStatus {
    usage: number;   // 百分比
    cores: number;
}

interface MemoryStatus {
    used: number;    // MB
    total: number;   // MB
    percent: number;
}

export interface SystemStatus {
    timestamp: string;
    gpu: GPUStatus;
    containers: ContainerInfo[];
    llama_server: LlamaServerStatus;
    cpu: CPUStatus;
    memory: MemoryStatus;
}

/* ------------------------------------------------------------------ */
/*  主路由                                                             */
/* ------------------------------------------------------------------ */

/**
 * GET /api/system/status
 * 聚合所有探针数据，返回完整的系统状态 JSON
 */
monitorRouter.get('/status', async (req, res) => {
    try {
        const [gpuR, containersR, llamaR, cpuR, memR] = await Promise.allSettled([
            getGPUStatus(),
            getDockerContainers(),
            getLlamaStatus(),
            getCPUStatus(),
            getMemoryStatus(),
        ]);

        const gpu = gpuR.status === "fulfilled" ? gpuR.value : { name: 'GPU 不可用', memoryUsed: 0, memoryTotal: 1, memoryPercent: 0, gpuUtilization: 0, temperature: 0 };
        const containers = containersR.status === "fulfilled" ? containersR.value : [];
        const llama_server = llamaR.status === "fulfilled" ? llamaR.value : { running: false, mode: 'unknown' as const, speed: 0 };
        const cpu = cpuR.status === "fulfilled" ? cpuR.value : { usage: 0, cores: 0 };
        const memory = memR.status === "fulfilled" ? memR.value : { used: 0, total: 0, percent: 0 };

        const status: SystemStatus = {
            timestamp: new Date().toISOString(),
            gpu,
            containers,
            llama_server,
            cpu,
            memory,
        };

        res.json({ success: true, data: status });
    } catch (error) {
        console.error('[monitor] /status 聚合失败:', error);
        res.status(500).json({
            success: false,
            error: 'System status temporarily unavailable',
        });
    }
});

/* ------------------------------------------------------------------ */
/*  GPU 探针（nvidia-smi）                                             */
/* ------------------------------------------------------------------ */

async function getGPUStatus(): Promise<GPUStatus> {
    try {
        const { stdout } = await execAsync(
            'nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu ' +
            '--format=csv,noheader,nounits',
            { timeout: 5000 },
        );

        const lines = stdout.trim().split('\n').filter(Boolean);
        if (lines.length === 0) throw new Error('nvidia-smi 无输出');

        // 只取第一块 GPU 的数据
        const parts = lines[0].split(',').map((s) => s.trim());
        const [name, memUsedStr, memTotalStr, gpuUtilStr, tempStr] = parts;

        const memUsed = parseInt(memUsedStr, 10) || 0;
        const memTotal = parseInt(memTotalStr, 10) || 1;

        return {
            name: name || 'NVIDIA GPU',
            memoryUsed: memUsed,
            memoryTotal: memTotal,
            memoryPercent: Math.round((memUsed / memTotal) * 100),
            gpuUtilization: parseInt(gpuUtilStr, 10) || 0,
            temperature: parseInt(tempStr, 10) || 0,
        };
    } catch (err) {
        // nvidia-smi 不可用（非 NVIDIA 环境 / 驱动未装 / 命令不存在）时的降级返回
        return {
            name: 'GPU 不可用',
            memoryUsed: 0,
            memoryTotal: 1,
            memoryPercent: 0,
            gpuUtilization: 0,
            temperature: 0,
        };
    }
}

/* ------------------------------------------------------------------ */
/*  Docker 容器探针                                                    */
/* ------------------------------------------------------------------ */

async function getDockerContainers(): Promise<ContainerInfo[]> {
    try {
        const { stdout } = await execAsync(
            'docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}"',
            { timeout: 5000 },
        );

        return stdout
            .trim()
            .split('\n')
            .filter(Boolean)
            .map((line) => {
                const [name, status, ports] = line.split('|');
                const rawStatus = (status || 'unknown').toLowerCase();
                let health: ContainerInfo['health'] = 'unknown';
                if (rawStatus.includes('healthy')) health = 'healthy';
                else if (rawStatus.includes('unhealthy')) health = 'unhealthy';

                return {
                    name: name || 'unknown',
                    status: status || 'unknown',
                    health,
                    ports: ports || '',
                };
            });
    } catch {
        // docker 命令不存在或无权限时返回空数组，不抛错
        return [];
    }
}

/* ------------------------------------------------------------------ */
/*  llama-server 状态探针                                              */
/* ------------------------------------------------------------------ */

async function getLlamaStatus(): Promise<LlamaServerStatus> {
    try {
        // 1) 先探测 /health 端点确认存活
        const { stdout } = await execAsync(
            `curl -s http://\${process.env.LLAMA_HOST || 'localhost'}:\${process.env.LLAMA_PORT || '18000'}/health`,
            { timeout: 3000 },
        );
        const healthData = JSON.parse(stdout);
        const isHealthy = healthData.status === 'ok';

        if (!isHealthy) {
            return { running: false, mode: 'unknown', speed: 0 };
        }

        // 2) 尝试获取 slots 信息以判断 GPU/CPU 模式及推理速度
        let mode: LlamaServerStatus['mode'] = 'unknown';
        let speed = 0;

        try {
            const { stdout: slotStdout } = await execAsync(
                `curl -s http://\${process.env.LLAMA_HOST || 'localhost'}:\${process.env.LLAMA_PORT || '18000'}/slots`,
                { timeout: 3000 },
            );
            const slotsData = JSON.parse(slotStdout);

            // 简单启发：slots 中有 "n_decode" 或 "tokens_predicted" 字段时视为 GPU 模式
            const hasGpuIndicator =
                JSON.stringify(slotsData).includes('cuda') ||
                JSON.stringify(slotsData).includes('gpu');
            mode = hasGpuIndicator ? 'GPU' : 'CPU';

            // 若 slots 返回了处理速度（tok/s），取第一个 slot 的 speed 字段
            if (Array.isArray(slotsData) && slotsData[0]?.speed) {
                speed = parseFloat(slotsData[0].speed) || 0;
            }
        } catch {
            // slots 接口可能不存在，不影响健康状态
            mode = 'GPU'; // 默认识别为 GPU，因为 /health 已通过
        }

        return { running: true, mode, speed };
    } catch {
        // 连接失败视为未运行
        return { running: false, mode: 'unknown', speed: 0 };
    }
}

/* ------------------------------------------------------------------ */
/*  CPU 探针                                                           */
/* ------------------------------------------------------------------ */

async function getCPUStatus(): Promise<CPUStatus> {
    const cores = os.cpus().length;

    try {
        // Linux: 使用 top 获取一次快照的 CPU 占用率
        const { stdout } = await execAsync(
            "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1",
            { timeout: 3000 },
        );
        const usage = parseFloat(stdout.trim()) || 0;
        return { usage, cores };
    } catch {
        // 降级：回退到 Node.js os.loadavg() 估算
        const loadAvg = os.loadavg()[0];
        const usage = Math.min(Math.round((loadAvg / cores) * 100), 100);
        return { usage, cores };
    }
}

/* ------------------------------------------------------------------ */
/*  内存探针                                                           */
/* ------------------------------------------------------------------ */

async function getMemoryStatus(): Promise<MemoryStatus> {
    try {
        const { stdout } = await execAsync(
            "free -m | grep '^Mem:' | awk '{print $3, $2}'",
            { timeout: 3000 },
        );
        const parts = stdout.trim().split(/\s+/).map(Number);
        const used = parts[0] || 0;
        const total = parts[1] || 1;

        return {
            used,
            total,
            percent: Math.round((used / total) * 100),
        };
    } catch {
        // 降级：使用 Node.js os.totalmem() / freemem()
        const total = Math.round(os.totalmem() / 1024 / 1024); // MB
        const free = Math.round(os.freemem() / 1024 / 1024);   // MB
        const used = total - free;
        return {
            used,
            total,
            percent: Math.round((used / total) * 100),
        };
    }
}

/* ------------------------------------------------------------------ */
/*  导出                                                               */
/* ------------------------------------------------------------------ */

export { monitorRouter };
export default monitorRouter;
