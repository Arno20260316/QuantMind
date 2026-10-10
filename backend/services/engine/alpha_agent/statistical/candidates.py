"""统计派 · 候选因子族（移植 P2 / Tier 2）。

28 个只用 OHLCV+成交额的价量候选（含 原实现 7 个：5 日反转 / 20 日
动量 / 放量 / 额突增 / 5 日非流动性 / 5 日振幅 / 5 日跳空），每个带：数学式、
文献出处、pandas 实现与 **qlib 表达式**（factor_code 落库/回测形态）。

双表示的纪律（比原实现的「pandas + qd.panel 代码体」更严）：

- pandas 实现严格对齐 **qlib 算子语义**（`qlib/data/ops.py` 实测：滚动窗口
  ``min_periods=1``、`Std` 即 pandas ``std()``（ddof=1）、逐元素 max 是
  ``Greater`` 而非 ``Max``（`Max` 只有滚动重载！）、`If(cond,a,b)`）；
- 等价性由集成测试钉住：同一份 qlib 取出的 OHLCV 面板上，qlib 表达式求值 与
  ``compute`` 输出逐点对比（``test_statistical_candidates.py``）。表达式改了
  而实现没改（或反之）会直接红。

方向（正/负 IC 预期）不在此固定——由数据说话，IC/RankIC 符号进结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

import numpy as np
import pandas as pd

#: 候选计算所需的行情列（QuantDB 日线面板）
REQUIRED_COLUMNS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)

FAMILY_LABELS: dict[str, str] = {
    "reversal": "反转",
    "momentum": "动量",
    "lottery": "彩票/偏度",
    "volatility": "波动率",
    "liquidity": "流动性/量能",
    "price_volume": "量价关系",
    "intraday": "日内结构",
}


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    family: str
    formulation: str
    source: str
    expression: str
    min_history: int  # 表达式所需最少 bar 数（引擎据此裁每个 symbol 的头部）
    compute: Callable[[pd.DataFrame], pd.Series]


def _ret(frame: pd.DataFrame) -> pd.Series:
    return frame["close"] / frame["close"].shift(1) - 1


def _mean(series: pd.Series, window: int) -> pd.Series:
    # min_periods=1 与 qlib Rolling 一致（qlib 不裁头部——暖机由引擎按
    # min_history 显式裁，算得对不对与用不用得到分开管）
    return series.rolling(window, min_periods=1).mean()


def _std(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=1).std()


# ── 反转族 ───────────────────────────────────────────────────────────


def _reversal(frame: pd.DataFrame, window: int) -> pd.Series:
    return frame["close"].shift(window) / frame["close"] - 1


# ── 动量族 ───────────────────────────────────────────────────────────


def _momentum(frame: pd.DataFrame, window: int) -> pd.Series:
    return frame["close"] / frame["close"].shift(window) - 1


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        key="reversal_5d",
        name="5 日反转",
        family="reversal",
        formulation="close[t-5]/close[t] - 1",
        source="Jegadeesh(1990)；A 股短期反转（原实现候选同口径）",
        expression="Ref($close,5)/$close-1",
        min_history=5,
        compute=lambda frame: _reversal(frame, 5),
    ),
    Candidate(
        key="reversal_10d",
        name="10 日反转",
        family="reversal",
        formulation="close[t-10]/close[t] - 1",
        source="Lehmann(1990)",
        expression="Ref($close,10)/$close-1",
        min_history=10,
        compute=lambda frame: _reversal(frame, 10),
    ),
    Candidate(
        key="reversal_20d",
        name="20 日反转",
        family="reversal",
        formulation="close[t-20]/close[t] - 1",
        source="Jegadeesh(1990)",
        expression="Ref($close,20)/$close-1",
        min_history=20,
        compute=lambda frame: _reversal(frame, 20),
    ),
    Candidate(
        key="reversal_60d",
        name="60 日反转",
        family="reversal",
        formulation="close[t-60]/close[t] - 1",
        source="De Bondt & Thaler(1985) 长期反转",
        expression="Ref($close,60)/$close-1",
        min_history=60,
        compute=lambda frame: _reversal(frame, 60),
    ),
    Candidate(
        key="momentum_20d",
        name="20 日动量",
        family="momentum",
        formulation="close[t]/close[t-20] - 1",
        source="Jegadeesh & Titman(1993)（原实现候选同口径）",
        expression="$close/Ref($close,20)-1",
        min_history=20,
        compute=lambda frame: _momentum(frame, 20),
    ),
    Candidate(
        key="momentum_60d",
        name="60 日动量",
        family="momentum",
        formulation="close[t]/close[t-60] - 1",
        source="Jegadeesh & Titman(1993)",
        expression="$close/Ref($close,60)-1",
        min_history=60,
        compute=lambda frame: _momentum(frame, 60),
    ),
    Candidate(
        key="momentum_120d",
        name="120 日动量",
        family="momentum",
        formulation="close[t]/close[t-120] - 1",
        source="Carhart(1997) 动量因子",
        expression="$close/Ref($close,120)-1",
        min_history=120,
        compute=lambda frame: _momentum(frame, 120),
    ),
    Candidate(
        key="high_prox_250d",
        name="52 周高点接近度",
        family="momentum",
        formulation="close[t] / max(close, 250)",
        source="George & Hwang(2004) 52-week high",
        expression="$close/Max($close,250)",
        min_history=250,
        compute=lambda frame: frame["close"]
        / frame["close"].rolling(250, min_periods=1).max(),
    ),
    Candidate(
        key="max_ret_20d",
        name="20 日最大单日收益",
        family="lottery",
        formulation="max(ret, 20)",
        source="Bali, Cakici & Whitelaw(2011) MAX 效应",
        expression="Max($close/Ref($close,1)-1,20)",
        min_history=21,
        compute=lambda frame: _ret(frame).rolling(20, min_periods=1).max(),
    ),
    Candidate(
        key="skew_20d",
        name="20 日收益偏度",
        family="lottery",
        formulation="skew(ret, 20)",
        source="Boyer, Mitton & Vorkink(2010)；Barberis & Huang(2008)",
        expression="Skew($close/Ref($close,1)-1,20)",
        min_history=21,
        compute=lambda frame: _ret(frame).rolling(20, min_periods=1).skew(),
    ),
    Candidate(
        key="vol_20d",
        name="20 日波动率",
        family="volatility",
        formulation="std(ret, 20)",
        source="Ang, Hodrick, Xing & Zhang(2006)",
        expression="Std($close/Ref($close,1)-1,20)",
        min_history=21,
        compute=lambda frame: _std(_ret(frame), 20),
    ),
    Candidate(
        key="vol_60d",
        name="60 日波动率",
        family="volatility",
        formulation="std(ret, 60)",
        source="Ang et al.(2006)",
        expression="Std($close/Ref($close,1)-1,60)",
        min_history=61,
        compute=lambda frame: _std(_ret(frame), 60),
    ),
    Candidate(
        key="downside_vol_20d",
        name="20 日下行波动",
        family="volatility",
        formulation="std(min(ret, 0), 20)",
        source="Ang & Chen(2002) 下行风险",
        expression="Std(If($close<Ref($close,1),$close/Ref($close,1)-1,0),20)",
        min_history=21,
        compute=lambda frame: _ret(frame)
        .where(_ret(frame) < 0, 0.0)
        .rolling(20, min_periods=1)
        .std(),
    ),
    Candidate(
        key="parkinson_20d",
        name="20 日 Parkinson 波动",
        family="volatility",
        formulation="sqrt(mean(ln(high/low)^2, 20) / (4 ln 2))",
        source="Parkinson(1980) 极值波动率",
        # 4·ln2 用字面量：qlib 0.9.7 的 Log 只接受表达式（Log(2) 装载即炸），
        # 且 repr(4·ln2)=2.772588722239781 与该 double 精确往返（pandas 侧同式）
        expression="Power(Mean(Power(Log($high/$low),2),20)/2.772588722239781,0.5)",
        min_history=20,
        compute=lambda frame: np.sqrt(
            _mean(np.log(frame["high"] / frame["low"]) ** 2, 20) / (4 * np.log(2))
        ),
    ),
    Candidate(
        key="atr_14",
        name="14 日真实波幅",
        family="volatility",
        formulation="mean(max(high-low, |high-prev_close|, |low-prev_close|), 14) / close",
        source="Wilder(1978) ATR",
        expression=(
            "Mean(Greater(Greater($high-$low,Abs($high-Ref($close,1))),"
            "Abs($low-Ref($close,1))),14)/$close"
        ),
        min_history=15,
        compute=lambda frame: _mean(
            np.maximum(
                np.maximum(
                    frame["high"] - frame["low"],
                    (frame["high"] - frame["close"].shift(1)).abs(),
                ),
                (frame["low"] - frame["close"].shift(1)).abs(),
            ),
            14,
        )
        / frame["close"],
    ),
    Candidate(
        key="amihud_5d",
        name="5 日非流动性",
        family="liquidity",
        formulation="mean(|ret| / (amount + 1e-8), 5)",
        source="Amihud(2002)（原实现 illiq_5d 同口径）",
        expression="Mean(Abs($close/Ref($close,1)-1)/($amount+1e-8),5)",
        min_history=6,
        compute=lambda frame: _mean(_ret(frame).abs() / (frame["amount"] + 1e-8), 5),
    ),
    Candidate(
        key="amihud_20d",
        name="20 日非流动性",
        family="liquidity",
        formulation="mean(|ret| / (amount + 1e-8), 20)",
        source="Amihud(2002)",
        expression="Mean(Abs($close/Ref($close,1)-1)/($amount+1e-8),20)",
        min_history=21,
        compute=lambda frame: _mean(_ret(frame).abs() / (frame["amount"] + 1e-8), 20),
    ),
    Candidate(
        key="volume_spike_20d",
        name="20 日放量倍数",
        family="liquidity",
        formulation="volume / mean(volume, 20)",
        source="原实现候选；Chordia et al.(2001) 交易量",
        expression="$volume/Mean($volume,20)",
        min_history=20,
        compute=lambda frame: frame["volume"] / _mean(frame["volume"], 20),
    ),
    Candidate(
        key="amount_burst_20d",
        name="20 日额突增倍数",
        family="liquidity",
        formulation="amount / mean(amount, 20)",
        source="原实现候选；Chordia et al.(2001)",
        expression="$amount/Mean($amount,20)",
        min_history=20,
        compute=lambda frame: frame["amount"] / _mean(frame["amount"], 20),
    ),
    Candidate(
        key="volume_cv_20d",
        name="20 日成交变异系数",
        family="liquidity",
        formulation="std(volume, 20) / mean(volume, 20)",
        source="Chordia, Roll & Subrahmanyam(2001) 交易量波动",
        expression="Std($volume,20)/Mean($volume,20)",
        min_history=20,
        compute=lambda frame: _std(frame["volume"], 20) / _mean(frame["volume"], 20),
    ),
    Candidate(
        key="pv_corr_20d",
        name="20 日量价相关",
        family="price_volume",
        formulation="corr(close, volume; 20)",
        source="Karpoff(1987) 量价关系综述",
        expression="Corr($close,$volume,20)",
        min_history=20,
        compute=lambda frame: frame["close"]
        .rolling(20, min_periods=1)
        .corr(frame["volume"]),
    ),
    Candidate(
        key="pv_corr_60d",
        name="60 日量价相关",
        family="price_volume",
        formulation="corr(close, volume; 60)",
        source="Karpoff(1987)",
        expression="Corr($close,$volume,60)",
        min_history=60,
        compute=lambda frame: frame["close"]
        .rolling(60, min_periods=1)
        .corr(frame["volume"]),
    ),
    Candidate(
        key="range_5d",
        name="5 日振幅",
        family="intraday",
        formulation="mean((high-low)/close, 5)",
        source="原实现候选 range_5d",
        expression="Mean(($high-$low)/$close,5)",
        min_history=5,
        compute=lambda frame: _mean(
            (frame["high"] - frame["low"]) / frame["close"], 5
        ),
    ),
    Candidate(
        key="range_20d",
        name="20 日振幅",
        family="intraday",
        formulation="mean((high-low)/close, 20)",
        source="日内振幅（波动率近亲）",
        expression="Mean(($high-$low)/$close,20)",
        min_history=20,
        compute=lambda frame: _mean(
            (frame["high"] - frame["low"]) / frame["close"], 20
        ),
    ),
    Candidate(
        key="gap_drift_5d",
        name="5 日跳空漂移",
        family="intraday",
        formulation="mean(open/prev_close - 1, 5)",
        source="原实现候选 gap_drift_5d",
        expression="Mean($open/Ref($close,1)-1,5)",
        min_history=6,
        compute=lambda frame: _mean(
            frame["open"] / frame["close"].shift(1) - 1, 5
        ),
    ),
    Candidate(
        key="overnight_ret_20d",
        name="20 日隔夜收益",
        family="intraday",
        formulation="mean(open/prev_close - 1, 20)",
        source="Lou, Polk & Skouras(2019) 隔夜收益",
        expression="Mean($open/Ref($close,1)-1,20)",
        min_history=21,
        compute=lambda frame: _mean(
            frame["open"] / frame["close"].shift(1) - 1, 20
        ),
    ),
    Candidate(
        key="intraday_ret_20d",
        name="20 日日内收益",
        family="intraday",
        formulation="mean(close/open - 1, 20)",
        source="Lou, Polk & Skouras(2019) 日内/隔夜分解",
        expression="Mean($close/$open-1,20)",
        min_history=20,
        compute=lambda frame: _mean(frame["close"] / frame["open"] - 1, 20),
    ),
    Candidate(
        key="close_position_20d",
        name="20 日收盘位置",
        family="intraday",
        formulation="mean((close-low)/(high-low+1e-12), 20)",
        source="RSV/KDJ 技术面；收盘位置效应",
        expression="Mean(($close-$low)/($high-$low+1e-12),20)",
        min_history=20,
        compute=lambda frame: _mean(
            (frame["close"] - frame["low"])
            / (frame["high"] - frame["low"] + 1e-12),
            20,
        ),
    ),
)

CANDIDATE_BY_KEY: dict[str, Candidate] = {c.key: c for c in CANDIDATES}

#: 暖机 bar 数 = 全体候选的最大回看（引擎据此外扩取数区间）
MAX_MIN_HISTORY: int = max(c.min_history for c in CANDIDATES)


def compute_panel(panel: pd.DataFrame, candidate: Candidate) -> pd.Series:
    """在整块面板上按 symbol 分组计算候选（输入/输出都是 MultiIndex 面板）。

    面板索引 ``(trade_date, symbol)``，按日期升序；组内赋值即逐 symbol 计算。
    输出按 **输入原序** 回排——组结果按 symbol 拼接，顺序与面板不一致，因子序列
    靠索引对齐，顺序走样就是静默错位，这里显式复位。

    逐组 ``concat`` 而不是 ``groupby.apply``：单组面板上 apply 会把「一组一条
    Series」包装成 DataFrame（实测 1 行 × 1 列），``rename(key)`` 随即炸在
    ``Index('key')``；``transform`` 对帧函数是按列传 Series（KeyError）。逐组
    拼装没有这类边界行为，顺便把「compute 必须返回 Series」查在明面上。
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in panel.columns]
    if missing:
        raise ValueError(f"面板缺列: {missing}（候选 {candidate.key} 需要 OHLCV+amount）")
    pieces: list[pd.Series] = []
    for _symbol, group in panel.groupby(level=1, sort=False):
        piece = candidate.compute(group)
        if not isinstance(piece, pd.Series):
            raise TypeError(
                f"候选 {candidate.key} 的 compute 必须返回 Series"
                f"（收到 {type(piece).__name__}）"
            )
        pieces.append(piece)
    if not pieces:
        return pd.Series(dtype=float, index=panel.index, name=candidate.key)
    return pd.concat(pieces).reindex(panel.index).rename(candidate.key)
