/**
 * 因子池「变体台账」tab（T-FE-09）：统计派变体的残差证据与冷却状态。
 *
 * 数据纪律：
 * - 全体候选骨架恒列（注册序），无观测行的指标一律 `—`（MISSING_METRIC_TEXT），
 *   绝不显 0；台账文件缺席 = 显式空态文案（不是错误、不是空白表）；
 * - 「不可验证永不判失败」：insufficient_days / unavailable / 父本缺席 只如实
 *   上屏状态，不挂冷却徽章——界面把「状态」与「冷却」两列分开呈现；
 * - 冷却按 (变体, 股票池) 键控：页头选「全部股票池」时无池口径，
 *   「冷却中」显 `—` 而不是臆造全池并集；
 * - 非 A 股市场如实标注链路口径（统计派变体为 A 股产物）。
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/Card';
import { Button } from './ui/Button';
import { MISSING_METRIC_TEXT } from '../services-v2/metricRegistry';
import {
  getVariantLedger,
  type VariantLedgerRow,
  type VariantLedgerStat,
  type VariantLedgerView,
} from '../services-v2/api';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';

const STAT_STATUS_LABEL: Record<string, string> = {
  ok: '可验证',
  insufficient_days: '有效日不足',
  unavailable: '不可用',
  no_baseline_in_run: '父本缺席',
};

const STAT_STATUS_STYLE: Record<string, string> = {
  ok: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30',
  insufficient_days: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
};

const STAT_STATUS_TITLE: Record<string, string> = {
  ok: '残差可验证（样本足）——冷却判定只认这一档',
  insufficient_days: '有效日不足：不给结论，等更长窗口',
  unavailable: '残差不可用（数据/基线缺失）',
  no_baseline_in_run: '父本本轮被冷却/缺席：残差无从计算',
};

function fmtNum(value: number | null | undefined, digits = 3): string {
  return value == null || !Number.isFinite(value) ? MISSING_METRIC_TEXT : value.toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value)
    ? MISSING_METRIC_TEXT
    : String(Math.round(value));
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return MISSING_METRIC_TEXT;
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? String(value) : dt.toLocaleString('zh-CN');
}

function fmtDay(value: string | null | undefined): string {
  return value ? String(value).slice(0, 10) : MISSING_METRIC_TEXT;
}

const StatusChip: React.FC<{ status: string }> = ({ status }) => (
  <span
    className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
      STAT_STATUS_STYLE[status] ?? 'bg-slate-400/15 text-slate-500 border-slate-400/30'
    }`}
    title={STAT_STATUS_TITLE[status] ?? status}
  >
    {STAT_STATUS_LABEL[status] ?? status}
  </span>
);

const KpiTile: React.FC<{ label: string; value: string; hint?: string; testId: string }> = ({
  label,
  value,
  hint,
  testId,
}) => (
  <Card className="glass card-hover">
    <CardContent className="p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-bold font-mono text-foreground" data-testid={testId}>
        {value}
      </div>
      {hint && <div className="mt-1 text-[11px] text-muted-foreground">{hint}</div>}
    </CardContent>
  </Card>
);

interface VariantLedgerTabProps {
  market: string;
  universe: string;
}

export const VariantLedgerTab: React.FC<VariantLedgerTabProps> = ({ market, universe }) => {
  const [view, setView] = useState<VariantLedgerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [observedOnly, setObservedOnly] = useState(false);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getVariantLedger({ universe: universe || undefined });
      if (res.success && res.data) {
        setView(res.data);
        setError(null);
      } else {
        setError(res.error ?? '变体台账读取失败');
      }
    } finally {
      setLoading(false);
    }
  }, [universe]);

  useEffect(() => {
    load();
  }, [load]);

  // 作用域切换后旧展开行无意义（观测随池重裁）→ 收起
  useEffect(() => {
    setExpandedKey(null);
  }, [market, universe]);

  const rows = view?.variants ?? [];
  const nameByKey = useMemo(() => new Map(rows.map((r) => [r.key, r.name])), [rows]);
  const observedCount = useMemo(() => rows.filter((r) => r.observationsCount > 0).length, [rows]);
  const coolableCount = useMemo(() => rows.filter((r) => r.coolable).length, [rows]);
  const visibleRows = useMemo(
    () => (observedOnly ? rows.filter((r) => r.observationsCount > 0) : rows),
    [rows, observedOnly],
  );

  const constants = view?.constants;
  const activeCount = view?.coolingActive?.length ?? null;
  const floor = constants?.coolIcirFloor ?? null;

  if (error) {
    return (
      <Card className="glass">
        <CardContent className="p-6 text-sm text-destructive">{error}</CardContent>
      </Card>
    );
  }

  if (!view) {
    return (
      <Card className="glass">
        <CardContent className="p-6 text-sm text-muted-foreground">
          {loading ? '正在读取变体台账…' : '变体台账加载中…'}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {market !== 'a_share' && (
        <Card className="glass">
          <CardContent className="p-3 text-xs text-muted-foreground">
            变体台账是 <span className="font-medium text-foreground">A 股统计派链路</span>
            的产物——其他市场按同一口径读取（通常为空骨架）。
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiTile
          label="已观测变体"
          value={`${observedCount} / ${rows.length}`}
          hint="有观测的变体 / 全体候选（骨架恒列）"
          testId="ledger-kpi-observed"
        />
        <KpiTile
          label="冷却中"
          value={activeCount == null ? MISSING_METRIC_TEXT : String(activeCount)}
          hint={
            activeCount == null
              ? '选具体股票池后显示（跨池证据不可比）'
              : `当前池生效（${universe}）· 下一轮挖掘不再评估`
          }
          testId="ledger-kpi-active"
        />
        <KpiTile
          label="可判失败变体"
          value={String(coolableCount)}
          hint="根与父本保护变体不判不冷"
          testId="ledger-kpi-coolable"
        />
        <KpiTile
          label="台账更新"
          value={fmtTime(view.updatedAt)}
          hint={view.available ? undefined : '尚无台账文件（未跑过统计派挖掘）'}
          testId="ledger-kpi-updated"
        />
      </div>

      <Card className="glass">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">判定口径（非对称否决：宁可漏判，不误杀）</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground space-y-1">
          <p>
            残差 ICIR &lt; {fmtNum(constants?.coolIcirFloor, 2)}（对最佳周期）且有效日 ≥{' '}
            {fmtInt(constants?.highConfDays)} → 判「无增量」入冷{' '}
            {fmtInt(constants?.cooldownDays)} 天；残差 ICIR ≥ 线且样本足 → 解除冷却。
          </p>
          <p>有效日不足 / 父本缺席 / 不可用 → 永不判失败（等新证据重评）。</p>
          <p>
            冷却生效 = 下一轮统计派挖掘中该变体不再评估（其子变体本轮不判残差）；判定按
            (变体, 股票池) 键控，观测保尾 {fmtInt(constants?.maxObservations)} 条。
          </p>
        </CardContent>
      </Card>

      <Card className="glass">
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-sm mr-auto">
              变体骨架（{visibleRows.length}
              {visibleRows.length !== rows.length ? ` / ${rows.length}` : ''}）
            </CardTitle>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={observedOnly}
                onChange={(e) => setObservedOnly(e.target.checked)}
              />
              仅显示有观测的行
            </label>
            <Button variant="ghost" size="sm" disabled={loading} onClick={load}>
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          {!view.available && (
            <div className="text-xs text-muted-foreground">
              台账尚无记录——跑过一轮统计派挖掘后，变体观测与冷却判定会在这里入账。
            </div>
          )}
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-xs text-muted-foreground">
                  <th className="py-2 px-2 text-left font-medium">变体</th>
                  <th className="py-2 px-2 text-center font-medium">编辑</th>
                  <th className="py-2 px-2 text-left font-medium">父本</th>
                  <th className="py-2 px-2 text-center font-medium">残差 ICIR</th>
                  <th className="py-2 px-2 text-center font-medium">有效日</th>
                  <th className="py-2 px-2 text-center font-medium">ρ(父本)</th>
                  <th className="py-2 px-2 text-center font-medium">观测窗</th>
                  <th className="py-2 px-2 text-center font-medium">状态</th>
                  <th className="py-2 px-2 text-center font-medium">冷却至</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => (
                  <LedgerRow
                    key={row.key}
                    row={row}
                    nameByKey={nameByKey}
                    floor={floor}
                    expanded={expandedKey === row.key}
                    onToggle={() =>
                      setExpandedKey((prev) => (prev === row.key ? null : row.key))
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
          {visibleRows.length === 0 && (
            <div className="p-8 text-center text-sm text-muted-foreground">
              {observedOnly ? '当前没有有观测的变体。' : '变体骨架为空（注册表不可用）。'}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

interface LedgerRowProps {
  row: VariantLedgerRow;
  nameByKey: Map<string, string>;
  floor: number | null;
  expanded: boolean;
  onToggle: () => void;
}

const LedgerRow: React.FC<LedgerRowProps> = ({ row, nameByKey, floor, expanded, onToggle }) => {
  const stat = row.latestStat;
  const belowFloor =
    floor != null && stat?.status === 'ok' && stat.icIr != null && stat.icIr < floor;
  const baselineName = row.baselineKey
    ? (nameByKey.get(row.baselineKey) ?? row.baselineKey)
    : null;

  return (
    <>
      <tr
        data-testid={`ledger-row-${row.key}`}
        className="border-b border-border/40 last:border-0 hover:bg-muted/40 transition-colors align-top"
      >
        <td className="py-2 px-2 max-w-[240px]">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              data-testid={`ledger-toggle-${row.key}`}
              aria-expanded={expanded}
              aria-label={`展开 ${row.name}`}
              onClick={onToggle}
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
            <span className="truncate font-medium" title={row.formulation || row.name}>
              {row.name}
            </span>
            {!row.coolable && (
              <span
                className="shrink-0 rounded border border-border/60 px-1 py-0 text-[9px] text-muted-foreground"
                title="根 / 父本保护：不参与冷却判定（防掐断控制链）"
              >
                不判
              </span>
            )}
          </div>
          <div className="truncate font-mono text-[10px] text-muted-foreground pl-5">
            {row.key}
          </div>
        </td>
        <td className="py-2 px-2 text-center">
          {row.editType ? (
            <span className="inline-flex items-center rounded-md border border-violet-500/30 bg-violet-500/10 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
              {row.editType}
            </span>
          ) : (
            <span className="text-muted-foreground">{MISSING_METRIC_TEXT}</span>
          )}
        </td>
        <td className="py-2 px-2 max-w-[160px]">
          <span className="truncate text-xs" title={row.baselineKey ?? undefined}>
            {baselineName ?? MISSING_METRIC_TEXT}
          </span>
        </td>
        <td
          className={`py-2 px-2 text-center font-mono ${
            belowFloor ? 'font-bold text-red-600' : ''
          }`}
          title={belowFloor ? '低于判失败线（等有效日达标即入冷）' : undefined}
        >
          {fmtNum(stat?.icIr, 3)}
        </td>
        <td className="py-2 px-2 text-center font-mono">{fmtInt(stat?.nDays)}</td>
        <td className="py-2 px-2 text-center font-mono">{fmtNum(stat?.maxBasisAbsCorr, 3)}</td>
        <td className="py-2 px-2 text-center font-mono text-xs">
          {fmtDay(row.latest?.windowEnd)}
        </td>
        <td className="py-2 px-2 text-center">
          {row.coolingActive === true ? (
            <span
              className="inline-flex items-center rounded-md border border-red-500/30 bg-red-500/15 px-1.5 py-0.5 text-[10px] font-bold text-red-600"
              title="残差无增量且样本充足：下一轮挖掘不再评估，到期凭新证据重评"
            >
              冷却中
            </span>
          ) : stat ? (
            <StatusChip status={stat.status} />
          ) : (
            <span className="text-muted-foreground">{MISSING_METRIC_TEXT}</span>
          )}
        </td>
        <td className="py-2 px-2 text-center font-mono text-xs">
          {row.coolingActive === true ? fmtTime(row.cooling[0]?.until) : MISSING_METRIC_TEXT}
        </td>
      </tr>
      {expanded && (
        <tr data-testid={`ledger-detail-${row.key}`} className="border-b border-border/40">
          <td colSpan={9} className="bg-secondary/20 px-4 py-3">
            <div className="space-y-3 text-xs">
              <div>
                <span className="text-muted-foreground">公式：</span>
                <span className="font-mono break-all">{row.formulation || MISSING_METRIC_TEXT}</span>
              </div>
              {row.latest ? (
                <>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
                    <span>
                      最新观测：<span className="font-mono text-foreground">{row.latest.universe}</span>
                    </span>
                    <span className="font-mono text-foreground">
                      {row.latest.windowStart} ~ {row.latest.windowEnd}
                    </span>
                    <span>入账 {fmtTime(row.latest.at)}</span>
                    <span>共 {row.observationsCount} 条观测</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="text-xs">
                      <thead>
                        <tr className="text-muted-foreground">
                          <th className="py-1 pr-4 text-left font-medium">周期</th>
                          <th className="py-1 pr-4 text-center font-medium">状态</th>
                          <th className="py-1 pr-4 text-center font-medium">残差 ICIR</th>
                          <th className="py-1 pr-4 text-center font-medium">IC 均值</th>
                          <th className="py-1 pr-4 text-center font-medium">有效日</th>
                          <th className="py-1 text-center font-medium">ρ(父本)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {row.latest.horizons.map((h: VariantLedgerStat, hi: number) => (
                          <tr key={`${h.horizon ?? 'na'}:${hi}`}>
                            <td className="py-1 pr-4 font-mono">
                              {h.horizon == null ? MISSING_METRIC_TEXT : `${h.horizon} 日`}
                            </td>
                            <td className="py-1 pr-4 text-center">
                              <StatusChip status={h.status} />
                            </td>
                            <td className="py-1 pr-4 text-center font-mono">{fmtNum(h.icIr, 3)}</td>
                            <td className="py-1 pr-4 text-center font-mono">{fmtNum(h.icMean, 4)}</td>
                            <td className="py-1 pr-4 text-center font-mono">{fmtInt(h.nDays)}</td>
                            <td className="py-1 text-center font-mono">
                              {fmtNum(h.maxBasisAbsCorr, 3)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <div className="text-muted-foreground">该变体还没有观测。</div>
              )}
              <div>
                <div className="font-medium text-foreground mb-1">
                  冷却记录（{row.cooling.length}）
                </div>
                {row.cooling.length === 0 ? (
                  <div className="text-muted-foreground">无冷却记录。</div>
                ) : (
                  row.cooling.map((c) => (
                    <div key={`${c.variantKey}:${c.universe}`} className="text-muted-foreground">
                      <span className="font-mono text-foreground">{c.universe}</span>
                      {' · '}
                      {fmtTime(c.since)} → {fmtTime(c.until)}
                      {c.evidence && (
                        <span className="ml-2">
                          判据：残差 ICIR {fmtNum(c.evidence.residualIcIr, 3)} · 有效日{' '}
                          {fmtInt(c.evidence.nDays)} · 周期 {fmtInt(c.evidence.horizon)} · 窗至{' '}
                          {c.evidence.windowEnd ?? MISSING_METRIC_TEXT}
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

export default VariantLedgerTab;
