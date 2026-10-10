/**
 * 挖掘选项状态机（算法 / 市场 / 池 / 数据源 / 挖掘配置）——
 * 文字挖掘（ChatInput）与文档挖掘（DocMiningPanel 确认步）共用的唯一一份。
 *
 * 背景：文档挖掘此前「点开始挖掘就按默认值裸跑」。把 ChatInput 里这套选择
 * 原样抽出来做成 hook，两条链的选项、能力面联动与提交散件从此同一处定义——
 * 各写一份必然漂移（一处改了另一处忘就是新的 400 来源）。
 *
 * 纪律（与挖掘配置/算法迁移的既有口径一致）：
 * - 能力面来自 `GET /alpha-agent/algorithms`；目录拉不到 = 未知 → 什么都不禁用
 *   （不拿猜测拦用户，后端闸门仍在），选择器只渲染「默认」一项；
 * - 换算法：市场不支持 → 换到该算法声明的首个市场；频率不支持 → 清空挖掘配置
 *   （窗口/数据集随频率数据面走）；数据集不消费 → 收回 datasets —— 三条闸服务端
 *   都是硬拒，当场收回比让用户对着报错删控件好；
 * - 挖掘配置仅 A 股可用：切到别的市场整块收回（带着 A 股窗口挖港美股必 400）；
 * - `requireLlm`（文档挖掘）：只保留消费方向文本的算法——文档挖掘的方向文本
 *   就是文档整理产物、必下发，非 LLM 算法收到即 400，不能让它在选择器里当陷阱。
 */
import { useEffect, useMemo, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type {
  MiningConfigSelection,
  TaskConfig,
  UniverseId,
  UniverseInfo,
} from '../types-v2';
import { alphaAgentService } from '../services/alphaAgentService';
import { getUniverses } from '../services-v2/api';
import {
  EMPTY_MINING_SELECTION,
  hasCustomMiningConfig,
} from '../services-v2/miningConfigApi';
import {
  algorithmSupportsFreq,
  algorithmSupportsMarket,
  getMiningAlgorithms,
  resolveAlgorithm,
  selectableAlgorithms,
  type MiningAlgorithm,
  type MiningAlgorithmCatalog,
} from '../services-v2/miningAlgorithmApi';

/** 市场目录拉不到时的兜底：与 /markets 的内置市场同表，宁可少显示就绪态也不空屏 */
export interface MarketChoice {
  id: string;
  name: string;
  ready: boolean;
}

const FALLBACK_MARKETS: MarketChoice[] = [
  { id: 'a_share', name: 'A股', ready: true },
  { id: 'crypto', name: '加密货币', ready: true },
  { id: 'hong_kong', name: '港股', ready: false },
  { id: 'us_stock', name: '美股', ready: false },
  { id: 'futures', name: '期货', ready: false },
];

export interface UseMiningOptionsOptions {
  /**
   * 只保留消费方向文本（uses_llm）的算法——文档挖掘专用：方向文本来自文档
   * 整理产物、必下发，非 LLM 算法收到即 400。
   */
  requireLlm?: boolean;
}

export interface MiningOptionsController {
  /** 芯片行数据：市场目录（拉不到 = 兜底表） */
  markets: MarketChoice[];
  universes: UniverseInfo[];
  /** 可选算法（已按 enabled 与 requireLlm 过滤；能力面未知 → 空表） */
  algorithmOptions: MiningAlgorithm[];
  defaultAlgorithm: MiningAlgorithm | null;
  effectiveAlgorithm: MiningAlgorithm | null;
  algorithmLabel: string;
  /** 生效算法是否消费方向文本；能力面未知按 true（不拦） */
  carriesDirection: boolean;
  /** requireLlm 下「默认（xx）」项是否可渲染：注册表默认不消费方向文本 = 选中即 400 */
  defaultOptionAllowed: boolean;
  algorithm: string;
  selectAlgorithm: (key: string) => void;
  miningMarket: string;
  setMiningMarket: (market: string) => void;
  /** 当前所选市场数据未就绪（提交口据此禁用/改文案） */
  selectedNotReady: boolean;
  universe: UniverseId;
  setUniverse: (universe: UniverseId) => void;
  dataSource: string;
  setDataSource: (source: string) => void;
  miningConfig: MiningConfigSelection;
  setMiningConfig: (next: MiningConfigSelection) => void;
  miningConfigPanelOpen: boolean;
  setMiningConfigPanelOpen: Dispatch<SetStateAction<boolean>>;
  /** 提交用散件：仅 A 股 + 有自定义才带 miningConfig 键（未自定义零参数下发） */
  miningConfigPayload: Partial<TaskConfig>;
}

export function useMiningOptions(
  opts: UseMiningOptionsOptions = {},
): MiningOptionsController {
  const requireLlm = opts.requireLlm === true;

  const [miningMarket, setMiningMarket] = useState<string>('a_share');
  const [universe, setUniverse] = useState<UniverseId>('csi300');
  const [universes, setUniverses] = useState<UniverseInfo[]>([]);
  const [dataSource, setDataSource] = useState<string>('qlib_bin');
  const [markets, setMarkets] = useState<MarketChoice[]>([]);
  const [algorithmCatalog, setAlgorithmCatalog] =
    useState<MiningAlgorithmCatalog | null>(null);
  const [algorithm, setAlgorithm] = useState<string>('');
  // 挖掘配置（移植 P1，仅 A 股）：区间 / 检验 / 数据集多选。
  // 全空 = 未自定义（提交零参数，与迁移前逐位一致）。
  const [miningConfigPanelOpen, setMiningConfigPanelOpen] = useState(false);
  const [miningConfig, setMiningConfig] = useState<MiningConfigSelection>(
    EMPTY_MINING_SELECTION,
  );

  useEffect(() => {
    alphaAgentService
      .listMarkets()
      .then((rows) =>
        setMarkets(
          rows.map((m) => ({ id: m.market_id, name: m.market_name, ready: m.data_ready })),
        ),
      )
      .catch(() => {});
    getUniverses()
      .then((res) => setUniverses(res.data?.universes ?? []))
      .catch(() => {});
    // 算法目录：拉不到就保持 null（能力面未知 → 不禁用任何控件、选择器
    // 只渲染「默认」一项），绝不拿半份目录去猜能力面
    getMiningAlgorithms()
      .then((res) => {
        if (res.success && res.data) setAlgorithmCatalog(res.data);
      })
      .catch(() => {});
  }, []);

  // 挖掘配置仅 A 股可用（其他市场数据面不走 QuantDB）：切到别的市场即清空，
  // 否则任务里会带着 A 股窗口去挖港美股——后端会 400，不如当场收回配置
  useEffect(() => {
    if (miningMarket !== 'a_share') {
      setMiningConfigPanelOpen(false);
      setMiningConfig(EMPTY_MINING_SELECTION);
    }
  }, [miningMarket]);

  const allSelectable = useMemo(
    () => selectableAlgorithms(algorithmCatalog),
    [algorithmCatalog],
  );
  const algorithmOptions = useMemo(
    () => (requireLlm ? allSelectable.filter((a) => a.usesLlm) : allSelectable),
    [allSelectable, requireLlm],
  );
  // '' = 注册表默认；目录未加载 / key 已下线 → null = 能力面未知
  const defaultAlgorithm = resolveAlgorithm(algorithmCatalog, '');
  const effectiveAlgorithm = resolveAlgorithm(algorithmCatalog, algorithm);
  const algorithmLabel = effectiveAlgorithm?.name ?? '';
  // 能力面未知（目录拉不到）一律按「都能用」处理：不拿猜测拦用户；
  // 后端闸门仍在（400/412 原文上屏），前端只是不做无谓的提前禁用。
  const carriesDirection = effectiveAlgorithm ? effectiveAlgorithm.usesLlm : true;
  const defaultOptionAllowed = !requireLlm || defaultAlgorithm?.usesLlm !== false;

  // requireLlm 下注册表默认不消费方向文本 → 自动落到首个可选 LLM 算法。
  // 注册表现任默认（rdagent）消费方向文本，这是未来换默认时的防线——不修的话
  // 「默认」项被藏起而 state 还是 ''，选择器会显示成第一个选项的假选中。
  useEffect(() => {
    if (!requireLlm || algorithm) return;
    if (defaultAlgorithm?.usesLlm !== false) return;
    const first = algorithmOptions[0];
    if (first) setAlgorithm(first.key);
  }, [requireLlm, algorithm, defaultAlgorithm, algorithmOptions]);

  /**
   * 换算法：目标算法吃不下当前配置 → 当场收回。留着不动的组合提交必被后端
   * 400（市场/频率/数据集三条闸都在服务端），在这里收比让用户对着报错删控件好；
   * 未选过的配置不受影响。
   */
  const selectAlgorithm = (key: string) => {
    if (key === algorithm) return;
    setAlgorithm(key);
    const next = resolveAlgorithm(algorithmCatalog, key);
    if (!next) return;
    if (!algorithmSupportsMarket(next, miningMarket)) {
      setMiningMarket(next.markets[0] || 'a_share');
    }
    if (!algorithmSupportsFreq(next, miningConfig.freq)) {
      // 与面板换挡同口径：频率换了数据面就换了，旧窗口/数据集一律清零
      setMiningConfig(EMPTY_MINING_SELECTION);
    } else if (!next.usesDatasets && miningConfig.datasets) {
      setMiningConfig((prev) => ({ ...prev, datasets: null }));
    }
  };

  const marketList = markets.length > 0 ? markets : FALLBACK_MARKETS;
  const selectedNotReady =
    marketList.find((m) => m.id === miningMarket)?.ready === false;

  const miningConfigPayload = useMemo<Partial<TaskConfig>>(
    () =>
      miningMarket === 'a_share' && hasCustomMiningConfig(miningConfig)
        ? { miningConfig }
        : {},
    [miningMarket, miningConfig],
  );

  return {
    markets: marketList,
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
    selectedNotReady,
    universe,
    setUniverse,
    dataSource,
    setDataSource,
    miningConfig,
    setMiningConfig,
    miningConfigPanelOpen,
    setMiningConfigPanelOpen,
    miningConfigPayload,
  };
}
