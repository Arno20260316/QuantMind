/**
 * 挖掘选项条（算法 / 市场 / 池 / 数据源 / [方向] / 区间·频率·数据集 / [尾部槽]）——
 * 文字挖掘与文档挖掘共用的单行芯片行。
 *
 * 纪律：
 * - **单行**：`flex-nowrap + overflow-x-auto`——窄窗宁可横向滚动也不换行
 *   （多行会把输入区挤走，用户点名要一行）；所有芯片 `shrink-0` 防止被压缩变形；
 * - 控件一律由能力面驱动（`useMiningOptions` 提供）——本组件不写死算法名/市场名；
 * - 方向开关（`direction`）与尾部槽（`trailing`，如父本按钮）可选：文档挖掘的
 *   方向即文档草稿、没有开关，父本属于拆解链，两者都只在文字挖掘侧传。
 */
import React from 'react';
import { CalendarRange, Compass } from 'lucide-react';
import type { UniverseId } from '../types-v2';
import { algorithmSupportsMarket } from '../services-v2/miningAlgorithmApi';
import { hasCustomMiningConfig } from '../services-v2/miningConfigApi';
import type { MiningOptionsController } from './useMiningOptions';

/** 市场按钮 title 用的中文名（目录拉不到时 m.name 已是中文，这里只补 id → 名） */
const MARKET_LABELS: Record<string, string> = {
  a_share: 'A股',
  crypto: '加密货币',
  hong_kong: '港股',
  us_stock: '美股',
  futures: '期货',
};

export interface MiningOptionsBarProps {
  /** useMiningOptions 的返回值（两条链各持一份实例） */
  options: MiningOptionsController;
  disabled?: boolean;
  /** 方向开关（文字挖掘专属：文档挖掘的方向文本就是文档草稿，没有开关） */
  direction?: { active: boolean; onToggle: () => void };
  /** 尾部槽：接在分隔链末尾（如父本按钮），保留调用方自己的样式 */
  trailing?: React.ReactNode;
}

export const MiningOptionsBar: React.FC<MiningOptionsBarProps> = ({
  options,
  disabled = false,
  direction,
  trailing,
}) => {
  const {
    markets,
    universes,
    algorithmOptions,
    defaultAlgorithm,
    effectiveAlgorithm,
    algorithmLabel,
    carriesDirection,
    defaultOptionAllowed,
    algorithm,
    selectAlgorithm,
    miningMarket,
    setMiningMarket,
    universe,
    setUniverse,
    dataSource,
    setDataSource,
    miningConfig,
    miningConfigPanelOpen,
    setMiningConfigPanelOpen,
  } = options;

  return (
    <div className="flex items-center gap-1.5 flex-nowrap overflow-x-auto">
      {/* 挖掘算法（移植 P2）：能力面（市场 / 频率 / 方向 / 数据集）随选择联动；
          目录来自后端注册表，前端不写死算法名 */}
      {algorithmOptions.length > 0 && (
        <div className="shrink-0 flex items-center gap-1 mr-1 pr-1 border-r border-slate-200">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 select-none">算法</span>
          <select
            value={algorithm}
            onChange={(e) => selectAlgorithm(e.target.value)}
            disabled={disabled}
            aria-label="挖掘算法"
            className="rounded-full bg-slate-50 px-2.5 py-[3px] text-[11px] font-medium text-slate-600 border-0 focus:outline-none focus:ring-1 focus:ring-blue-200 disabled:opacity-40 appearance-none cursor-pointer"
            title={effectiveAlgorithm?.desc || '挖掘算法（后端注册表下发）；不同算法的市场 / 频率 / 数据集能力面不同'}
          >
            {/* requireLlm 且注册表默认不消费方向文本时，这一项是选中即 400 的陷阱，整项不渲染 */}
            {defaultOptionAllowed && (
              <option value="">默认（{defaultAlgorithm?.name ?? '注册表默认'}）</option>
            )}
            {algorithmOptions.map((a) => (
              <option key={a.key} value={a.key}>{a.name}</option>
            ))}
          </select>
        </div>
      )}

      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-widest text-slate-400 mr-0.5 select-none">市场</span>
      {markets.map((m) => {
        // 能力面不支持的市场：禁用（不是隐藏——隐藏会让人以为市场丢了）
        const unsupported = !algorithmSupportsMarket(effectiveAlgorithm, m.id);
        const selectable = m.ready !== false && !unsupported;
        return (
          <button
            key={m.id}
            onClick={() => setMiningMarket(m.id)}
            disabled={disabled || !selectable}
            className={`shrink-0 rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 flex items-center gap-1 ${
              miningMarket === m.id
                ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-200 shadow-xs'
                : selectable
                  ? 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
                  : 'text-slate-300 cursor-not-allowed'
            }`}
            title={
              unsupported
                ? `${algorithmLabel || '当前算法'}不支持${MARKET_LABELS[m.id] ?? m.name}市场`
                : !m.ready
                  ? '数据未就绪，请先在管理后台同步数据'
                  : `${MARKET_LABELS[m.id] ?? m.name}因子挖掘`
            }
          >
            <span className={`inline-block w-1.5 h-1.5 rounded-full ring-1 ${m.ready ? 'bg-emerald-400 ring-emerald-300' : 'bg-slate-300 ring-slate-200'}`} />
            {m.name}
          </button>
        );
      })}

      {/* Universe selector */}
      {miningMarket === 'a_share' && (
        <div className="shrink-0 flex items-center gap-1 ml-2 pl-2 border-l border-slate-200">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 select-none">池</span>
          <select
            value={universe}
            onChange={(e) => setUniverse(e.target.value as UniverseId)}
            disabled={disabled}
            className="rounded-full bg-slate-50 px-2.5 py-[3px] text-[11px] font-medium text-slate-600 border-0 focus:outline-none focus:ring-1 focus:ring-blue-200 disabled:opacity-40 appearance-none cursor-pointer"
            title="选择因子挖掘的股票池"
          >
            {universes.length > 0
              ? universes.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.isSystem === false ? '★ ' : ''}{u.name}{u.stockCount > 0 ? ` (${u.stockCount})` : ''}
                  </option>
                ))
              : (
                <option value="csi300">沪深300</option>
              )}
          </select>
        </div>
      )}

      {/* Data source */}
      <div className="shrink-0 flex items-center gap-1 ml-1 pl-1 border-l border-slate-200">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 select-none">数据</span>
        {[
          { id: 'qlib_bin', name: 'Qlib' },
          { id: 'parquet', name: 'Parquet' },
        ].map((ds) => (
          <button
            key={ds.id}
            onClick={() => setDataSource(ds.id)}
            disabled={disabled}
            className={`shrink-0 rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 ${
              dataSource === ds.id
                ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-200 shadow-xs'
                : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
            }`}
          >
            {ds.name}
          </button>
        ))}
      </div>

      {/* Direction toggle：只对消费方向文本的算法（uses_llm）有意义；
          文档挖掘不传 direction（它的方向就是文档草稿） */}
      {carriesDirection && direction && (
        <button
          type="button"
          onClick={direction.onToggle}
          title={direction.active ? '使用设置中的挖掘方向（已开）' : '使用设置中的挖掘方向（点击开启）'}
          className={`shrink-0 ml-1 pl-1 border-l border-slate-200 flex items-center gap-1 rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 ${
            direction.active
              ? 'bg-purple-50 text-purple-600 ring-1 ring-purple-200 shadow-xs'
              : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
          }`}
        >
          <Compass className="h-3 w-3" />
          <span>方向</span>
        </button>
      )}

      {/* 挖掘配置（仅 A 股）：自定义挖掘/检验区间 + 数据集多选 */}
      {miningMarket === 'a_share' && (
        <button
          type="button"
          onClick={() => setMiningConfigPanelOpen((v) => !v)}
          disabled={disabled}
          title={
            hasCustomMiningConfig(miningConfig)
              ? '已自定义挖掘频率/区间/数据集（点开调整）'
              : '挖掘配置：挖掘频率（日线 / 分钟 1min）、自定义挖掘区间、样本外检验区间与挖掘数据集'
          }
          className={`shrink-0 ml-1 pl-1 border-l border-slate-200 flex items-center gap-1 rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 disabled:opacity-40 ${
            miningConfigPanelOpen || hasCustomMiningConfig(miningConfig)
              ? 'bg-teal-50 text-teal-700 ring-1 ring-teal-200 shadow-xs'
              : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
          }`}
        >
          <CalendarRange className="h-3 w-3" />
          <span>区间/频率/数据集</span>
          {miningConfig.freq === '1min' ? (
            <span className="rounded-full bg-violet-600 text-white px-1.5 text-[10px] font-bold leading-4">
              1min
            </span>
          ) : (
            hasCustomMiningConfig(miningConfig) && (
              <span className="rounded-full bg-teal-600 text-white px-1.5 text-[10px] font-bold leading-4">
                自定义
              </span>
            )
          )}
        </button>
      )}

      {trailing}
    </div>
  );
};
