/** 分析文本四段式解析：总体总结 / 分析链路 / 推理论证 / 交易决策。
 *  统一处理两套标记：
 *    A) 【总体总结】【分析链路】【推理论证】【决策】
 *    B) ①/②/③/④ + 小节名（flash 实测输出风格）
 *  无任何标记时回退（2026-10-15 起四级递降）：JSON 内 summary 字段 → 剥离 JSON 后的
 *  散文首段 → 决策扼要行（「本轮 N 项决策：…」）→ 说明句；推理=去掉已调用行、JSON 区段
 *  以及被摘要取用的那段之后的正文。
 *  各段互不重叠（摘要不会在推理里重复出现）。 */
export interface DecItem {
  action: string; // buy / sell / hold / watch
  code?: string;
  name?: string;
  pct?: number;
  stop_loss?: number;
  take_profit?: number;
  move_stop?: number;
  invalidation?: string;
  confidence?: number;
  risk_amount?: number;
  reason?: string;
}

export interface ParsedAnalysis {
  summary: string;
  chain: string;
  reasoning: string;
  decisions: DecItem[];
}

/** 括号匹配扫描文本中所有 JSON 对象（带原文偏移，供剥离 JSON 区段用） */
interface JsonSpan {
  value: unknown;
  start: number;
  end: number;
}

function scanJsonBlocks(text: string): JsonSpan[] {
  const out: JsonSpan[] = [];
  let i = 0;
  while (i < text.length) {
    const s = text.indexOf('{', i);
    if (s < 0) break;
    let depth = 0;
    let inStr = false;
    let j = s;
    for (; j < text.length; j++) {
      const c = text[j];
      if (inStr) {
        if (c === '\\') j++;
        else if (c === '"') inStr = false;
      } else if (c === '"') inStr = true;
      else if (c === '{') depth++;
      else if (c === '}') {
        depth--;
        if (depth === 0) {
          j++;
          break;
        }
      }
    }
    const raw = text.slice(s, j);
    try {
      out.push({ value: JSON.parse(raw), start: s, end: j });
    } catch {
      /* 忽略不可解析片段 */
    }
    i = j;
  }
  return out;
}

/** 括号匹配提取文本中所有 JSON 对象 */
function extractJsonBlocks(text: string): unknown[] {
  return scanJsonBlocks(text).map((b) => b.value);
}

/** 剥离 JSON 区段后的正文（去「已调用」行） */
function proseWithoutJson(text: string, spans: JsonSpan[]): string {
  const parts: string[] = [];
  let cursor = 0;
  for (const s of spans) {
    parts.push(text.slice(cursor, s.start));
    cursor = s.end;
  }
  parts.push(text.slice(cursor));
  return parts
    .join('\n')
    .split('\n')
    .filter((l) => !/^\s*已调用/.test(l))
    .join('\n')
    .trim();
}

/** 段内剥掉 markdown 标题行（#/##…）：真实 agent 输出常以「# 盘前持仓分析（日期）」开头，
 *  标题当摘要=「总结没有」（decision/contract.py 记过这条）——摘要取内容句，标题行不进摘要。 */
function stripHeadingLines(para: string): string {
  return para
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !/^#{1,6}\s/.test(l))
    .join('\n')
    .trim();
}

/** 从 JSON 块里挑第一个非空字符串字段（模型输出字段名不统一，多别名探测） */
function jsonStrField(blocks: unknown[], keys: string[]): string {
  for (const b of blocks) {
    if (!b || typeof b !== 'object' || Array.isArray(b)) continue;
    const rec = b as Record<string, unknown>;
    for (const k of keys) {
      const v = rec[k];
      if (typeof v === 'string' && v.trim()) return v.trim();
    }
  }
  return '';
}

/** 决策动作中文化（JSON 里 buy/sell 与中文混用） */
const DEC_ACTION_CN: Record<string, string> = {
  buy: '买入', sell: '卖出', hold: '持有', watch: '观察',
  add: '加仓', reduce: '减仓', clear: '清仓', open: '开仓', close: '平仓',
};

/** 决策项 → 一行给人看的扼要（「本轮 3 项决策：卖出 600338.SH 100% · …」） */
function decisionDigest(items: DecItem[]): string {
  const seg = items.slice(0, 4).map((d) => {
    const act = DEC_ACTION_CN[String(d.action || '').toLowerCase()] ?? String(d.action || '');
    const who = d.name || d.code || '';
    const pct = d.pct != null ? ` ${Math.round(d.pct * 100)}%` : '';
    return `${act} ${who}${pct}`.trim();
  });
  return `本轮 ${items.length} 项决策：${seg.join(' · ')}${items.length > 4 ? ' …' : ''}`;
}

function toDecItems(blocks: unknown[]): DecItem[] {
  const items: DecItem[] = [];
  for (const b of blocks) {
    if (Array.isArray(b)) {
      for (const it of b) {
        if (it && typeof it === 'object' && (it as DecItem).action) items.push(it as DecItem);
      }
    } else if (b && typeof b === 'object') {
      const rec = b as Record<string, unknown>;
      const arr = rec.decisions ?? rec.actions ?? rec.trades;
      if (Array.isArray(arr)) {
        for (const it of arr) {
          if (it && typeof it === 'object' && (it as DecItem).action) items.push(it as DecItem);
        }
      } else if (rec.action) {
        items.push(rec as unknown as DecItem);
      }
    }
  }
  return items.slice(0, 8);
}

/** 段标与编号剔除：seg() 只跳过一个字符（按 ① 设计），5 字【段标】会残留
 *  「推理论证】」标签（含 ①【段标】写法切片后的「【段标】」形态）——段首一并剔；
 *  段尾孤立编号也回剔。这些串只出现在段标位，正文同形字串概率可忽略。 */
const clean = (x: string) =>
  x
    .replace(/^[①②③④]?\s*/, '')
    .replace(/^【?(?:总体总结|分析链路|推理论证|决策)】\s*/, '')
    .replace(/[①②③④]\s*$/, '')
    .trim();

export function parseAnalysis(thought?: string | null): ParsedAnalysis {
  const t = (thought ?? '').trim();
  const out: ParsedAnalysis = { summary: '', chain: '', reasoning: t, decisions: [] };
  if (!t) return out;

  const idxOf = (re: RegExp) => {
    const m = t.match(re);
    return m && m.index != null ? m.index : -1;
  };
  const hasBra = /【总体总结】|【分析链路】|【推理论证】|【决策】/.test(t);
  const i1 = idxOf(/①|【总体总结】/);
  const i2 = idxOf(/②|【分析链路】/);
  const i3 = idxOf(/③|【推理论证】/);
  const i4 = idxOf(/④|【决策】/);
  const hasNum = [i1, i2, i3, i4].some((i) => i >= 0);

  if (hasNum) {
    // 编号/【】位置分隔，各段互不重叠
    const seg = (from: number, to: number) =>
      from >= 0 ? clean(t.slice(from + 1, to >= 0 ? to : undefined)) : '';
    out.summary = i1 >= 0 ? seg(i1, i2 >= 0 ? i2 : i3 >= 0 ? i3 : i4) : '';
    out.chain = seg(i2, i3);
    const reasonRaw = seg(i3, i4);
    out.reasoning = reasonRaw || t;
    const decRaw = i4 >= 0 ? t.slice(i4 + 1) : '';
    out.decisions = toDecItems(extractJsonBlocks(hasBra ? decRaw : decRaw || t));
    if (!out.summary && i1 < 0 && i2 > 0) {
      // 无非标题①直接②开头：②前（如有）即摘要
      const pre2 = clean(t.slice(0, i2));
      out.summary = pre2;
    }
    out.summary = out.summary || (t.split('\n').map((l) => l.trim()).find((l) => l.length > 0) ?? '');
    if (!hasBra && !out.chain && i2 >= 0 && i3 > i2) {
      out.chain = clean(t.slice(i2 + 1, i3));
    }
  } else if (hasBra) {
    // 仅【】标记：按原逻辑
    const mH = t.match(/【总体总结】([\s\S]*?)(?=【分析链路】|【推理论证】|【决策】|$)/);
    const mC = t.match(/【分析链路】([\s\S]*?)(?=【推理论证】|【决策】|$)/);
    const mR = t.match(/【推理论证】([\s\S]*?)(?=【决策】|$)/);
    const mD = t.match(/【决策】([\s\S]*)$/);
    if (mH) out.summary = clean(mH[1]);
    if (mC) out.chain = clean(mC[1]);
    if (mR) out.reasoning = clean(mR[1]);
    out.decisions = toDecItems(extractJsonBlocks(mD ? mD[1] : t));
    if (!mH) {
      const endM = t.match(/[②③④]|^分析链路|^推理论证|^\s*决策|^【/m);
      const body = endM && endM.index != null ? t.slice(0, endM.index) : t;
      out.summary = clean(body);
    }
  } else {
    // 完全无标记。2026-10-15：agent 的真实输出两种形态都会走到这里——纯结构化 JSON
    // （决策轮）与 markdown 报告（分析轮，首段是「# 盘前持仓分析（日期）」标题）。旧
    // 「摘要=首段」前者把 JSON 印满总结条、后者总结=标题=「没有」（contract.py 记过）。
    // 四级递降：JSON 内 summary 字段 → 首个**含正文**的段（标题行不算）→ 决策扼要行
    // → 说明句；推理与摘要**互不重叠**——摘要实际取自哪段，推理就剔哪段。
    // （2026-10-15 评审修复：旧实现无条件 slice(1)——markdown 报告时摘要句在推理里
    //   复读；短段+决策 JSON 轮时首段既没进摘要也被吞。）
    const spans = scanJsonBlocks(t);
    const blocks = spans.map((s) => s.value);
    const prose = proseWithoutJson(t, spans);
    const paras = prose.split('\n\n').map((s) => s.trim()).filter(Boolean);
    const firstPar = paras[0] ?? '';
    const proseUsable = prose.length >= 4 && !/^[{[]/.test(prose);
    out.decisions = toDecItems(blocks);

    const recSummary = jsonStrField(blocks, ['summary', 'overall', 'market_view', '总体总结', '市场总结', '总结']);
    const recReason = jsonStrField(blocks, ['analysis', 'reasoning', 'rationale', 'comment', '推理论证', '分析']);
    const stripped = proseUsable ? paras.map((p) => stripHeadingLines(p)) : [];
    const proseIdx = proseUsable ? stripped.findIndex((p) => p.length >= 6) : -1;
    if (recSummary) out.summary = clean(recSummary).slice(0, 260);
    else if (proseIdx >= 0) out.summary = clean(stripped[proseIdx]).slice(0, 260);
    else if (out.decisions.length) out.summary = decisionDigest(out.decisions);
    else if (spans.length || /^[{[]/.test(t.trim())) out.summary = '结构化输出未能解析（展开查看原始记录）';
    else out.summary = clean(firstPar).slice(0, 260);

    if (recReason) out.reasoning = recReason;
    else if (recSummary) out.reasoning = prose; // 摘要来自 JSON 字段：正文段整段进推理
    else if (proseIdx >= 0) out.reasoning = paras.filter((p, i) => i !== proseIdx && stripHeadingLines(p) !== '').join('\n\n'); // 摘要取自该段+纯标题段均不进推理
    else if (out.decisions.length || spans.length || /^[{[]/.test(t.trim())) out.reasoning = prose; // 摘要=决策扼要/未能解析说明：正文整段进推理（截断 JSON 原文不丢料）
    else out.reasoning = paras.slice(1).join('\n\n'); // 摘要=首段兜底，推理不重复首段
  }
  return out;
}