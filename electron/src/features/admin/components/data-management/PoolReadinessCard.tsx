import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Spin, Tag, Tooltip, Typography } from 'antd';
import {
    CheckCircleFilled,
    ClockCircleOutlined,
    CloseCircleFilled,
    MinusCircleFilled,
    ReloadOutlined,
    ThunderboltFilled,
} from '@ant-design/icons';
import {
    dataPlatformService,
    PoolReadiness,
    PoolReadinessStage,
} from '../../services/dataPlatformService';
import { describeError } from '../quantdb/utils';

const { Text } = Typography;

/** 五段的固定顺序（与后端 `pool_readiness.STAGE_KEYS` 同一契约）。 */
const STAGE_ORDER: PoolReadinessStage['key'][] = [
    'kline',
    'alpha_library',
    'custom_dataset',
    'inference',
    'pool',
];

interface StageVisual {
    icon: React.ReactNode;
    ring: string;
    text: string;
    hint: string;
}

/**
 * 一段的视觉与话术。
 *
 * 三态必须分开，尤其第 2 条：方向闸判看空 → 池按设计清空，**不是故障**。把它画成
 * 红的，运维几天内就会开始无视这张卡（10-12 复盘判「强烈看空」，健康链跑出来也是这个形状）。
 */
function visualOf(stage: PoolReadinessStage): StageVisual {
    if (stage.ok && stage.by_design) {
        return {
            icon: <MinusCircleFilled />,
            ring: 'border-slate-300 text-slate-500 bg-slate-50',
            text: '按设计清空',
            hint: '方向闸判看空，候选池本就应该为空——正常',
        };
    }
    if (stage.ok) {
        return {
            icon: <CheckCircleFilled />,
            ring: 'border-emerald-300 text-emerald-600 bg-emerald-50',
            text: '就绪',
            hint: '已就绪',
        };
    }
    return {
        icon: <CloseCircleFilled />,
        ring: 'border-rose-300 text-rose-600 bg-rose-50',
        text: '未就绪',
        hint: '未就绪',
    };
}

function StageCell({
    stage,
    blocked,
    stale,
}: {
    stage: PoolReadinessStage;
    blocked: boolean;
    /** 上游断了之后的下游段：不是它自己的锅，弱化显示。 */
    stale: boolean;
}) {
    const v = visualOf(stage);
    const dim = stale && !stage.ok;
    return (
        <Tooltip title={`${v.hint}｜${stage.detail}`}>
            <div
                className={[
                    'flex-1 min-w-[112px] px-2 py-1.5 rounded-md border transition-colors',
                    v.ring,
                    blocked ? 'ring-2 ring-rose-400/70 shadow-sm' : '',
                    dim ? 'opacity-45 saturate-50' : '',
                ].join(' ')}
            >
                <div className="flex items-center gap-1 text-[11px] font-semibold whitespace-nowrap">
                    <span className="text-[13px] leading-none">{v.icon}</span>
                    <span className="truncate">{stage.label}</span>
                </div>
                <div className="mt-0.5 text-[10px] leading-tight opacity-90 break-all">
                    {stage.detail}
                </div>
            </div>
        </Tooltip>
    );
}

/**
 * 补池链就绪状态卡（后台「数据管理」页，A 股）。
 *
 * 为什么有这张卡：2026-10-13 候选池整天没产出，第一个告警是**开盘后** 09:35 决策轮
 * 才发的（`empty_pool`），当天开仓机会已经错过。这卡把同一条链的五段（日K → alpha 因子库
 * → 自定义因子集 → 当日推理批 → 候选池）摊在一次屏幕里，卡在哪一段一眼可见。
 *
 * 判据不在前端：数据来自 `GET /admin/data-platform/pool-readiness`，与宿主盘前告警脚本
 * 共用 `backend/shared/pool_readiness.py` 一份实现——两处各写一套必然漂移。
 */
export const PoolReadinessCard: React.FC = () => {
    const [report, setReport] = useState<PoolReadiness | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setReport(await dataPlatformService.getPoolReadiness('A'));
        } catch (e) {
            // 取不到就明说取不到——**不许**在数据缺失时画成「就绪」（本卡存在的意义就是
            // 让「池为什么没有」可见，谎报就绪比报错更坏）。
            setReport(null);
            setError(`补池链状态读取失败：${describeError(e)}`);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const stages = report
        ? STAGE_ORDER.map((k) => report.stages.find((s) => s.key === k)).filter(
              (s): s is PoolReadinessStage => !!s,
          )
        : [];
    const blockedIdx = report?.blocked_stage
        ? stages.findIndex((s) => s.key === report.blocked_stage)
        : -1;

    const shell = 'mt-4 p-3 rounded-lg border border-dashed bg-gray-50/60';

    if (error) {
        return (
            <div className={`${shell} border-rose-300`}>
                <div className="text-xs font-semibold text-rose-700 flex items-center">
                    <CloseCircleFilled className="mr-1" />
                    补池链就绪（状态读取失败——不代表链路正常）
                </div>
                <div className="mt-1 text-[11px] text-rose-600 break-all">{error}</div>
                <Button
                    size="small"
                    className="mt-2"
                    icon={<ReloadOutlined />}
                    loading={loading}
                    onClick={() => void load()}
                >
                    重试
                </Button>
            </div>
        );
    }

    return (
        <div className={`${shell} ${report?.ok ? 'border-emerald-300/70' : 'border-amber-400/70'}`}>
            <div className="flex items-center justify-between mb-2 gap-2">
                <span className="text-xs font-semibold text-gray-700 flex items-center">
                    <ThunderboltFilled className="mr-1 text-amber-500" />
                    补池链就绪
                    {report && (
                        <Text code className="ml-2 text-[11px]">
                            目标交易日 {report.target}
                        </Text>
                    )}
                </span>
                <Button
                    type="link"
                    size="small"
                    className="text-xs p-0"
                    icon={<ReloadOutlined />}
                    loading={loading}
                    onClick={() => void load()}
                >
                    刷新
                </Button>
            </div>

            {!report && loading && (
                <div className="text-[11px] text-gray-500 flex items-center">
                    <Spin size="small" className="mr-2" />
                    正在读取补池链状态…
                </div>
            )}

            {report && (
                <>
                    <div className="flex flex-wrap items-stretch gap-1.5">
                        {stages.map((s, i) => (
                            <StageCell
                                key={s.key}
                                stage={s}
                                blocked={i === blockedIdx}
                                stale={blockedIdx >= 0 && i > blockedIdx}
                            />
                        ))}
                    </div>

                    <div className="mt-2 text-[11px] leading-relaxed">
                        {report.ok ? (
                            report.by_design ? (
                                <span className="text-slate-600">
                                    链路完好，候选池按<b>方向闸</b>清空（复盘判看空，属正常）。
                                </span>
                            ) : (
                                <span className="text-emerald-700">
                                    链路完好，今日候选池已就绪。
                                </span>
                            )
                        ) : (
                            <span className="text-rose-700">
                                链路断在<b>{report.blocked_label || report.blocked_stage}</b>
                                ——开盘后决策轮会因「候选池非成员」拦下全部新开仓。
                            </span>
                        )}
                    </div>

                    {report.target_degraded && (
                        <Alert
                            type="warning"
                            showIcon
                            className="mt-2"
                            message={
                                <span className="text-[11px]">
                                    交易日历不可用（来源 {report.target_source}），目标日按
                                    <b>工作日启发式</b>推出——周中长假会判错，请人工核对。
                                </span>
                            }
                        />
                    )}

                    {!report.target_degraded && (
                        <div className="mt-1 text-[10px] text-gray-400">
                            目标日来源：{report.target_source}｜日K 最新 dt={report.kline_dt || '—'}
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default PoolReadinessCard;
