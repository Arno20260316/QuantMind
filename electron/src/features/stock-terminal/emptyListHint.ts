/**
 * 候选信号列表「空态」的如实解释（纯展示逻辑，可单测）。
 *
 * 列表为空**不是一种情况**，但历史实现一律渲染成「无匹配股票」——用户看不懂，
 * 只能反复重搜或以为界面坏了。这里的唯一职责是：拿后端的事实字段把空因分成
 * 三类，各给一个**下一步**。文案只描述事实，不臆测（不写「可能…」「大概…」）。
 *
 * 事实来源（`/stock-terminal/list` 响应，见 StockListResponse）：
 * - `signal_rows`：本请求**过滤前**的分数行数（选中模型时已按该模型的 run 收窄）。
 *   0 = 没分数；>0 = 有分数但可能被条件滤掉。
 * - `total`：过滤**后**的只数。0 且 signal_rows>0 = 是筛选条件滤空的。
 * - `signal_date`：基准信号日，写进文案让用户知道看的是哪一天。
 *
 * 老后端不带 `signal_rows`（undefined）→ 只说一句诚实的「当前条件下没有股票」，
 * 不假装知道是哪种空因。
 */

import { signalPositionLabel } from '../shared/signalVocabulary';

export interface EmptyListHintInput {
  /** 是否处于检索模式（搜索框有词） */
  searching: boolean;
  query: string;
  /** 本请求过滤前的分数行数（undefined = 老后端无此字段） */
  signalRows?: number;
  /** 当前选中的推理模型 id（空 = 全部） */
  model?: string;
  signalDate?: string | null;
  /** 过滤后的只数 */
  filteredTotal: number;
  /** 已生效的**主动缩小**条件标签（不含 side / 只看自选） */
  narrowingLabels: string[];
  /** 候选列表的「信号」位置闸（BUY/SELL/HOLD）；未生效传 undefined */
  side?: string;
  /** 该位置闸是否真的在生效（检索模式 / 只看自选下让开 → false） */
  sideApplied: boolean;
  onlyWatchlist: boolean;
}

export interface EmptyListHint {
  title: string;
  /** 下一步说明；无话可说时为空串（不硬凑） */
  detail: string;
  /** 面包屑标签（哪些条件在生效） */
  labels: string[];
  /** 是否展示「清除这些条件」按钮（有主动缩小条件且清掉有意义时才给） */
  canClear: boolean;
}

export function emptyListHint(input: EmptyListHintInput): EmptyListHint {
  const {
    searching, query, signalRows, model, signalDate,
    filteredTotal, narrowingLabels, side, sideApplied, onlyWatchlist,
  } = input;

  const labels = [
    ...narrowingLabels,
    ...(sideApplied && side ? [`信号 ${signalPositionLabel(side)}`] : []),
    ...(onlyWatchlist ? ['只看自选'] : []),
  ];

  if (searching) {
    return {
      title: `全市场没有匹配「${query.trim()}」的股票代码或名称`,
      detail: '',
      labels,
      canClear: false,
    };
  }

  const day = signalDate ? `${signalDate} ` : '';
  const isEmptyFromFilters =
    signalRows != null && signalRows > 0 && filteredTotal === 0;

  if (signalRows === 0 && model) {
    return {
      title: '这款模型当日没有打分',
      detail: '所选模型在这一天没有产出推理分（不是筛选条件的问题）。换一款模型试试——若多款都空，请到「系统健康」查看推理任务是否失败。',
      labels,
      canClear: false,
    };
  }
  if (signalRows === 0) {
    return {
      title: `${day}当日没有推理信号`,
      detail: '上游批量推理本日没有产出打分，放宽筛选条件也不会有结果——换个基准日，或到「系统健康」查看推理任务是否失败。',
      labels,
      canClear: false,
    };
  }
  if (isEmptyFromFilters) {
    const aside = sideApplied
      ? '候选列表默认只看「靠前」的一侧，改方向筛选就能看到另一侧。'
      : '';
    return {
      title: '有打分，但被当前筛选条件滤掉了',
      detail: `${day}基准信号日共有 ${signalRows} 只有打分——放宽或清除条件即可看到。${aside}`,
      labels,
      canClear: narrowingLabels.length > 0,
    };
  }

  return { title: '当前条件下没有股票', detail: '', labels, canClear: false };
}
