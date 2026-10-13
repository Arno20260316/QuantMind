import React from 'react';
import { Activity, AlertTriangle, Check, Play, RefreshCw, Square, Wifi, WifiOff, X } from 'lucide-react';
import { Select } from 'antd';
import type { RealTradingStatus } from '../../../../../services/realTradingService';
import type { StrategyFile } from '../../../../../types/backtest/strategy';
import { RUN_STATE_META } from '../topologyTypes';
import type { RunState } from '../topologyTypes';
import { modeCopy } from '../../../utils/tradingModeCopy';
import { fmtBeijingClock } from '../../../../../utils/timeBeijing';
import type { TradingMode } from '../../../utils/tradingModeCopy';
import { describeMarketMismatch } from '../../../utils/strategyMarket';
import type { StartEligibilityState } from '../hooks/useStartEligibility';

const MARKET_LABELS: Record<string, string> = {
    CN: 'A 股',
    HK: '港股',
    US: '美股',
    CRYPTO: '区块链',
    FUTURES: '期货',
};

const MARKET_BROKER_LABEL: Record<string, string> = {
    CN: '通达信实盘交易',
    HK: '券商实盘交易（富途/老虎/IB）',
    US: '券商实盘交易（老虎/IB/富途）',
    FUTURES: '券商实盘交易（IB）',
    CRYPTO: '暂无券商通道',
};

interface CommandBarProps {
    mode: TradingMode;
    market: string;
    status: RealTradingStatus | null;
    loading: boolean;
    runState: RunState;
    lastUpdatedAt: string | null;
    strategyOptions: Array<{ value: string; label: string }>;
    strategiesLoading: boolean;
    selectedStrategyId: string;
    selectedStrategy: StrategyFile | undefined;
    onSelectStrategy: (id: string) => void;
    onRefreshStrategies: () => void;
    onDeploy: () => void;
    onStop: () => void;
    /**
     * 启动资格清单（点启动**之前**就可见）。后端 `shared/start_eligibility.py`
     * 产出，与 `/start` 的 400 信封同源；本组件只展示，不重算判据。
     *
     * 缺省 `undefined` 时退回旧判据 `is_verified` —— 只在没接线的地方发生。
     */
    startEligibility?: StartEligibilityState;
    /** 离开页签后仍运行 —— 由页面可见性推导，用于提示用户「你走开期间它也在跑」 */
    awayWhileRunning?: boolean;
}

/**
 * 顶部状态条（T-RC-17）：模式 + 市场 + 运行状态机 + 账户关键值 + 操作区。
 *
 * **模式文案全部取自 `tradingModeCopy`**——此前三处手写三元里有一处分支写反，
 * 实盘部署的按钮写着「启动模拟交易」，参数向导标题硬编码「模拟执行参数」。
 * 对交易台来说这不是文案瑕疵：用户会据此判断自己下的单进了哪个账户。
 */
const CommandBar: React.FC<CommandBarProps> = ({
    mode,
    market,
    status,
    loading,
    runState,
    lastUpdatedAt,
    strategyOptions,
    strategiesLoading,
    selectedStrategyId,
    selectedStrategy,
    onSelectStrategy,
    onRefreshStrategies,
    onDeploy,
    onStop,
    startEligibility,
    awayWhileRunning,
}) => {
    const copy = modeCopy(mode);
    const runMeta = RUN_STATE_META[runState];
    const isRunning = runState === 'running' || runState === 'starting' || runState === 'config_pending';
    const portfolio = status?.portfolio;
    const marketLabel = MARKET_LABELS[market] || market;
    const mismatch = describeMarketMismatch(status?.strategy_market, status?.market);
    // 信号源可用性作为「行情/链路通不通」的外显证据
    const signalOk = !!status?.signal_source_status?.available;

    // 启动资格：有清单就按清单（与执行点同源），没有就退回旧的「已验证」判据。
    const gateItems = startEligibility?.items ?? [];
    const gateAllowed = startEligibility ? startEligibility.allowed : !!selectedStrategy?.is_verified;
    // 未通过项的可见理由：优先用清单里第一条未通过的，其次用「未参加校验」的说明。
    const blockingItem = gateItems.find((it) => !it.passed);
    const deployReason = !selectedStrategyId
        ? '请先选择策略'
        : gateAllowed
          ? ''
          : (blockingItem?.reason || blockingItem?.next_step || startEligibility?.nextStep || startEligibility?.uncheckedReason || '当前策略不满足启动条件') +
            (blockingItem?.next_step && blockingItem.next_step !== blockingItem.reason
                ? `；${blockingItem.next_step}`
                : '');
    const isDeployDisabled = !selectedStrategyId || !gateAllowed;
    // 参与校验且清单明确不允许时才红着脸列出来；未校验（模板/读取失败）不渲染这条。
    const showGateBar = !!startEligibility && gateItems.length > 0;

    return (
        <div
            data-testid="command-bar"
            data-run-state={runState}
            className="bg-white rounded-2xl shadow-xs border border-slate-200/80 overflow-hidden"
        >
            <div className="p-4 px-5 flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4">
                <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2.5 mb-1.5">
                        <span className={`px-2 py-0.5 rounded-full text-[11px] font-black border ${copy.badgeClass}`}>
                            {copy.full}
                        </span>
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-black border bg-slate-50 text-slate-700 border-slate-200">
                            {marketLabel}
                        </span>
                        <div className={`w-2 h-2 rounded-full ${runMeta.dot}`} />
                        <h2 className="text-base font-bold text-slate-800">{copy.bannerTitle}</h2>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-black border ${runMeta.banner}`}>
                            {runMeta.label}
                        </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 text-slate-500 text-[11px] font-bold">
                        <span className="flex items-center gap-1.5">
                            <Activity size={12} className={copy.isReal ? 'text-rose-500' : 'text-sky-500'} />
                            {copy.isReal
                                ? (MARKET_BROKER_LABEL[market] || '券商通道')
                                : copy.bannerSubtitle}
                        </span>
                        <span className="text-slate-200">|</span>
                        <span className="flex items-center gap-1.5" title={signalOk ? '信号源可用' : status?.signal_source_status?.message}>
                            {signalOk
                                ? <Wifi size={12} className="text-emerald-500" />
                                : <WifiOff size={12} className="text-slate-400" />}
                            {signalOk ? '信号就绪' : '信号未就绪'}
                        </span>
                        {typeof portfolio?.position_count === 'number' && (
                            <>
                                <span className="text-slate-200">|</span>
                                <span>持仓 {portfolio.position_count} 只</span>
                            </>
                        )}
                        {lastUpdatedAt && (
                            <>
                                <span className="text-slate-200">|</span>
                                <span className="text-slate-400">
                                    更新于 {fmtBeijingClock(lastUpdatedAt, { withSeconds: false })}
                                </span>
                            </>
                        )}
                    </div>

                    {mismatch && (
                        <div className="mt-1.5 text-[11px] font-bold text-amber-700">
                            {mismatch}
                        </div>
                    )}
                    {!loading && runState === 'config_pending' && runMeta.hint && (
                        <div className="mt-1.5 text-[11px] font-bold text-amber-700">{runMeta.hint}</div>
                    )}

                    {showGateBar && (
                        <div
                            data-testid="start-eligibility"
                            className={`mt-2 rounded-xl border px-3 py-2 ${
                                gateAllowed
                                    ? 'bg-emerald-50/70 border-emerald-200'
                                    : 'bg-rose-50/70 border-rose-200'
                            }`}
                        >
                            <div className="flex items-center gap-1.5 mb-1">
                                <span
                                    className={`text-[10px] font-black px-1.5 py-0.5 rounded ${
                                        gateAllowed
                                            ? 'bg-emerald-100 text-emerald-700'
                                            : 'bg-rose-100 text-rose-700'
                                    }`}
                                >
                                    启动资格 · {copy.isReal ? '实盘' : '模拟盘'}
                                </span>
                                {!gateAllowed && (
                                    <span className="text-[10px] font-bold text-rose-600">
                                        下列未通过项修好后即可启动
                                    </span>
                                )}
                            </div>
                            <div className="flex flex-col gap-1">
                                {gateItems.map((item) => (
                                    <div key={item.key} className="flex items-start gap-1.5 text-[11px] leading-5">
                                        <span className="mt-0.5 shrink-0">
                                            {item.passed
                                                ? <Check size={12} className="text-emerald-600" />
                                                : <X size={12} className="text-rose-600" />}
                                        </span>
                                        <span className="font-bold text-slate-700 shrink-0">{item.label}</span>
                                        <span className={item.passed ? 'text-slate-500' : 'text-rose-700 font-bold'}>
                                            {item.value}
                                        </span>
                                        {!item.passed && item.reason && (
                                            <span className="text-rose-600">— {item.reason}</span>
                                        )}
                                        {!item.passed && item.next_step && (
                                            <span className="text-slate-500">（下一步：{item.next_step}）</span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    {!showGateBar && startEligibility?.uncheckedReason && selectedStrategyId && (
                        <div className="mt-1.5 text-[11px] font-bold text-slate-400">
                            启动资格未校验：{startEligibility.uncheckedReason}
                        </div>
                    )}
                </div>

                <div className="flex items-center gap-2.5 shrink-0">
                    {!isRunning ? (
                        <>
                            <div className="w-56" data-testid="strategy-select">
                                <Select
                                    value={selectedStrategyId || undefined}
                                    onChange={(value) => onSelectStrategy(String(value))}
                                    onOpenChange={(open) => {
                                        if (open) onRefreshStrategies();
                                    }}
                                    options={strategyOptions}
                                    placeholder={`选择已验证策略…（${marketLabel}）`}
                                    className="w-full custom-antd-select-v2"
                                    size="middle"
                                    showSearch
                                    loading={strategiesLoading}
                                    notFoundContent={strategiesLoading ? '加载中…' : `暂无 ${marketLabel} 策略`}
                                />
                            </div>
                            <button
                                onClick={onRefreshStrategies}
                                className="p-2 text-slate-400 hover:text-blue-600 border border-slate-200 rounded-xl"
                                title="刷新策略列表"
                            >
                                <RefreshCw size={16} className={strategiesLoading ? 'animate-spin' : ''} />
                            </button>
                            <button
                                onClick={onDeploy}
                                disabled={isDeployDisabled}
                                title={deployReason || undefined}
                                data-deploy-blocked-reason={isDeployDisabled ? deployReason : undefined}
                                className={`px-5 py-2 rounded-xl text-xs font-bold text-white transition-all ${
                                    isDeployDisabled
                                        ? 'bg-slate-300 cursor-not-allowed'
                                        : (copy.isReal ? 'bg-rose-500 hover:bg-rose-600' : 'bg-blue-600 hover:bg-blue-700')
                                }`}
                            >
                                <Play size={15} className="inline mr-1.5" />
                                {!selectedStrategyId
                                    ? '未选择策略'
                                    : gateAllowed
                                      ? copy.startButton
                                      : '启动资格未通过'}
                            </button>
                        </>
                    ) : (
                        <button
                            data-testid="stop-strategy"
                            onClick={onStop}
                            className="px-6 py-2.5 bg-rose-500 hover:bg-rose-600 text-white rounded-xl font-bold shadow-lg shadow-rose-100 flex items-center gap-2 text-xs"
                        >
                            <Square size={15} fill="currentColor" /> {copy.stopButton}
                        </button>
                    )}
                </div>
            </div>

            {awayWhileRunning && isRunning && (
                <div className="bg-amber-50 border-t border-amber-200 px-5 py-1.5 text-[11px] font-bold text-amber-800">
                    你刚才离开过本页，策略在这期间<span className="font-black">持续运行</span>（见上方托管心跳）——离开界面不会停止策略。
                </div>
            )}
        </div>
    );
};

export default CommandBar;
