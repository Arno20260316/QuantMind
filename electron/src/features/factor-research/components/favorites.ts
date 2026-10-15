/**
 * 收藏（自选）—— localStorage 持久化的最小工具，排行榜与因子报告左栏共用
 * （键由各自调用方构造：两个栏目是两套集合，互不串）。
 *
 * 读坏数据一律当空、写失败静默：收藏是便利功能，不该因为一条手改过的
 * localStorage 或隐私模式配额把整页打崩/弹错。
 */
export function loadFavs(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    const arr = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export function saveFavs(key: string, arr: string[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(arr));
  } catch {
    /* 配额满 / 隐私模式：忽略 */
  }
}

/** 收藏集合的切换（纯函数，不改原数组） */
export function toggleInFavs(arr: string[], name: string): string[] {
  return arr.includes(name) ? arr.filter((x) => x !== name) : [...arr, name];
}

/**
 * 按「栏目 × 市场 × 数据集」读收藏（排行榜与因子报告左栏共用，各自给 prefix）。
 * 键 = `${prefix}:${market}:${ds}` —— **市场必须进键**：各市场因子代码空间不同
 * （A 股 6 位 / 港股 5 位），共用键会把别市场的代码算进「自选 N」，筛出来的行
 * 还一条都对不上（静默错，界面上看不出来）。
 *
 * 旧键（加市场段之前，该功能当时只有 CN）= `${prefix}:${ds}`：仅 CN 且新键
 * **从未写过**时迁移一次。判据必须是「新键在 localStorage 里存不存在」，不是
 * 「读出来是不是空」——否则用户清空自选后会被旧键复活。非 CN 不搬：旧键里
 * 只可能是 A 股代码，搬过去就是让「自选 N」虚高。
 */
export function loadScopedFavs(prefix: string, market: string, ds: string): string[] {
  const key = `${prefix}:${market}:${ds}`;
  let exists = false;
  try {
    exists = localStorage.getItem(key) !== null;
  } catch {
    /* 隐私模式：当不存在，直接读（loadFavs 自己兜底） */
  }
  if (exists || market !== 'CN') return loadFavs(key);
  const legacy = loadFavs(`${prefix}:${ds}`);
  if (legacy.length) saveFavs(key, legacy);
  return legacy;
}

/** 收藏键（与 loadScopedFavs 同一构造：存取都走它，免得两边拼错） */
export function scopedFavKey(prefix: string, market: string, ds: string): string {
  return `${prefix}:${market}:${ds}`;
}
