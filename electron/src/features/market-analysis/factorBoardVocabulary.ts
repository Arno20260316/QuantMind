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
