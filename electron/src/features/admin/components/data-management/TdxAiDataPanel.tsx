import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Input, message, Space, Switch, Tag } from 'antd';
import {
    ApiOutlined,
    CloudServerOutlined,
    ReloadOutlined,
    SafetyCertificateOutlined,
    ThunderboltOutlined,
} from '@ant-design/icons';
import { adminService } from '../../services/adminService';

interface TokenStatus {
    configured: boolean;
    masked: string;
}

interface TdxAiDataConfig {
    dir: string;
    enabled: boolean;
    dir_ready: boolean;
    socket_path: string;
    shard_count?: number;
    max_requests_per_window?: number;
    token: TokenStatus;
}

interface GateStatus {
    cooldown_active: boolean;
    cooldown_remaining_s: number;
    window_requests: number;
    max_requests_per_window: number;
    window_age_s: number;
}

interface WorkerStatus {
    worker?: string;
    worker_state?: string;
    sdk_ready?: boolean;
    sdk_error?: string | null;
    pid?: number;
    gate?: GateStatus;
    counters?: { requests?: number; ok?: number; rate_limited?: number };
    /** 分片集群（SDK 单进程订阅上限 100，热集 >100 需多分片） */
    shard_count?: number;
    shards_up?: string;
}

interface SelfcheckResult {
    ok: boolean;
    symbol: string;
    latency_ms?: number;
    error?: string;
    error_code?: string;
    retry_after_s?: number;
    field_count?: number;
    [key: string]: unknown;
}

/**
 * TdxAiData 实时数据源配置面板（P6 T-P6-01）。
 *
 * 说明（与后端实测一致）：该通道免 Windows 客户端；**订阅推送**为实时主源（五档全字段、
 * 零请求配额），**请求接口**按预算窗口限流（订阅不吃配额）——面板如实展示配额窗口与
 * 冷却倒计时，窗口上限/分片数均可在此调整（保存后重启全分片生效）。
 */
export const TdxAiDataPanel: React.FC = () => {
    const [cfg, setCfg] = useState<TdxAiDataConfig | null>(null);
    const [worker, setWorker] = useState<WorkerStatus>({});
    const [dirInput, setDirInput] = useState('');
    const [tokenInput, setTokenInput] = useState('');
    const [enabledInput, setEnabledInput] = useState(true);
    const [shardInput, setShardInput] = useState('');
    const [maxReqInput, setMaxReqInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [checking, setChecking] = useState(false);
    const [checkResult, setCheckResult] = useState<SelfcheckResult | null>(null);

    const applyConfig = useCallback((data: any) => {
        const c: TdxAiDataConfig | undefined = data?.config;
        if (c) {
            setCfg(c);
            setDirInput(c.dir || '');
            setEnabledInput(!!c.enabled);
            setShardInput(c.shard_count ? String(c.shard_count) : '');
            setMaxReqInput(c.max_requests_per_window ? String(c.max_requests_per_window) : '');
        }
        setWorker(data?.worker ?? {});
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await adminService.getTdxAiDataConfig();
            applyConfig(resp?.data);
        } catch (err: unknown) {
            // 未部署/未授权时不阻塞页面：给出中性提示
            message.warning(err instanceof Error ? `加载数据源配置失败: ${err.message}` : '加载数据源配置失败');
        } finally {
            setLoading(false);
        }
    }, [applyConfig]);

    useEffect(() => {
        void load();
    }, [load]);

    const handleSave = async () => {
        setSaving(true);
        try {
            const payload: {
                dir?: string;
                enabled?: boolean;
                token?: string;
                shard_count?: number;
                max_requests_per_window?: number;
            } = {
                enabled: enabledInput,
            };
            if (dirInput.trim() && dirInput.trim() !== cfg?.dir) payload.dir = dirInput.trim();
            if (tokenInput.trim()) payload.token = tokenInput.trim();
            const shardNum = Number(shardInput);
            if (shardInput.trim() && Number.isFinite(shardNum) && shardNum >= 1 && shardNum !== cfg?.shard_count) {
                payload.shard_count = Math.floor(shardNum);
            }
            const maxReqNum = Number(maxReqInput);
            if (
                maxReqInput.trim() &&
                Number.isFinite(maxReqNum) &&
                maxReqNum >= 1 &&
                maxReqNum !== cfg?.max_requests_per_window
            ) {
                payload.max_requests_per_window = Math.floor(maxReqNum);
            }
            const resp = await adminService.saveTdxAiDataConfig(payload);
            applyConfig(resp?.data);
            setTokenInput('');
            message.success(
                resp?.data?.token_written
                    ? '配置已保存（Token 已写入 ini，worker 已重启）'
                    : '配置已保存',
            );
            void load();
        } catch (err: unknown) {
            message.error(err instanceof Error ? `保存失败: ${err.message}` : '保存失败');
        } finally {
            setSaving(false);
        }
    };

    const handleSelfcheck = async () => {
        setChecking(true);
        setCheckResult(null);
        try {
            const resp = await adminService.tdxAiDataSelfcheck();
            const data: SelfcheckResult = resp?.data;
            setCheckResult(data);
            setWorker({
                ...worker,
                ...(data?.worker as WorkerStatus),
                gate: (data?.gate as GateStatus | undefined) ?? worker.gate,
            });
            if (data?.ok) message.success('连通性自检通过');
            else if (data?.error_code === 'rate_limited') message.warning('配额窗口冷却中（见下方倒计时）');
            else message.warning(data?.error || '自检未通过');
        } catch (err: unknown) {
            message.error(err instanceof Error ? `自检失败: ${err.message}` : '自检失败');
        } finally {
            setChecking(false);
        }
    };

    const gate = worker?.gate;
    const maxReq = gate?.max_requests_per_window ?? cfg?.max_requests_per_window ?? 30;
    const usedReq = Math.min(gate?.window_requests ?? 0, maxReq);
    // 预算格逐格画只在格数可读时用；上限调到几十时改走「已用/上限」数字 + 比例条
    const showPips = maxReq <= 12;
    const usedPct = maxReq > 0 ? Math.min(100, (usedReq / maxReq) * 100) : 0;
    const workerDegraded = worker?.worker === 'degraded';
    const workerUp = worker?.worker === 'up' || workerDegraded;
    const sdkReady = worker?.sdk_ready === true;

    return (
        <div className="mt-4 p-4 rounded-xl border border-dashed border-sky-400/60 bg-sky-50/40 space-y-3">
            {/* 标题 + worker 状态 */}
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="text-xs font-semibold text-sky-700 flex items-center">
                    <ThunderboltOutlined className="mr-1" />
                    通达信 TdxAiData 实时数据源（免 Windows 客户端；订阅推送=实时主源，请求接口低频补充）
                </span>
                <Space size={6}>
                    <Tag
                        className="m-0 rounded-full px-2 font-bold"
                        color={workerUp ? (workerDegraded ? 'warning' : 'success') : worker ? 'default' : undefined}
                    >
                        {workerUp ? (workerDegraded ? 'worker 降级运行' : 'worker 运行中') : 'worker 未运行'}
                    </Tag>
                    {workerUp && (worker?.shard_count ?? 1) > 1 && (
                        <Tag className="m-0 rounded-full px-2 font-bold" color={workerDegraded ? 'warning' : 'blue'}>
                            分片 {worker?.shards_up ?? '—'}
                        </Tag>
                    )}
                    {workerUp && (
                        <Tag className="m-0 rounded-full px-2 font-bold" color={sdkReady ? 'processing' : 'error'}>
                            {sdkReady ? 'SDK 就绪' : 'SDK 异常'}
                        </Tag>
                    )}
                    <Button
                        size="small"
                        type="text"
                        icon={<ReloadOutlined />}
                        loading={loading}
                        onClick={() => void load()}
                    >
                        刷新
                    </Button>
                </Space>
            </div>

            {/* 状态条：目录 / Token / 配额窗口 */}
            <div className="flex items-center gap-4 flex-wrap text-xs text-slate-600">
                <span className="flex items-center gap-1">
                    <CloudServerOutlined className="text-slate-400" />
                    目录
                    <code className="px-1 rounded bg-white/70 text-[11px]">{cfg?.dir || '—'}</code>
                    <Tag className="m-0 rounded-full px-1.5 text-[10px] font-bold" color={cfg?.dir_ready ? 'success' : 'error'}>
                        {cfg?.dir_ready ? '就绪' : '缺文件'}
                    </Tag>
                </span>
                <span className="flex items-center gap-1">
                    <SafetyCertificateOutlined className="text-slate-400" />
                    Token
                    <code className="px-1 rounded bg-white/70 text-[11px]">
                        {cfg?.token?.configured ? cfg.token.masked : '未配置'}
                    </code>
                </span>
                <span className="flex items-center gap-1">
                    <ApiOutlined className="text-slate-400" />
                    请求配额窗口
                    <span className="inline-flex items-center gap-1">
                        {showPips ? (
                            <span className="inline-flex gap-0.5">
                                {Array.from({ length: maxReq }).map((_, i) => (
                                    <span
                                        key={i}
                                        className={`inline-block w-2 h-3 rounded-sm ${i < usedReq ? 'bg-sky-500' : 'bg-slate-200'}`}
                                    />
                                ))}
                            </span>
                        ) : (
                            <span className="inline-block w-16 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                                <span
                                    className="block h-full bg-sky-500 transition-[width] duration-300"
                                    style={{ width: `${usedPct}%` }}
                                />
                            </span>
                        )}
                        <span className="text-[11px] text-slate-500">
                            {usedReq}/{maxReq}
                            {gate?.cooldown_active ? ` · 冷却剩 ${Math.ceil(gate.cooldown_remaining_s)}s` : ''}
                        </span>
                    </span>
                </span>
            </div>

            {/* 配置表单 */}
            <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                <Input
                    className="md:col-span-4"
                    size="small"
                    addonBefore="安装目录"
                    value={dirInput}
                    onChange={(e) => setDirInput(e.target.value)}
                    placeholder="/opt/tdx-aidata"
                />
                <Input.Password
                    className="md:col-span-4"
                    size="small"
                    addonBefore="Token"
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder={cfg?.token?.configured ? '已配置（留空不修改）' : '粘贴通达信 Token'}
                />
                <Input
                    className="md:col-span-2"
                    size="small"
                    addonBefore="分片数"
                    value={shardInput}
                    onChange={(e) => setShardInput(e.target.value)}
                    placeholder={cfg?.shard_count ? String(cfg.shard_count) : '1'}
                />
                <Input
                    className="md:col-span-2"
                    size="small"
                    addonBefore="窗口上限"
                    value={maxReqInput}
                    onChange={(e) => setMaxReqInput(e.target.value)}
                    placeholder={cfg?.max_requests_per_window ? String(cfg.max_requests_per_window) : '30'}
                />
                <div className="md:col-span-12 flex items-center gap-3">
                    <span className="text-xs text-slate-500 flex items-center gap-1">
                        启用
                        <Switch size="small" checked={enabledInput} onChange={setEnabledInput} />
                    </span>
                    <Button size="small" type="primary" loading={saving} onClick={() => void handleSave()}>
                        保存并重启
                    </Button>
                    <Button size="small" loading={checking} onClick={() => void handleSelfcheck()}>
                        连通性自检
                    </Button>
                    <span className="text-[11px] text-slate-400">
                        分片数经验值 = ceil(热集规模 / 100)；分片数/窗口上限为构造期配置，保存后重启全分片生效
                    </span>
                </div>
            </div>

            {/* 自检结果（如实呈现：通过 / 限流冷却 / 失败原因） */}
            {checkResult && (
                <Alert
                    type={checkResult.ok ? 'success' : checkResult.error_code === 'rate_limited' ? 'warning' : 'error'}
                    showIcon
                    className="rounded-lg"
                    message={
                        checkResult.ok
                            ? `自检通过：${checkResult.symbol} 取回实时快照（${checkResult.latency_ms ?? '-'} ms，${checkResult.field_count ?? 0} 个字段）`
                            : checkResult.error_code === 'rate_limited'
                              ? `配额冷却中：${checkResult.error || 'Token Insufficient'}（约 ${Math.ceil(Number(checkResult.retry_after_s) || 0)}s 后可重试）`
                              : `自检未通过：${checkResult.error || '未知原因'}`
                    }
                    description={
                        checkResult.ok ? (
                            <span className="text-xs text-slate-500">
                                样例字段：
                                {['Now', 'price', 'PreClose', 'pre_close', 'Open']
                                    .filter((k) => checkResult[k] !== undefined)
                                    .map((k) => `${k}=${String(checkResult[k])}`)
                                    .join('  ') || '（见接口原始响应）'}
                            </span>
                        ) : undefined
                    }
                />
            )}

            <div className="text-[11px] text-slate-400">
                说明：订阅推送（五档盘口全字段）为盘中实时主源、不占请求配额；请求接口（K 线/分时/分笔）
                受「窗口上限」约束，超限快速失败并进入冷却（冷却时长翻倍，180s 起、600s 封顶）。
                自检消耗 1 次窗口配额。
            </div>
        </div>
    );
};

export default TdxAiDataPanel;
