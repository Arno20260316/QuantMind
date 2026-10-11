"""统计派挖掘引擎（移植 P2/P3，机构级 Tier 1+2+3）——全流水线编排。

    取数(含暖机) → 可交易性过滤 → 逐候选计算 → 可选中性化 → 多周期 IC
    → BH-FDR(跨 候选×周期) → 子窗稳定性（折间符号一致性）→ 样本外一致性
    → 选择分排序 → 选择期值级去重（遭拒递补）→ IC 加权合成 → 全量审计

**选择规则**（顺序固定；每个候选都带拒因落审计，不许静默落榜）：

1. 样本内可评估：best horizon 的有效 IC 日 ≥ ``min_valid_days``，否则拒
   （``样本内有效日不足``）；
2. 多重检验：对全部 候选×周期 的 t 值做一次 BH-FDR（q）——在 N 个候选里挑
   Top-K 本身就是选择偏差，逐周期修正；任一周期过线即算过（报告记明哪几个）。
   FDR 未过者**照样复算样本外**：拒因已定，但「弱但方向一致」与「弱且反转」
   对复核是两条信息，报告要给全；
3. 子窗稳定性（Tier 3，``require_stability``）：逐折 RankIC 与全窗同号的折
   占比 < ``min_fold_sign_consistency`` → 拒（``折间符号不稳``）。折结构不足
   （任一折有效日 < ``min_fold_days``）**不拒**：如实记 note，不拿假精确说话。
   滚动再选择命中率是**审计量**（跨候选抢 K 席的相对量，不当闸——2026-10-11
   csi300 实跑：19/28 候选命中 0/3 而折间全同号，旧闸刀锋误伤，见 stability.py）；
4. 样本外一致性（配了验证区间才启用）：同号且 ``|OOS RankIC| ≥ oos_min_ratio
   × |IS RankIC|``，否则拒（``样本外符号反转`` / ``样本外衰减过大``）；
5. 通过者按**选择分**（``|RankIC| × 折间符号一致性``；无稳定性证据时权重取
   中性 1）降序排序——幅度与稳定同权，防「整窗 |RankIC| 大、一半折反向」的
   候选纯靠幅度霸榜；过闸但排在 K 外的记 ``未入选 Top-K``；
6. **选择期相关性去重**（Tier 3，``require_dedup``）：沿选择分序贪心，与已
   保留者 |ρ| ≥ ``dedup_corr_reject`` 者跳过（``与 X 高度相关``）并**递补**
   ——Top-K 是席位，信息量相同的因子只留代表，凑满即停；不足 K 就是不足 K；
7. **IC 加权合成**（Tier 3，``composite_enabled``）：去重后入选 ≥ 2 个时合成
   ``Σ ŵ·z(f)``（口径见 ``composite.py``），走同一套 IC/样本外/分组证据；
   不进 BH-FDR（不属于原始候选池）；合成因子的样本外闸不过 → 只记账不落库。

**取数范围**：面板取 ``[min(挖掘起, 验证起) 前推MAX_MIN_HISTORY交易日, max(挖掘止,
验证止)]``——验证区间的数据必须真取到，否则样本外闸门会因「无数据」整批误杀。
两个区间重叠会在报告里标 ``validation_overlaps_mining``（重叠 = 样本外已被样本内
污染，数字不可信）。

**审计**：过滤逐级计数、中性化报告、每候选每周期的 IC/RankIC/ICIR/t/p/FDR、
样本外复算、分组单调性，随 :class:`MiningResult` 一起返回——复核数字先看它。

**内存纪律**：候选逐条计算（面板几百万行，同时持 28 条因子会几个 GB）；前向收益
按周期预计算一次；分组收益只对入选者算。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import candidates as _candidates
from . import composite as _composite
from . import dedup as _dedup
from . import ic_stats as _ic
from . import neutralize as _nz
from . import panel as _panel
from . import stability as _stab
from . import tradability as _td

#: 样本外最低 RankIC 保留比例（OOS/IS），低于即判「衰减过大」
DEFAULT_OOS_MIN_RATIO = 0.3


@dataclass(frozen=True)
class StatisticalConfig:
    horizons: tuple[int, ...] = (1, 5, 10, 20)
    top_k: int = 5
    min_names_per_day: int = _ic.DEFAULT_MIN_NAMES_PER_DAY
    min_valid_days: int = _ic.DEFAULT_MIN_VALID_DAYS
    quantiles: int = _ic.DEFAULT_QUANTILES
    fdr_q: float = _ic.DEFAULT_FDR_Q
    require_fdr: bool = True
    oos_min_ratio: float = DEFAULT_OOS_MIN_RATIO
    oos_min_days: int = _ic.DEFAULT_MIN_VALID_DAYS
    neutralize: str = "industry"  # none | industry | industry_size
    tradability: _td.TradabilityConfig = field(default_factory=_td.TradabilityConfig)
    # ── Tier 3（体系化）：子窗稳定性 / 相关性去重 / IC 加权合成 ──────────
    stability_folds: int = _stab.DEFAULT_FOLDS
    min_fold_sign_consistency: float = _stab.DEFAULT_MIN_FOLD_SIGN_CONSISTENCY
    min_fold_days: int = _stab.DEFAULT_MIN_FOLD_DAYS
    require_stability: bool = True
    dedup_corr_reject: float = _dedup.DEFAULT_CORR_REJECT
    dedup_corr_warn: float = _dedup.DEFAULT_CORR_WARN
    require_dedup: bool = True
    composite_enabled: bool = True
    composite_z_window: int = _composite.DEFAULT_Z_WINDOW

    def __post_init__(self) -> None:
        if not self.horizons or any(int(h) < 1 for h in self.horizons):
            raise ValueError(f"horizons 必须是正整数序列（收到 {self.horizons!r}）")
        if self.top_k < 1:
            raise ValueError(f"top_k 至少为 1（收到 {self.top_k!r}）")
        if self.quantiles < 2:
            raise ValueError(f"quantiles 至少为 2（收到 {self.quantiles!r}）")
        if self.neutralize not in {"none", "industry", "industry_size"}:
            raise ValueError(f"neutralize 口径非法: {self.neutralize!r}")
        if self.stability_folds < 2:
            raise ValueError(f"stability_folds 至少为 2（收到 {self.stability_folds!r}）")
        if not 0 < self.min_fold_sign_consistency <= 1:
            raise ValueError(
                "min_fold_sign_consistency 必须在 (0, 1]"
                f"（收到 {self.min_fold_sign_consistency!r}）"
            )
        if self.min_fold_days < 1:
            raise ValueError(f"min_fold_days 至少为 1（收到 {self.min_fold_days!r}）")
        if not 0.0 <= self.dedup_corr_warn < self.dedup_corr_reject <= 1.0:
            raise ValueError(
                "相关性阈值必须满足 0 ≤ warn < reject ≤ 1"
                f"（收到 warn={self.dedup_corr_warn!r} reject={self.dedup_corr_reject!r}）"
            )
        if self.composite_z_window < 2:
            raise ValueError(f"composite_z_window 至少为 2（收到 {self.composite_z_window!r}）")


@dataclass(frozen=True)
class HorizonStats:
    horizon: int
    n_days: int
    ic_mean: float
    rank_ic_mean: float
    icir: float
    rank_ic_t: float  # Newey–West 修正 t（规范；喂 p/FDR/落库）
    rank_ic_p: float
    rank_ic_win_rate: float
    fdr_pass: bool = False
    #: 朴素 t 与 NW 带宽（审计对照；缺失期 = NaN/0）
    rank_ic_t_naive: float = float("nan")
    rank_ic_t_lags: int = 0


@dataclass(frozen=True)
class CandidateReport:
    """单候选全量证据（未入选者也有：拒因 + 已算出的统计量）。"""

    key: str
    name: str
    family: str
    formulation: str
    source: str
    expression: str
    min_history: int
    horizons: tuple[HorizonStats, ...]
    best_horizon: int | None
    rank_ic_mean: float
    icir: float
    t_stat: float
    p_value: float
    fdr_pass: bool
    fdr_horizons: tuple[int, ...]
    oos_verified: bool
    oos_horizon: int | None
    oos_rank_ic: float
    oos_n_days: int
    quantile_monotonicity: float
    spread_mean: float
    selected: bool
    reject_reason: str = ""
    # ── Tier 3 证据（不可判定一律 NaN/空，语义见 stability/dedup 模块）──
    fold_ics: tuple[float, ...] = ()  # 逐折 RankIC 均值（stability_folds 个）
    fold_sign_consistency: float = float("nan")  # 与全窗同号的折占比（闸取它）
    selection_score: float = float("nan")  # |RankIC| × 折间符号一致性（选择排序键）
    reselect_rate: float = float("nan")  # 展开窗检查点 Top-K 命中率（审计量）
    reselect_hits: int = 0
    reselect_checkpoints: int = 0
    ic_half_life: float = float("nan")  # |RankIC| 对数线性衰减半衰期（bar）
    ic_decay_slope: float = float("nan")  # 衰减系数 λ（≤0 = 不衰减）
    max_abs_corr: float = float("nan")  # 与其他保留入选者的最大 |ρ|
    corr_warn: str = ""  # ρ ∈ [warn, reject) 的警语（保留但提示）


@dataclass(frozen=True)
class CompositeReport:
    """IC 加权合成因子的全量证据（Tier 3 / T-S3-04）。

    ``components/weights/rank_ics`` 逐位对齐；``expression`` 是落库的 qlib
    单行表达式（与 :meth:`composite.CompositePlan.candidate` 的 pandas 实现
    同式，等价性由测试对拍）。合成因子**不参与 BH-FDR**（不属于原始候选池，
    分量已各自过检）——``fdr`` 相关字段刻意缺席，metadata 里注明。
    """

    key: str
    name: str
    horizon: int  # 权重来源周期 h*（分量在该周期的 RankIC 归一）
    components: tuple[str, ...]
    weights: tuple[float, ...]
    component_rank_ics: tuple[float, ...]
    z_window: int
    expression: str
    min_history: int
    horizons: tuple[HorizonStats, ...]
    best_horizon: int | None
    rank_ic_mean: float
    icir: float
    t_stat: float
    p_value: float
    oos_verified: bool
    oos_horizon: int | None
    oos_rank_ic: float
    oos_n_days: int
    quantile_monotonicity: float
    spread_mean: float
    eligible: bool  # 样本内可评估 ∧ 样本外闸（若配）
    reject_reason: str = ""


@dataclass(frozen=True)
class MiningResult:
    candidates: tuple[CandidateReport, ...]
    selected: tuple[str, ...]
    filter_report: _td.FilterReport
    neutralize_report: _nz.NeutralizeReport | None
    neutralize_mode: str  # 实际生效口径（降级后的，非请求的）
    horizons: tuple[int, ...]
    window: dict
    panel_rows: int
    n_symbols: int
    #: 实际取数用的标的池（qlib market 名）；任务请求的 universe 就是它——
    #: 落库/审计据此说话，不拿任务参数冒充（请求 csi300 就必须真跑 csi300）
    universe: str = "all_a"
    #: Tier 3：入选集相关性去重记录（未启用/入选 < 2 → None）
    dedup: _dedup.DedupReport | None = None
    #: Tier 3：IC 加权合成因子报告（未启用/分量 < 2/退化 → None）
    composite: CompositeReport | None = None


# ── 内部工具 ─────────────────────────────────────────────────────────


def _best_horizon(stats: tuple[HorizonStats, ...] | list[HorizonStats]) -> HorizonStats | None:
    valid = [s for s in stats if np.isfinite(s.rank_ic_mean)]
    if not valid:
        return None
    return max(valid, key=lambda s: abs(s.rank_ic_mean))


def _horizon_stats(horizon: int, summary: _ic.IcSummary | None) -> HorizonStats:
    if summary is None:
        return HorizonStats(
            horizon=horizon, n_days=0, ic_mean=np.nan, rank_ic_mean=np.nan,
            icir=np.nan, rank_ic_t=np.nan, rank_ic_p=np.nan, rank_ic_win_rate=np.nan,
        )
    return HorizonStats(
        horizon=horizon,
        n_days=summary.n_days,
        ic_mean=summary.ic_mean,
        rank_ic_mean=summary.rank_ic_mean,
        icir=summary.icir,
        rank_ic_t=summary.rank_ic_t,
        rank_ic_p=summary.rank_ic_p,
        rank_ic_win_rate=summary.rank_ic_win_rate,
        rank_ic_t_naive=summary.rank_ic_t_naive,
        rank_ic_t_lags=summary.rank_ic_t_lags,
    )


def _window_slice(index: pd.MultiIndex, start, end) -> np.ndarray:
    """日期区间布尔掩码（None = 该端不设限）。"""
    dates = index.get_level_values(0)
    mask = np.ones(len(index), dtype=bool)
    if start is not None:
        mask &= np.asarray(dates >= pd.Timestamp(start))
    if end is not None:
        mask &= np.asarray(dates <= pd.Timestamp(end))
    return mask


def _apply_fdr(horizon_stats: dict[str, list[HorizonStats]], q: float) -> None:
    """跨全部 候选×周期 做一次 BH-FDR，回填每周期 ``fdr_pass``。"""
    p_values = [s.rank_ic_p for stats in horizon_stats.values() for s in stats]
    flags = _ic.bh_fdr(p_values, q=q)
    cursor = 0
    for key in list(horizon_stats):
        replaced = []
        for s in horizon_stats[key]:
            replaced.append(
                HorizonStats(**{**s.__dict__, "fdr_pass": bool(flags[cursor])})
            )
            cursor += 1
        horizon_stats[key] = replaced


def _compute_factor(
    candidate: _candidates.Candidate,
    panel: pd.DataFrame,
    trad_mask: pd.Series,
    position: pd.Series,
    neutralize: str,
    industry: pd.Series | None,
    size: pd.Series | None,
) -> tuple[pd.Series, _nz.NeutralizeReport | None]:
    """单候选 → 过滤 + 暖机裁剪 + 中性化（供 IC / 分组共用，中性化只算一次）。"""
    usable = trad_mask & (position >= candidate.min_history)
    factor = _candidates.compute_panel(panel, candidate).where(usable)
    if neutralize == "industry" and industry is not None:
        return _nz.industry_demean(factor, industry)
    if neutralize == "industry_size" and industry is not None and size is not None:
        return _nz.industry_size_residual(factor, industry, size=size)
    return factor, None


def _oos_verdict(
    best: HorizonStats | None, oos: HorizonStats | None, cfg: StatisticalConfig
) -> tuple[bool, str]:
    """样本外闸门 → (是否通过, 拒因)。未配验证区间 = 未复核（跳过闸门）。"""
    if best is None:
        return False, ""  # 样本内已判，不重复记因
    if oos is None:
        return True, ""  # 未配验证区间：允许入选，报告如实标 oos_verified=False
    if oos.n_days == 0:
        return False, "样本外有效日不足"
    if np.sign(oos.rank_ic_mean) != np.sign(best.rank_ic_mean):
        return False, "样本外符号反转"
    if abs(oos.rank_ic_mean) < cfg.oos_min_ratio * abs(best.rank_ic_mean):
        return False, "样本外衰减过大"
    return True, ""


# ── 主流程 ───────────────────────────────────────────────────────────


def run_statistical_mining(
    mining_start: str,
    mining_end: str,
    *,
    valid_start: str | None = None,
    valid_end: str | None = None,
    cfg: StatisticalConfig | None = None,
    price_panel: pd.DataFrame | None = None,
    metadata: pd.DataFrame | None = None,
    loader: Callable | None = None,
    calendar: pd.DatetimeIndex | None = None,
    qlib_dir: str = _panel.DEFAULT_QLIB_DIR,
    universe: str = "all_a",
    progress: Callable[[str, str], None] | None = None,
) -> MiningResult:
    """跑一轮统计派挖掘（``price_panel``/``metadata`` 可注入：离线测试全链路）。

    ``universe``：标的池（qlib market 名，如 ``csi300``/``all_a``）——任务选了
    什么就真在什么池子上算，IC 与落库行同一口径；解析为空即报错，绝不静默
    换池（换池的 IC 数字不是用户要的东西）。

    ``price_panel`` 注入时须已含暖机段（调用方负责），且覆盖到两个区间的并集。
    """
    cfg = cfg or StatisticalConfig()
    emit = progress or (lambda _stage, _detail: None)
    has_oos = valid_start is not None or valid_end is not None
    anchor = min([pd.Timestamp(t) for t in (mining_start, valid_start) if t is not None])
    fetch_end = max([pd.Timestamp(t) for t in (mining_end, valid_end) if t is not None])

    universe = str(universe or "").strip() or "all_a"
    emit("data", f"读取行情面板（{universe}，含暖机，覆盖挖掘+验证区间）")
    if price_panel is None:
        # 暖机 = 候选最大回看 + 合成因子的 z 窗（Tier 3）：合成因子的
        # min_history 比分量多一个 z 窗，不多取这段暖机，挖掘窗头部就会被
        # 静默裁掉 z 窗长度的样本（IC 少算一截，还没人报错）。
        warmup = _candidates.MAX_MIN_HISTORY + (
            cfg.composite_z_window if cfg.composite_enabled else 0
        )
        price_panel = _panel.load_price_panel(
            anchor.strftime("%Y-%m-%d"),
            fetch_end.strftime("%Y-%m-%d"),
            market=universe,
            warmup_bars=warmup,
            loader=loader,
            calendar=calendar,
            qlib_dir=qlib_dir,
        )
    if metadata is None:
        metadata = _panel.load_symbol_metadata()
    panel = price_panel
    if metadata is not None and not metadata.empty and "is_st" in metadata.columns:
        panel = panel.assign(is_st=_panel.map_to_panel(panel, metadata["is_st"]))

    emit("filter", "A 股可交易性过滤")
    listing = (
        metadata["listing_date"].dropna()
        if metadata is not None and "listing_date" in metadata.columns
        else None
    )
    trad_mask, filter_report = _td.build_mask(
        panel, cfg=cfg.tradability, listing_dates=listing
    )
    position = panel.groupby(level=1).cumcount()
    in_is = _window_slice(panel.index, mining_start, mining_end)
    in_oos = _window_slice(panel.index, valid_start, valid_end)
    if has_oos and (pd.Timestamp(valid_start or mining_start) <= pd.Timestamp(mining_end)
                    and pd.Timestamp(valid_end or mining_end) >= pd.Timestamp(mining_start)):
        emit("filter", "⚠ 验证区间与挖掘区间重叠：样本外复算已被样本内污染")

    industry = size = None
    if metadata is not None and not metadata.empty:
        if "industry" in metadata.columns:
            industry = _panel.map_to_panel(panel, metadata["industry"])
        if "float_shares" in metadata.columns:
            size = panel["close"].astype(float) * _panel.map_to_panel(
                panel, metadata["float_shares"]
            )
    neutralize = cfg.neutralize
    if neutralize == "industry_size" and size is None:
        emit("filter", "流通股本元数据不可用：industry_size 降级为 industry")
        neutralize = "industry" if industry is not None else "none"
    if neutralize != "none" and industry is None:
        emit("filter", "行业元数据不可用：中性化降级为 none")
        neutralize = "none"

    emit("data", f"前向收益（周期 {cfg.horizons}）")
    fwd = {h: _ic.forward_returns(panel, h) for h in cfg.horizons}

    emit("evaluate", f"逐候选计算 + 多周期 IC（{len(_candidates.CANDIDATES)} 个）")
    horizon_stats: dict[str, list[HorizonStats]] = {}
    oos_stats: dict[str, HorizonStats | None] = {}
    #: (候选, 周期) → 挖掘窗逐日 IC 序列——Tier 3 的折/再选择/相关性都从
    #: 这份「已算出的逐日原始证据」出发，不再重算 IC（内存 = 每格三列小数）
    daily_is: dict[tuple[str, int], pd.DataFrame] = {}
    nz_report: _nz.NeutralizeReport | None = None
    for candidate in _candidates.CANDIDATES:
        factor, candidate_nz = _compute_factor(
            candidate, panel, trad_mask, position, neutralize, industry, size
        )
        nz_report = candidate_nz if candidate_nz is not None else nz_report  # 形状报告取最后非空
        stats = []
        for h in cfg.horizons:
            daily = _ic.daily_ic(
                factor[in_is], fwd[h][in_is], min_names=cfg.min_names_per_day
            )
            daily_is[(candidate.key, h)] = daily
            stats.append(_horizon_stats(h, _ic.summarize_ic(daily, min_days=cfg.min_valid_days)))
        horizon_stats[candidate.key] = stats
        best = _best_horizon(stats)
        oos_stats[candidate.key] = None
        if has_oos and best is not None:
            oos_stats[candidate.key] = _horizon_stats(
                best.horizon,
                _ic.summarize_ic(
                    _ic.daily_ic(
                        factor[in_oos],
                        fwd[best.horizon][in_oos],
                        min_names=cfg.min_names_per_day,
                    ),
                    min_days=cfg.oos_min_days,
                ),
            )

    if cfg.require_fdr:
        emit("select", "多重检验修正（BH-FDR，跨 候选×周期）")
        _apply_fdr(horizon_stats, cfg.fdr_q)

    stab: dict[str, _stab.ReselectStats] = {}
    if cfg.require_stability:
        emit(
            "stability",
            f"子窗稳定性（{cfg.stability_folds} 折：折间符号一致性闸 + "
            f"滚动再选择审计，逐检查点重排 Top-{cfg.top_k}）",
        )
        rank_series: dict[str, pd.Series] = {}
        for candidate in _candidates.CANDIDATES:
            best = _best_horizon(horizon_stats[candidate.key])
            if best is not None:
                rank_series[candidate.key] = daily_is[(candidate.key, best.horizon)]["rank_ic"]
        stab = _stab.reselect_statistics(
            rank_series,
            folds=cfg.stability_folds,
            top_k=cfg.top_k,
            min_fold_days=cfg.min_fold_days,
        )
        thin = [key for key, s in stab.items() if not s.eligible and s.fold_ics]
        if thin:
            emit("stability", f"{len(thin)} 个候选折内有效日不足：不判稳定性（如实记 note）")

    emit("select", "样本外一致性 + 选择分排序（Top-K 席位）")
    drafts: list[CandidateReport] = []
    for candidate in _candidates.CANDIDATES:
        stats = horizon_stats[candidate.key]
        best = _best_horizon(stats)
        fdr_horizons = tuple(s.horizon for s in stats if s.fdr_pass)
        oos = oos_stats[candidate.key]
        oos_pass, oos_reason = _oos_verdict(best, oos, cfg)
        half_life, decay = _stab.ic_half_life(
            cfg.horizons, [s.rank_ic_mean for s in stats]
        )
        stability = stab.get(candidate.key)
        reasons = []
        if best is None:
            reasons.append("样本内有效日不足")  # 没进 FDR 检验的项不记 FDR 拒因
        elif cfg.require_fdr and not fdr_horizons:
            reasons.append("多重检验未过（BH-FDR）")
        if oos_reason:
            reasons.append(oos_reason)
        # 子窗稳定性闸：折间符号翻脸才算不稳；不可判定（折薄/无数据/全窗零）
        # 不拒——拒要有证据，没证据的场景如实记 note 进报告，不拿 NaN 冒充结论
        if (
            best is not None
            and stability is not None
            and stability.eligible
            and np.isfinite(stability.sign_consistency)
            and stability.sign_consistency < cfg.min_fold_sign_consistency
        ):
            n_active = sum(1 for ic in stability.fold_ics if np.isfinite(ic))
            matches = int(round(stability.sign_consistency * n_active))
            reasons.append(f"折间符号不稳（{matches}/{n_active} 折与全窗同号）")
        drafts.append(
            CandidateReport(
                key=candidate.key,
                name=candidate.name,
                family=candidate.family,
                formulation=candidate.formulation,
                source=candidate.source,
                expression=candidate.expression,
                min_history=candidate.min_history,
                horizons=tuple(stats),
                best_horizon=best.horizon if best else None,
                rank_ic_mean=best.rank_ic_mean if best else np.nan,
                icir=best.icir if best else np.nan,
                t_stat=best.rank_ic_t if best else np.nan,
                p_value=best.rank_ic_p if best else np.nan,
                fdr_pass=bool(fdr_horizons),
                fdr_horizons=fdr_horizons,
                oos_verified=oos is not None,
                oos_horizon=best.horizon if oos is not None and best else None,
                oos_rank_ic=oos.rank_ic_mean if oos else np.nan,
                oos_n_days=oos.n_days if oos else 0,
                quantile_monotonicity=np.nan,
                spread_mean=np.nan,
                selected=False,
                reject_reason="；".join(reasons),
                fold_ics=stability.fold_ics if stability else (),
                fold_sign_consistency=(
                    stability.sign_consistency if stability else np.nan
                ),
                selection_score=_selection_score(best, stability),
                reselect_rate=stability.reselect_rate if stability else np.nan,
                reselect_hits=stability.reselect_hits if stability else 0,
                reselect_checkpoints=stability.reselect_checkpoints if stability else 0,
                ic_half_life=half_life,
                ic_decay_slope=decay,
            )
        )
    ranked_keys = _rank_eligible(drafts)

    # 入选因子值懒算 + 缓存：选择期去重的贪心步行走哪算哪（K 席之外或递补后
    # 才轮到的候选不白算），算过的留给分组证据复用——面板几百万行，逐消费方
    # 重算是这层最大的重复开销。被**拒**的候选在判决当下即释放（on_drop）：
    # 只压尾巴不压峰值，缓存峰值恒为 O(K+1) 而不是 O(走过的候选数)
    factor_cache: dict[str, pd.Series] = {}

    def _factor_of(key: str) -> pd.Series:
        if key not in factor_cache:
            factor, _ = _compute_factor(
                _candidates.CANDIDATE_BY_KEY[key],
                panel, trad_mask, position, neutralize, industry, size,
            )
            factor_cache[key] = factor[in_is]
        return factor_cache[key]

    dedup_report: _dedup.DedupReport | None = None
    if cfg.require_dedup:
        emit(
            "dedup",
            f"选择期值级去重（逐日 Rank 相关，拒 |ρ| ≥ {cfg.dedup_corr_reject:g}，"
            f"遭拒递补至 Top-{cfg.top_k} 满）",
        )
        report = _dedup.greedy_select(
            ranked_keys,
            provider=_factor_of,
            top_k=cfg.top_k,
            reject=cfg.dedup_corr_reject,
            warn=cfg.dedup_corr_warn,
            min_names=cfg.min_names_per_day,
            on_drop=lambda key: factor_cache.pop(key, None),
        )
        if report.dropped:
            emit(
                "dedup",
                f"去重跳过 {len(report.dropped)} 个（与已保留因子高相关，"
                "后续候选递补）",
            )
        selected_keys = list(report.kept)
        # 一对都没比过（入选 < 2 / K=1）→ 无报告（没去重过就没有报告，不是空报告）
        dedup_report = report if report.n_pairs > 0 else None
    else:
        selected_keys = ranked_keys[: cfg.top_k]

    selected_factors = {key: _factor_of(key) for key in selected_keys}

    composite_report = _composite_evidence(
        selected_keys, drafts, panel, trad_mask, position, fwd, in_is, in_oos,
        has_oos, cfg, emit,
    )

    quantile_info = _quantile_evidence(
        selected_keys, drafts, selected_factors, fwd, cfg,
    )

    dropped_because: dict[str, tuple[str, float]] = {}
    if dedup_report is not None:
        dropped_because = {
            key: (matched, rho) for key, matched, rho in dedup_report.dropped
        }

    final = []
    for report in drafts:
        if report.key in selected_keys:
            tau, spread = quantile_info.get(report.key, (np.nan, np.nan))
            final.append(CandidateReport(**{
                **report.__dict__,
                "selected": True,
                "quantile_monotonicity": tau,
                "spread_mean": spread,
                "reject_reason": "",
                "max_abs_corr": (
                    dedup_report.max_abs_corr(report.key)
                    if dedup_report is not None else np.nan
                ),
                "corr_warn": (
                    dedup_report.warn_note(report.key) if dedup_report is not None else ""
                ),
            }))
        elif report.key in dropped_because:
            # 已过全部闸门、进了 Top-K，被值级去重挤出——拒因要写清「被谁挤掉」
            matched, rho = dropped_because[report.key]
            threshold = cfg.dedup_corr_reject
            final.append(CandidateReport(**{
                **report.__dict__,
                "reject_reason": f"与 {matched} 高度相关（ρ={rho:.2f}，阈值 {threshold:g}）",
            }))
        elif not report.reject_reason:
            final.append(CandidateReport(**{
                **report.__dict__,
                "reject_reason": "未入选 Top-K（按选择分排序）",
            }))
        else:
            final.append(report)
    return MiningResult(
        candidates=tuple(final),
        selected=tuple(selected_keys),
        filter_report=filter_report,
        neutralize_report=nz_report,
        neutralize_mode=neutralize,
        horizons=tuple(cfg.horizons),
        universe=universe,
        window={
            "mining_start": mining_start,
            "mining_end": mining_end,
            "valid_start": valid_start,
            "valid_end": valid_end,
            "fetch_start": anchor.strftime("%Y-%m-%d"),
            "fetch_end": fetch_end.strftime("%Y-%m-%d"),
            "validation_overlaps_mining": bool(
                has_oos
                and pd.Timestamp(valid_start or mining_start) <= pd.Timestamp(mining_end)
                and pd.Timestamp(valid_end or mining_end) >= pd.Timestamp(mining_start)
            ),
        },
        panel_rows=int(len(panel)),
        n_symbols=int(panel.index.get_level_values(1).nunique()),
        dedup=dedup_report,
        composite=composite_report,
    )


def _selection_score(
    best: HorizonStats | None, stability: _stab.ReselectStats | None
) -> float:
    """选择分 = |RankIC| × 折间符号一致性（无稳定性证据 → 权重取中性 1）。

    幅度与稳定同权（FactorEngine 的 FS 适应度同思路）：防「整窗 |RankIC| 大、
    但一半折反向」的候选在纯幅度排序下霸榜。折结构不足 / 未算稳定性
    （``require_stability=False``）时不加权——没有证据不罚，也不假装稳定。
    """
    if best is None:
        return float("nan")
    weight = 1.0
    if (
        stability is not None
        and stability.eligible
        and np.isfinite(stability.sign_consistency)
    ):
        weight = float(stability.sign_consistency)
    return abs(float(best.rank_ic_mean)) * weight


def _rank_eligible(drafts: list[CandidateReport]) -> list[str]:
    """过闸候选按选择分降序排**全序**（拒因已在草稿上记好，这里不改状态）。

    K 截断不在这里做——选择期去重的贪心步行需要 K 外的递补者（去重被拒者
    不占席）；步行走完自然凑满 K。同分以 |RankIC| 兜底，再平按登记序（稳定排序）。
    """
    eligible = [
        r for r in drafts
        if r.best_horizon is not None and not r.reject_reason
    ]
    eligible.sort(
        key=lambda r: (r.selection_score, abs(r.rank_ic_mean)), reverse=True
    )
    return [r.key for r in eligible]


def _quantile_evidence(
    selected_keys: list[str],
    drafts: list[CandidateReport],
    factors: dict[str, pd.Series],
    fwd: dict[int, pd.Series],
    cfg: StatisticalConfig,
) -> dict[str, tuple[float, float]]:
    """入选候选在 best horizon 上的分组收益证据 → {key: (单调性 τ, 顶底价差均值)}。

    ``factors``：key → 挖掘窗内（已过滤/已中性化/已切窗）因子值——调用方
    对入选集只算一次，这里不再重算。只对入选者算分组（逐日分组循环不便宜）；
    分组不足即 (NaN, NaN)，不硬凑。
    """
    best_by_key = {r.key: r.best_horizon for r in drafts}
    out: dict[str, tuple[float, float]] = {}
    for key in selected_keys:
        horizon = best_by_key.get(key)
        factor = factors.get(key)
        if horizon is None or factor is None:
            out[key] = (np.nan, np.nan)
            continue
        summary = _ic.quantile_returns(
            factor,
            fwd[horizon].reindex(factor.index),
            quantiles=cfg.quantiles,
            min_names=cfg.min_names_per_day,
        )
        out[key] = (
            (summary.monotonicity_tau, summary.spread_mean)
            if summary is not None
            else (np.nan, np.nan)
        )
    return out


def _pick_composite_horizon(
    selected_keys: list[str],
    ic_by_key: dict[str, dict[int, float]],
    horizons: tuple[int, ...],
) -> int | None:
    """h* = 入选集「集体最强」的周期（Σ|RankIC| 最大；并列取更短周期）。

    单个候选各自的 best horizon 可以不同，但合成权重只能有一个共同口径；
    取集体最强是「在哪段时间尺度上这群因子一起说话」的直读。没有任何
    周期有可算 IC → None（不合成，不是拿 0 权重硬算）。
    """
    best_h: int | None = None
    best_score = -np.inf
    for h in horizons:  # cfg.horizons 的先后即并列裁决：靠前的（更短）优先
        values = [
            abs(ic_by_key[key][h])
            for key in selected_keys
            if np.isfinite(ic_by_key.get(key, {}).get(h, np.nan))
        ]
        if not values:
            continue
        score = float(np.sum(values))
        if score > best_score:
            best_score, best_h = score, int(h)
    return best_h


def _composite_evidence(
    selected_keys: list[str],
    drafts: list[CandidateReport],
    panel: pd.DataFrame,
    trad_mask: pd.Series,
    position: pd.Series,
    fwd: dict[int, pd.Series],
    in_is: np.ndarray,
    in_oos: np.ndarray,
    has_oos: bool,
    cfg: StatisticalConfig,
    emit: Callable[[str, str], None],
) -> CompositeReport | None:
    """去重后入选集 → IC 加权合成因子证据（Tier 3 / T-S3-04）；退化 → None。

    流程与普通候选同族（逐周期 IC → best → 样本外 → 分组），差异只有两处，
    都写死在报告中：**不进 BH-FDR**（不在原始候选池内）、**不做截面中性化**
    （合成因子以单行 qlib 表达式落库，中性化回归复现不了——算了它，落库的
    表达式也复现不出报告数字）。这两条是报告与落库表达式的「口径等价」前提。
    """
    if not cfg.composite_enabled or len(selected_keys) < 2:
        return None
    ic_by_key = {
        r.key: {s.horizon: s.rank_ic_mean for s in r.horizons}
        for r in drafts
        if r.key in set(selected_keys)
    }
    horizon = _pick_composite_horizon(selected_keys, ic_by_key, cfg.horizons)
    if horizon is None:
        return None
    plan = _composite.build_composite_plan(
        [
            (_candidates.CANDIDATE_BY_KEY[key], ic_by_key[key][horizon])
            for key in selected_keys
        ],
        horizon=horizon,
        z_window=cfg.composite_z_window,
    )
    if plan is None:
        return None
    emit(
        "composite",
        f"IC 加权合成（{len(plan.components)} 个分量，h*={horizon}，"
        f"z 窗 {plan.z_window}）",
    )
    series = _candidates.compute_panel(panel, plan.candidate()).where(
        trad_mask & (position >= plan.min_history)
    )
    stats = tuple(
        _horizon_stats(
            h,
            _ic.summarize_ic(
                _ic.daily_ic(series[in_is], fwd[h][in_is], min_names=cfg.min_names_per_day),
                min_days=cfg.min_valid_days,
            ),
        )
        for h in cfg.horizons
    )
    best = _best_horizon(stats)
    oos = None
    if has_oos and best is not None:
        oos = _horizon_stats(
            best.horizon,
            _ic.summarize_ic(
                _ic.daily_ic(
                    series[in_oos],
                    fwd[best.horizon][in_oos],
                    min_names=cfg.min_names_per_day,
                ),
                min_days=cfg.oos_min_days,
            ),
        )
    oos_pass, oos_reason = _oos_verdict(best, oos, cfg)
    if best is None:
        oos_reason = "样本内有效日不足"
    tau = spread = np.nan
    if best is not None:
        summary = _ic.quantile_returns(
            series[in_is],
            fwd[best.horizon][in_is],
            quantiles=cfg.quantiles,
            min_names=cfg.min_names_per_day,
        )
        if summary is not None:
            tau, spread = summary.monotonicity_tau, summary.spread_mean
    return CompositeReport(
        key=plan.candidate().key,
        name=plan.candidate().name,
        horizon=plan.horizon,
        components=plan.keys,
        weights=plan.weights,
        component_rank_ics=plan.rank_ics,
        z_window=plan.z_window,
        expression=plan.expression,
        min_history=plan.min_history,
        horizons=stats,
        best_horizon=best.horizon if best else None,
        rank_ic_mean=best.rank_ic_mean if best else np.nan,
        icir=best.icir if best else np.nan,
        t_stat=best.rank_ic_t if best else np.nan,
        p_value=best.rank_ic_p if best else np.nan,
        oos_verified=oos is not None,
        oos_horizon=best.horizon if oos is not None and best else None,
        oos_rank_ic=oos.rank_ic_mean if oos else np.nan,
        oos_n_days=oos.n_days if oos else 0,
        quantile_monotonicity=tau,
        spread_mean=spread,
        eligible=bool(oos_pass and best is not None),
        reject_reason=oos_reason,
    )
