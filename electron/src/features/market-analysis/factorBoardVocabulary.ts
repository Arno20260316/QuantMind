/** 因子概率板 · 判定枚举 → 展示词（唯一映射处）。
 *
 * 与 `features/shared/signalVocabulary.ts` 同一条纪律：展示面只说**位置**
 * （靠前/靠后/居中），不写买入/卖出。判定枚举是机读契约（resonant_long 等），
 * 后端 `backend/shared/factor_board/sectors.py` 产出，真值表金样在
 * `backend/tests/fixtures/factorBoardGolden.json`（前后端测试共读防漂移）。
 *
 * 未知枚举**不硬套**：显示「未知判定」并提示原始枚举，让上游新增判定显形。
 */

export interface VerdictMeta {
  label: string;
  cls: string;
  hint: string;
}

export const VERDICT_META: Record<string, VerdictMeta> = {
  resonant_long: {
    label: '共振 · 靠前',
    cls: 'bg-red-50 text-red-700 border-red-200/80',
    hint: '板块合成分居截面高位（≥q67），且个股广度≥0.5、模型广度≥0.55（三层同向靠前）',
  },
  resonant_short: {
    label: '共振 · 靠后',
    cls: 'bg-green-50 text-green-700 border-green-200/80',
    hint: '板块合成分居截面低位（≤q33），且个股广度≤0.5、模型广度≤0.45（三层同向靠后）',
  },
  diverge_model_weak: {
    label: '背离 · 模型未跟随',
    cls: 'bg-amber-50 text-amber-700 border-amber-200/80',
    hint: '板块合成分靠前但模型广度<0.45：因子层靠前、模型层靠后',
  },
  diverge_model_resilient: {
    label: '背离 · 模型更靠前',
    cls: 'bg-sky-50 text-sky-700 border-sky-200/80',
    hint: '板块合成分靠后但模型广度>0.55：因子层靠后、模型层靠前',
  },
  neutral: {
    label: '无判定',
    cls: 'bg-slate-50 text-slate-500 border-slate-200/80',
    hint: '未同时满足三层条件（任一层缺失时不判定）',
  },
};

/** 未知枚举的展示（不猜语义，把上游新增的判定原样显形）。 */
export function verdictMeta(verdict: string): VerdictMeta {
  return (
    VERDICT_META[verdict] ?? {
      label: '未知判定',
      cls: 'bg-slate-50 text-slate-400 border-slate-200/80',
      hint: `未识别的判定枚举: ${verdict}`,
    }
  );
}

/** 衰减四态（selection.py `decay` 字段；金样 `decayEnum` 双端共读防漂移）。
 *
 * 谓词（尾窗 20 个有效日，对齐量 = sgn×IC）：均值 <0 → flipped；
 * |均值| <0.02 → weak；否则 ok；有效日 <15 → insufficient。
 */
export const DECAY_META: Record<string, VerdictMeta> = {
  ok: {
    label: '同向',
    cls: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
    hint: '尾窗 20 个有效日方向对齐 IC（sgn×RankIC）均值 ≥0.02：近 20 日方向与强度维持',
  },
  weak: {
    label: '走弱',
    cls: 'bg-amber-50 text-amber-700 border-amber-200/80',
    hint: '尾窗方向对齐 IC 均值 |x|<0.02：近 20 日方向仍在、强度衰减（只显形，不影响入选闸门）',
  },
  flipped: {
    label: '近期反向',
    cls: 'bg-red-50 text-red-700 border-red-200/80',
    hint: '尾窗方向对齐 IC 均值为负：近 20 日与 IC60 主方向相反（负值原样显形，不截断）',
  },
  insufficient: {
    label: '样本不足',
    cls: 'bg-slate-50 text-slate-500 border-slate-200/80',
    hint: '尾窗有效日 <15 天：不硬判状态',
  },
};

/** 衰减 chip 的未知/缺省出口（同 verdictMeta 纪律）。 */
export function decayMeta(state: string | null | undefined): VerdictMeta {
  if (!state) {
    return {
      label: '—',
      cls: 'bg-slate-50 text-slate-400 border-slate-200/80',
      hint: '无衰减统计（1.0 产物或尾窗缺失）',
    };
  }
  return (
    DECAY_META[state] ?? {
      label: '未知',
      cls: 'bg-slate-50 text-slate-400 border-slate-200/80',
      hint: `未识别的衰减状态: ${state}`,
    }
  );
}

/** regime 注记四态（regime.py `regime_note` 字段；金样 `regimeNoteEnum` 双端共读）。
 *
 * 弱桶谓词（唯一实现 regime_buckets.is_weak_bucket）：制度桶内对齐 IC 均值 ≤0
 * 且有效日 ≥15 → 权重 ×0.5（只降不翻）；其余恒 ×1.0。
 */
export const REGIME_NOTE_META: Record<string, VerdictMeta> = {
  ok: {
    label: '桶内正常',
    cls: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
    hint: '该因子在制度桶内对齐 IC 均值为正且样本充分：权重不调整',
  },
  weak: {
    label: '弱桶降权',
    cls: 'bg-amber-50 text-amber-700 border-amber-200/80',
    hint: '制度桶内对齐 IC 均值 ≤0 且 ≥15 日：权重 ×0.5（只降不翻，绝不改方向）',
  },
  insufficient: {
    label: '桶内样本不足',
    cls: 'bg-slate-50 text-slate-500 border-slate-200/80',
    hint: '当前制度桶内有效日 <15 天：样本不足，权重不调整',
  },
  unavailable: {
    label: '制度不可用',
    cls: 'bg-slate-50 text-slate-500 border-slate-200/80',
    hint: '制度状态表/IC 配对不可用：整块降级，权重全不动',
  },
};

/** regime 注记 chip 的未知/缺省出口。 */
export function regimeNoteMeta(state: string | null | undefined): VerdictMeta {
  if (!state) {
    return {
      label: '—',
      cls: 'bg-slate-50 text-slate-400 border-slate-200/80',
      hint: '无制度注记（1.0 产物或 regime 降级）',
    };
  }
  return (
    REGIME_NOTE_META[state] ?? {
      label: '未知',
      cls: 'bg-slate-50 text-slate-400 border-slate-200/80',
      hint: `未识别的制度注记: ${state}`,
    }
  );
}

/** 个股象限 chip 着色（唯一实现：因子概率板名单行与资金流个股卡共用）。
 *
 * 与四象限矩阵同口径：HH 红 / LL 绿 / 背离琥珀（因子轴在极端而模型轴未同向）
 * / 其余中性。输入为两字母组合（fq+mq ∈ {H,M,L}²），null/未知 → 中性。
 */
export function quadChipCls(q: string | null | undefined): string {
  if (q === 'HH') return 'bg-red-50 text-red-600 border-red-200/70';
  if (q === 'LL') return 'bg-green-50 text-green-700 border-green-200/70';
  if (q && ((q[0] === 'H' && q[1] !== 'H') || (q[0] === 'L' && q[1] !== 'L'))) {
    return 'bg-amber-50 text-amber-700 border-amber-200/70';
  }
  return 'bg-slate-50 text-slate-500 border-slate-200/60';
}

const QUAD_AXIS_WORD: Record<string, string> = { H: '高', M: '中', L: '低' };

/** 个股象限 chip 的文案与释义（两字母 = 因子分位档 + 模型分位档）。
 *
 * 释义只用位置词（高/中/低为截面分位档，靠前/靠后描述方向），不写买卖。
 * 缺失一律「—」并说明原因，绝不按 0 处理。
 */
export function quadrantMeta(q: string | null | undefined): VerdictMeta {
  const cls = quadChipCls(q);
  if (!q || q.length !== 2 || !(q[0] in QUAD_AXIS_WORD) || !(q[1] in QUAD_AXIS_WORD)) {
    return {
      label: '—',
      cls,
      hint: '无象限数据（因子分位或模型分位缺失，绝不按 0 处理）',
    };
  }
  const fw = QUAD_AXIS_WORD[q[0]];
  const mw = QUAD_AXIS_WORD[q[1]];
  let meaning: string;
  if (q === 'HH') meaning = '两者同向靠前（因子分位与模型分位都在截面高位）';
  else if (q === 'LL') meaning = '两者同向靠后（因子分位与模型分位都在截面低位）';
  else if (q[0] === 'H') meaning = '背离：因子层靠前、模型层未同向';
  else if (q[0] === 'L') meaning = '背离：因子层靠后、模型层未同向';
  else meaning = '因子层居中，无共振/背离判定';
  return { label: q, cls, hint: `因子分位${fw} × 模型分位${mw}；${meaning}` };
}
