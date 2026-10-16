/** 聪明钱 · 标签面懒取数 hook（面板 v2.1：战绩/事件研究/证据台首次切入才取数）。
 *
 * 状态机：loaded 只表示「初次取数已结算」——成功或 404 未构建（由组件自带
 * 「尚未生成」空态承担，不报错）；失败置 error 且不置 loaded（效果闸由此挡住
 * 自动重试风暴，重试只走显式重试/刷新）。seq 防串（刷新与旧响应交错的过期丢弃）。
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import { SmartMoneyError } from '../../services/smartMoneyService';

export interface FaceData<T> {
  data: T | null;
  loading: boolean;
  /** 初次取数已结算（成功或 404 空态）；失败不置位 */
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;
  /** 清空重来（刷新：清数据 + 复位闸，激活面随效果自动重取） */
  reset: () => void;
}

interface FaceState<T> {
  data: T | null;
  loading: boolean;
  loaded: boolean;
  error: string | null;
}

const INIT = { data: null, loading: false, loaded: false, error: null };

export function useFaceData<T>(
  fetchOnce: () => Promise<T>,
  fallbackMsg: string,
): FaceData<T> {
  const [state, setState] = useState<FaceState<T>>(INIT);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const my = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fetchOnce();
      if (my !== seq.current) return;
      setState({ data, loading: false, loaded: true, error: null });
    } catch (e) {
      if (my !== seq.current) return;
      if (e instanceof SmartMoneyError && e.notBuilt) {
        // 404：未构建不是错误——空态文案由面组件承担（绝不白屏/红条误导）
        setState({ data: null, loading: false, loaded: true, error: null });
        return;
      }
      setState((s) => ({
        ...s,
        loading: false,
        error: e instanceof Error ? e.message : fallbackMsg,
      }));
    }
  }, [fetchOnce, fallbackMsg]);

  const reset = useCallback(() => {
    seq.current += 1;
    setState(INIT);
  }, []);

  return useMemo(
    () => ({ ...state, load, reset }),
    [state, load, reset],
  );
}
