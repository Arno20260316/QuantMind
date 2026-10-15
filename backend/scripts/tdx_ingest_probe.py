#!/usr/bin/env python3
"""通达信桥数据面全量接入 · 批次 0 实桥探针（只读，不写 PG）。

逐项回答实施细案的 6 个未知数（GP 形态与批量上限 / zdt·pricevol 粒度 /
BK 代码来源 / 各数据集最早可及日期 / FN 分页语义 / more_info 全字段样本），
原始 JSON 样本归档到 data/tdx_bridge/_probe/，同时打印形状摘要。

用法（宿主直接可跑，stdlib-only）:
    python backend/scripts/tdx_ingest_probe.py --all
    python backend/scripts/tdx_ingest_probe.py --group gp
    python backend/scripts/tdx_ingest_probe.py --case gp_value_2stocks

桥配额 600 req/min 全端点共享：本探针全量 ~40 次调用、0.3s 节拍，远在预算内。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tdx-probe")

PACE_S = 0.3
READ_TIMEOUT = 30.0  # 桥内 call 超时 8s，读完留足网络余量


def _load_env() -> tuple[str, str]:
    """TDX_BRIDGE_URL/TOKEN：优先环境变量，回退仓库 .env（宿主手跑场景）。"""
    url = os.getenv("TDX_BRIDGE_URL", "").strip()
    token = os.getenv("TDX_BRIDGE_TOKEN", "").strip()
    if not url or not token:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TDX_BRIDGE_URL=") and not url:
                    url = line.split("=", 1)[1].strip()
                elif line.startswith("TDX_BRIDGE_TOKEN=") and not token:
                    token = line.split("=", 1)[1].strip()
    return url.rstrip("/"), token


def _archive_dir() -> Path:
    base = ROOT / "data" / "tdx_bridge" / "_probe"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # 只读/权限异常时退到 /tmp，不阻断探针
        log.warning("归档目录不可写(%s)，退到 /tmp/tdx_probe", e)
        base = Path("/tmp/tdx_probe")
        base.mkdir(parents=True, exist_ok=True)
    return base


def _to_suffix(code: str) -> str:
    """SH600036 → 600036.SH；已是后缀式则原样。"""
    code = str(code or "").strip()
    if "." in code:
        return code
    if len(code) == 8 and code[:2].isalpha():
        return f"{code[2:]}.{code[:2].upper()}"
    return code


class Probe:
    def __init__(self, url: str, token: str, archive: Path, pace: float, tag: str = ""):
        self.url = url
        self.token = token
        self.archive = archive
        self.pace = pace
        self.tag = tag
        self.seq = 0
        self.calls = 0
        self.failures: list[str] = []
        self.state: dict = {}  # 组间共享（如 universe 列表）

    def call(self, case: str, method: str, params: dict) -> dict | None:
        """调桥一次并归档原始信封；返回 result（失败返回 None）。"""
        self.seq += 1
        self.calls += 1
        time.sleep(self.pace)
        body = json.dumps(
            {"method": method, "params": params}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/api/v1/tdx/call",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        envelope: dict
        try:
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
                envelope = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            envelope = {"success": False, "error": f"HTTP {e.code}", "detail": detail}
        except Exception as e:
            envelope = {"success": False, "error": f"{type(e).__name__}: {e}"}

        sample = {
            "case": case,
            "method": method,
            "params": params,
            "taken_at": datetime.now().isoformat(timespec="seconds"),
            "envelope": envelope,
        }
        path = self.archive / f"{self.tag}{self.seq:02d}_{case}.json"
        try:
            path.write_text(
                json.dumps(sample, ensure_ascii=False, indent=1)[:8_000_000],
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("样本归档失败 %s: %s", path, e)

        ok = bool(envelope.get("success")) and envelope.get("result") is not None
        tag = "OK " if ok else "FAIL"
        log.info(
            "[%s] #%02d %s(%s) → %s",
            tag,
            self.seq,
            case,
            method,
            _describe(envelope.get("result") if ok else envelope.get("error")),
        )
        if not ok:
            self.failures.append(case)
        return envelope.get("result") if ok else None

    def run_case(self, case: str, method: str, params: dict):
        return self.call(case, method, params)


def _describe(obj, depth: int = 0) -> str:
    """形状摘要：dict 键 + 类型/长度；list 长度 + 首元素预览。"""
    if obj is None:
        return "None"
    if isinstance(obj, bool):
        return str(obj)
    if isinstance(obj, (int, float, str)):
        s = repr(obj)
        return s[:80]
    if isinstance(obj, list):
        head = _describe(obj[0], depth + 1) if obj else "空"
        return f"list[{len(obj)}] 首: {head[:120]}"
    if isinstance(obj, dict):
        keys = list(obj.keys())
        if depth == 0:
            parts = []
            for k in keys[:8]:
                parts.append(f"{k}: {_describe(obj[k], depth + 1)[:60]}")
            more = f" …共{len(keys)}键" if len(keys) > 8 else ""
            return "{" + "; ".join(parts) + more + "}"
        return f"dict(keys={keys[:6]}{'…' if len(keys) > 6 else ''})"
    return type(obj).__name__


# ---------------------------------------------------------------- 各组探针


def group_universe(p: Probe):
    """U3 前置：全 A 清单与代码格式（供批量探针取样本）。"""
    for case, market in (
        ("universe_none", None),
        ("universe_ag", "AG"),
        ("universe_0", 0),
    ):
        r = p.run_case(case, "get_stock_list", {"market": market, "list_type": 0})
        if r is not None and case == "universe_none":
            lst = (
                r
                if isinstance(r, list)
                else r.get("Value")
                if isinstance(r, dict)
                else None
            )
            if isinstance(lst, list) and lst:
                p.state["codes_raw"] = lst[:60]
                p.state["codes"] = [
                    _to_suffix(c) for c in lst[:60] if isinstance(c, str)
                ]
                p.state["codes_all"] = [
                    _to_suffix(c) for c in lst if isinstance(c, str)
                ]
                log.info("universe 样本: 前5=%s 共传入样本=%d", lst[:5], len(lst))


def group_gp(p: Probe):
    """U1：GP 返回形态与批量上限。"""
    probe_fields = [
        "GP01",
        "GP02",
        "GP03",
        "GP06",
        "GP14",
        "GP15",
        "GP16",
        "GP21",
        "GP24",
        "GP27",
        "GP30",
        "GP36",
    ]
    p.run_case(
        "gp_value_2stocks",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH", "000001.SZ"],
            "field_list": probe_fields,
            "start_time": "20261001",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "gp_value_1field_history",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP16"],
            "start_time": "20260901",
            "end_time": "20261015",
        },
    )
    codes60 = p.state.get("codes") or ["600036.SH"]
    p.run_case(
        "gp_value_batch50",
        "get_gpjy_value",
        {
            "stock_list": codes60[:50],
            "field_list": ["GP16"],
            "start_time": "20261014",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "gp_value_empty_list",
        "get_gpjy_value",
        {
            "stock_list": [],
            "field_list": ["GP16"],
            "start_time": "20261014",
            "end_time": "20261014",
        },
    )
    p.run_case(
        "gp_value_no_dates",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP16"],
            "start_time": "",
            "end_time": "",
        },
    )
    p.run_case(
        "gp_by_date",
        "get_gpjy_value_by_date",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP16"],
            "year": 2026,
            "mmdd": 1014,
        },
    )
    p.run_case(
        "gp_by_date_latest",
        "get_gpjy_value_by_date",
        {"stock_list": ["600036.SH"], "field_list": ["GP16"], "year": 0, "mmdd": 0},
    )


def group_zdt(p: Probe):
    """U2：zdt / pricevol 调用粒度。"""
    trio = ["600036.SH", "000001.SZ", "300750.SZ"]
    p.run_case("zdt_3stocks", "get_zdt_data", {"stock_list": trio})
    p.run_case("zdt_empty", "get_zdt_data", {"stock_list": []})
    p.run_case("pricevol_3stocks", "get_pricevol", {"stock_list": trio})


def group_limits(p: Probe):
    """U2 补：zdt / pricevol 批量上限 + universe market='23' 变体。"""
    codes = p.state.get("codes_all") or ["600036.SH", "000001.SZ"]
    p.run_case("zdt_500", "get_zdt_data", {"stock_list": codes[:500]})
    p.run_case("zdt_1000", "get_zdt_data", {"stock_list": codes[:1000]})
    p.run_case("zdt_2000", "get_zdt_data", {"stock_list": codes[:2000]})
    p.run_case("pricevol_500", "get_pricevol", {"stock_list": codes[:500]})
    p.run_case("pricevol_full", "get_pricevol", {"stock_list": codes})
    p.run_case("universe_23", "get_stock_list", {"market": "23", "list_type": 0})


def group_lists(p: Probe):
    """引用清单：get_stock_list market 取值表实测 + 活码 kzz 形态。"""
    p.run_case("list_5_allA", "get_stock_list", {"market": "5", "list_type": 0})
    p.run_case("list_22_kzz_stocks", "get_stock_list", {"market": "22", "list_type": 0})
    r = p.run_case("list_32_cb", "get_stock_list", {"market": "32", "list_type": 1})
    v = (r if isinstance(r, list) else r.get("Value")) if r is not None else None
    cb_codes: list[str] = []
    for item in (v or [])[:5]:
        code = item.get("Code") if isinstance(item, dict) else item
        if code:
            cb_codes.append(str(code))
    for code in cb_codes[:3]:
        p.run_case(
            f"kzz_live_{code.split('.')[0]}",
            "get_kzz_info",
            {"stock_code": code, "field_list": []},
        )
    p.run_case("list_91_etf_index", "get_stock_list", {"market": "91", "list_type": 1})
    p.run_case("ipo_upcoming", "get_ipo_info", {"ipo_type": 2, "ipo_date": 1})


def group_refresh(p: Probe):
    """关键假设：refresh_cache 能否让 GP/SC/FN/BK/GO 家族免数据包可用。"""
    p.run_case("refresh_warm", "refresh_cache", {"market": "AG", "force": False})
    p.run_case(
        "gp_after_refresh",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP16"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "sc_after_refresh",
        "get_scjy_value",
        {"field_list": ["SC01"], "start_time": "20261008", "end_time": "20261015"},
    )
    p.run_case(
        "fn_after_refresh",
        "get_financial_data",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["FN1"],
            "start_time": "20260101",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "bk_after_refresh",
        "get_bkjy_value",
        {
            "stock_list": ["880201.SH", "881479.SH"],
            "field_list": ["BK5", "BK12"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "go_after_refresh",
        "get_gp_one_data",
        {"stock_list": ["600036.SH", "000001.SZ"], "field_list": ["GO3", "GO33"]},
    )


def group_refresh_force(p: Probe):
    """refresh_warm 未能解锁时，强制刷新后再试（GP 命名字段两版同验）。"""
    p.run_case("refresh_force", "refresh_cache", {"market": "AG", "force": True})
    p.run_case(
        "gp_np_after_force",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP1", "GP3"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "gp_pad_after_force",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP01", "GP03"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )


def group_kline(p: Probe):
    """批次 3 预探：K 线对账源形态（桥封装只有 count，无 start/end）。"""
    p.run_case(
        "kline_daily_5",
        "get_market_data",
        {
            "stock_list": ["600036.SH"],
            "period": "1d",
            "count": 5,
            "dividend_type": "front",
        },
    )


def group_bk(p: Probe):
    """U3：BK 代码来源与板块序列。"""
    r0 = p.run_case("sector_list_0", "get_sector_list", {"list_type": 0})
    p.run_case("sector_list_1", "get_sector_list", {"list_type": 1})
    sector_codes = []
    for r in (r0,):
        lst = (
            r
            if isinstance(r, list)
            else (r.get("Value") if isinstance(r, dict) else None)
        )
        if isinstance(lst, list):
            for item in lst[:30]:
                if isinstance(item, str):
                    sector_codes.append(item)
                elif isinstance(item, dict):
                    code = (
                        item.get("Code") or item.get("code") or item.get("block_code")
                    )
                    if code:
                        sector_codes.append(str(code))
    p.state["sector_codes"] = sector_codes[:10]
    if sector_codes:
        p.run_case(
            "bk_value_sample",
            "get_bkjy_value",
            {
                "stock_list": [_to_suffix(c) for c in sector_codes[:5]],
                "field_list": ["BK5", "BK9", "BK12", "BK14", "BK15", "BK17"],
                "start_time": "20261001",
                "end_time": "20261015",
            },
        )
        p.run_case(
            "sector_members",
            "get_stock_list_in_sector",
            {"block_code": sector_codes[0], "block_type": 0, "list_type": 0},
        )


def group_dates(p: Probe):
    """U4：各数据集最早可及日期。"""
    p.run_case(
        "sc_latest",
        "get_scjy_value",
        {
            "field_list": ["SC01", "SC03", "SC24", "SC31", "SC33"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "sc_2016",
        "get_scjy_value",
        {
            "field_list": ["SC01", "SC02", "SC03", "SC16"],
            "start_time": "20160101",
            "end_time": "20160131",
        },
    )
    p.run_case(
        "sc_2010",
        "get_scjy_value",
        {
            "field_list": ["SC01", "SC16"],
            "start_time": "20100101",
            "end_time": "20100331",
        },
    )
    for year, mmdd, case in (
        (2010, 115, "gp_by_date_2010"),
        (2005, 104, "gp_by_date_2005"),
    ):
        p.run_case(
            case,
            "get_gpjy_value_by_date",
            {
                "stock_list": ["600036.SH"],
                "field_list": ["GP01", "GP16"],
                "year": year,
                "mmdd": mmdd,
            },
        )
    p.run_case(
        "gp_early_2005",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP01", "GP16"],
            "start_time": "20050104",
            "end_time": "20050331",
        },
    )
    # 命名字段变体（GP01 vs GP1 / SC01 vs SC1）：数据包就位后一次判明哪种被服务端接受
    p.run_case(
        "gp_value_np",
        "get_gpjy_value",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP1", "GP2", "GP3", "GP16"],
            "start_time": "20261001",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "gp_by_date_np",
        "get_gpjy_value_by_date",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["GP1"],
            "year": 2026,
            "mmdd": 1014,
        },
    )
    p.run_case(
        "sc_np",
        "get_scjy_value",
        {
            "field_list": ["SC1", "SC3"],
            "start_time": "20261008",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "sc_by_date_np",
        "get_scjy_value_by_date",
        {"field_list": ["SC1"], "year": 2026, "mmdd": 1014},
    )


def group_fn(p: Probe):
    """U5：FN 分页/批量语义 + 最早报告期。"""
    fields = ["FN1", "FN2", "FN3", "FN5", "FN100", "FN107"]
    p.run_case(
        "fn_1stock_wide",
        "get_financial_data",
        {
            "stock_list": ["600036.SH"],
            "field_list": fields,
            "start_time": "19980101",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "fn_2stocks",
        "get_financial_data",
        {
            "stock_list": ["600036.SH", "000001.SZ"],
            "field_list": ["FN1", "FN2"],
            "start_time": "20250101",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "fn_empty_fields",
        "get_financial_data",
        {
            "stock_list": ["600036.SH"],
            "field_list": [],
            "start_time": "20260101",
            "end_time": "20261015",
        },
    )
    p.run_case(
        "fn_by_date",
        "get_financial_data_by_date",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["FN1", "FN2"],
            "year": 2025,
            "mmdd": 1231,
        },
    )
    p.run_case(
        "fn_report_type_announce",
        "get_financial_data",
        {
            "stock_list": ["600036.SH"],
            "field_list": ["FN1"],
            "start_time": "20260101",
            "end_time": "20261015",
            "report_type": "announce_time",
        },
    )


def group_more_info(p: Probe):
    """U6：more_info 全字段样本 + stock_info 对照。"""
    p.run_case("more_info_1", "get_more_info", {"stock_code": "600036.SH"})
    p.run_case("stock_info_1", "get_stock_info", {"stock_code": "600036.SH"})
    p.run_case("exday_1", "get_exday_data", {"stock_code": "600036.SH", "count": 2})


def group_go(p: Probe):
    """GO：一致预期形态与批量。"""
    codes60 = p.state.get("codes") or ["600036.SH"]
    p.run_case(
        "go_2stocks_explicit",
        "get_gp_one_data",
        {
            "stock_list": ["600036.SH", "000001.SZ"],
            "field_list": ["GO3", "GO5", "GO26", "GO29", "GO33", "GO35"],
        },
    )
    p.run_case(
        "go_empty_fields",
        "get_gp_one_data",
        {"stock_list": ["600036.SH"], "field_list": []},
    )
    p.run_case(
        "go_batch50",
        "get_gp_one_data",
        {"stock_list": codes60[:50], "field_list": ["GO33"]},
    )


def group_ref(p: Probe):
    """批次 2 预探：gb/divid/kzz/ipo/trackzs/日历 形态（低成本去险）。

    首轮教训：kzz/trackzs 传空 code 报 codestr error（是探针参数问题，非环境）；
    gb_info 空 date_list 返回 Date=0 退化行 ⇒ 必须显式传升序日期。
    """
    p.run_case(
        "gb_info_dated",
        "get_gb_info",
        {"stock_code": "600036.SH", "date_list": ["20260930", "20261013"], "count": 2},
    )
    p.run_case(
        "divid_dated",
        "get_divid_factors",
        {"stock_code": "600036.SH", "start_time": "20250101", "end_time": "20251231"},
    )
    for code in ("110059.SH", "113050.SH", "123120.SZ"):
        p.run_case(
            f"kzz_{code.split('.')[0]}",
            "get_kzz_info",
            {"stock_code": code, "field_list": []},
        )
    p.run_case("trackzs_csi300", "get_trackzs_etf_info", {"zs_code": "000300.SH"})
    p.run_case("trackzs_cyb", "get_trackzs_etf_info", {"zs_code": "399006.SZ"})
    p.run_case("ipo_type1", "get_ipo_info", {"ipo_type": 1, "ipo_date": 0})
    p.run_case("ipo_today", "get_ipo_info", {"ipo_type": 0, "ipo_date": 20261015})
    p.run_case("trading_dates_5", "get_trading_dates", {"market": "", "count": 5})
    p.run_case(
        "trading_dates_range",
        "get_trading_dates",
        {"market": "", "start_time": "20261001", "end_time": "20261015"},
    )


GROUPS = {
    "universe": group_universe,
    "gp": group_gp,
    "zdt": group_zdt,
    "bk": group_bk,
    "dates": group_dates,
    "fn": group_fn,
    "more_info": group_more_info,
    "go": group_go,
    "ref": group_ref,
    "limits": group_limits,
    "kline": group_kline,
    "lists": group_lists,
    "refresh": group_refresh,
    "refresh_force": group_refresh_force,
}


def main():
    parser = argparse.ArgumentParser(description="通达信桥批次 0 探针（只读）")
    parser.add_argument("--all", action="store_true", help="跑全部组")
    parser.add_argument("--group", choices=sorted(GROUPS), help="只跑某组")
    parser.add_argument(
        "--pace", type=float, default=PACE_S, help="调用节拍秒（默认 0.3）"
    )
    parser.add_argument(
        "--tag", default="", help="归档文件名前缀（防多轮跑串号，如 r2_）"
    )
    args = parser.parse_args()

    if not args.all and not args.group:
        parser.print_help()
        return 2

    url, token = _load_env()
    if not url or not token:
        log.error("TDX_BRIDGE_URL/TOKEN 未配置（环境变量或 .env）")
        return 2
    archive = _archive_dir()
    p = Probe(url, token, archive, max(args.pace, 0.0), tag=args.tag)
    log.info("桥=%s 归档=%s", url, archive)

    # 代码格式默认值：批量探针依赖 universe，全部跑时强制先跑 universe
    if args.group != "universe":
        group_universe(p)

    if args.all:
        names = list(GROUPS)
    else:
        names = [args.group]
    for name in names:
        log.info("━━━ 组: %s ━━━", name)
        try:
            GROUPS[name](p)
        except Exception as e:
            log.error("组 %s 异常: %s", name, e)
            p.failures.append(f"group:{name}")

    log.info(
        "━━━ 完成: 共 %d 次调用, 失败 %d —— 归档 %s", p.calls, len(p.failures), archive
    )
    if p.failures:
        log.warning("失败清单: %s", ", ".join(p.failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
