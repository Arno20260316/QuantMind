/** 港股聪明钱 · 口径文案与展示映射（唯一映射处；细案 §6 用词纪律）。
 *
 * 纪律：
 * - 展示面只说**事实语义与位置**：席位行为=增持/减持，类型资金=净流入/净流出，
 *   命中率一律「历史胜率」（事件后 10 日超额与方向同号占比——事后统计，非预测）；
 *   不出现「推荐/建议/买入信号」，不写「前 N%」话术（统一 0–100 分位）；
 * - 缺失一律「—」，**绝不显示成 0**（0 是真实读数，与缺失两码事）；
 * - 分类色统一引用「机构持仓」栏 INST_CATEGORY_COLORS（不在此复制色值）；
 * - IC 与命中率保持中性色（统计量不是涨跌）；金额/超额正负走红绿
 *   （signCls，港股红涨绿跌）；分数走中性紫阶（scoreCls）。
 */

/** 面板口径横幅 */
export const HK_SM_TITLE = '聪明钱 · 席位行为统计与类型效力';
export const HK_SM_DISCLAIMER = '研究统计（非模型预测、非交易指令）';

/** 缺失展示符（全站统一「—」） */
export const MISSING = '—';

/** 类型展示序（矩阵与概览带共用；与后端 CATEGORIES 键同集） */
export const CATEGORY_ORDER = ['cn_broker', 'southbound', 'hk', 'us_eu', 'apac', 'other'] as const;

/** 类型兜底中文标签（接口已随行返回 category_label，本表仅兜底） */
export const CATEGORY_FALLBACK: Record<string, string> = {
  cn_broker: '内资·券商',
  southbound: '内资·港股通',
  hk: '港资',
  us_eu: '外资·欧美',
  apac: '外资·亚太',
  other: '其他',
};

export function categoryLabel(cat: string, fromApi?: string | null): string {
  return fromApi || CATEGORY_FALLBACK[cat] || cat;
}

/** 席位 kind 兜底标签（与后端 KIND_LABELS 同步） */
export const KIND_FALLBACK: Record<string, string> = {
  broker: '券商',
  custodian: '托管',
  settlement: '结算',
  other: '其他',
};

export function kindLabel(kind: string | null | undefined): string {
  if (!kind) return MISSING;
  return KIND_FALLBACK[kind] ?? kind;
}

/** 席位方向（+1 增持 / −1 减持）→ 展示词 */
export function directionWord(d: number | null | undefined): string {
  if (d === 1) return '增持';
  if (d === -1) return '减持';
  return MISSING;
}

/** 类型净额 → 流向词（正=净流入，负=净流出） */
export function flowWord(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  const n = Number(v);
  if (n > 0) return '净流入';
  if (n < 0) return '净流出';
  return MISSING;
}

/** 席位榜窗口 chips（与后端 window 参数同集） */
export const SEAT_WINDOW_OPTIONS: Array<{ id: string; label: string }> = [
  { id: '20', label: '20日' },
  { id: '60', label: '60日' },
  { id: 'all', label: '全史' },
];

export function windowLabel(id: string): string {
  return SEAT_WINDOW_OPTIONS.find((o) => o.id === id)?.label ?? id;
}

/** 席位榜排序 chips（与后端 sort 参数同集；v2 默认按 90% 下界） */
export const SEAT_SORT_OPTIONS: Array<{ id: string; label: string }> = [
  { id: 'lb_skill', label: '按90%下界' },
  { id: 'skill', label: '按技能' },
  { id: 'vw_skill', label: '按大钱技能' },
  { id: 'n', label: '按事件数' },
];

/** 席位榜样本量门槛 chips（v2 默认 n≥50；「全部」= 读侧契约下限 SEAT_MIN_EVENTS=5） */
export const SEAT_MIN_OPTIONS: Array<{ id: number; label: string; title: string }> = [
  { id: 30, label: 'n≥30', title: '仅看事件数 ≥ 30 的席位' },
  { id: 50, label: 'n≥50', title: '仅看事件数 ≥ 50 的席位（默认：小样本噪声沉底）' },
  { id: 100, label: 'n≥100', title: '仅看事件数 ≥ 100 的席位（最扎实的一档）' },
  { id: 5, label: '全部', title: '不设额外门槛（仍不低于读侧契约 n≥5）' },
];

/** 口径脚注（面板口径卡与榜单行 tooltip 共用同一句话） */
export const HIT_NOTE =
  '历史胜率 = 事件后 10 交易日超额收益与席位方向同号的占比（事后统计，非预测）';
export const SHRINK_NOTE =
  '收缩估计 w=n/(n+20)：小样本向中性（超额 0bp、胜率 50%）收缩；n 为窗口内事件数，随行披露';
export const EPISODE_NOTE =
  'episode 归并：同一席位连续同向变动只计首日（避免一次建仓被拆成多笔计数）';
export const SUSPECT_NOTE =
  '过户嫌疑 = 单日持股占比变动过大（疑似股份过户而非真实买卖），只标记、不计入统计';
export const FWD_NOTE =
  '事后超额 = 事件日 T+1 收盘入场、持有 10 日的市场调整超额（封存展示列，不参与信号构造）';
export const ADMIT_NOTE =
  '入柱 = 通过 h10 双过闸（IC 显著 ∧ 尾部 q5−q1 同号）；「零入柱」为真裁定（数据结论），不是链路故障';

/** v2 口径注（机构优势面与席位抽屉共用；与后端 hk_smart_money.py 文案一致） */
export const SKILL_NOTE =
  '方向技能 = 买入事件 +事后超额 / 卖出事件 −事后超额（方向对不对）；同样是事后统计——持续性审计显示席位层弱持续，榜单是描述性盘点，不是可跟随信号';
export const LB_NOTE =
  '90% 下界 = 技能均值 − 1.28×标准误：小样本自动沉底，榜默认按此排序；样本 < 5 不给下界（「—」比假标 90% 诚实）';
export const VW_NOTE = '大钱技能 = 按 |变动金额| 加权的方向技能：笔数与分量级分离，防小额高频刷榜';
export const PERSIST_NOTE =
  '持续性审计：把全史切成前/后半场，检验「前半强的席位后半是否还强」。实测秩相关约 0.12（弱正）——优势可盘点、难以直接跟随，这是数据结论不是故障';
export const SAMPLE_NOTE = '样本纪律：默认只看事件数 ≥ 50 的席位；「全部」档会混入小样本噪声，请连同 n 阅读';
export const PROFILE_WINDOW_NOTE = '窗口统计口径与席位榜一字不差：episode 首日 ∧ 非过户嫌疑；样本 = fwd_exc_10 可测事件';

/** 缺失安全数值格式化：null/NaN → 「—」，绝不显示 0 */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return Number(v).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 整数（千分位，最多 0 位小数）；缺失「—」 */
export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

/** 分数（0–100）→ 1 位小数；缺失「—」 */
export function fmtScore(v: number | null | undefined): string {
  return fmtNum(v, 1);
}

/** 分数（0–1）→ 百分比带 % 符号；缺失「—」 */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${fmtNum(Number(v) * 100, digits)}%`;
}

/** 带符号数值（正数统一带「+」，0 不带号）；缺失「—」 */
export function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${fmtNum(n, digits)}`;
}

/** bp → 带符号 1 位小数字符串（不加单位后缀）；缺失「—」 */
export function fmtBp(v: number | null | undefined, digits = 1): string {
  return fmtSigned(v, digits);
}

/** 亿元数值（接口已按亿）→ 带符号 2 位；缺失「—」 */
export function fmtYi(v: number | null | undefined, digits = 2): string {
  return fmtSigned(v, digits);
}

/** 股数 → 亿股/万股自适应；缺失「—」 */
export function fmtShares(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${fmtNum(n / 1e8, 2)}亿股`;
  if (abs >= 1e4) return `${fmtNum(n / 1e4, 1)}万股`;
  return `${fmtInt(n)}股`;
}

/** HKD 原始金额 → 万（标定网格等门槛值展示）；缺失「—」 */
export function fmtHkdWan(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${fmtNum(Number(v) / 1e4, 0)}万`;
}

/** 单笔分量级（接口单位=百万 HKD）→ 亿/万自适应；缺失「—」 */
export function fmtValueM(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 100) return `${fmtNum(n / 100, 2)}亿`;
  return `${fmtNum(n * 100, 0)}万`;
}

/** 金额/超额符号着色（港股口径：红涨绿跌，仅用于数值正负）；0 与缺失走中性 */
export function signCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v)) || Number(v) === 0) {
    return 'text-slate-500';
  }
  return Number(v) > 0 ? 'text-red-600' : 'text-green-600';
}

/** IC 着色（中性：统计量不是涨跌，绝不套红绿） */
export function icCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return 'text-slate-400';
  return Math.abs(Number(v)) >= 0.01 ? 'text-slate-800' : 'text-slate-500';
}

/** 分数色阶（中性，紫系）：分不是涨跌，绝不套红绿 */
export function scoreCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return 'text-slate-400';
  if (v >= 70) return 'text-purple-700';
  if (v >= 40) return 'text-slate-700';
  return 'text-slate-500';
}
