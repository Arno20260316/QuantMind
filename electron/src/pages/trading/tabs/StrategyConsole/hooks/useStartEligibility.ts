import { useEffect, useState } from 'react';
import type { StrategyFile } from '../../../../../types/backtest/strategy';
import {
    getStrategyHealth,
} from '../../../../../features/eval-center/services/evalCenterService';
import type { StartEligibilityItem } from '../../../../../features/eval-center/types/evalCenter';

/** 控制台「启动资格」条（T-P3-05 展示面）。
 *
 * 背景：策略不能启动原先只在 `/start` 抛一个 400，前端弹一次就没了，用户看不出
 * **差哪一项、下一步做什么**。本 hook 在**点启动之前**把逐项清单拿到手。
 *
 * 三条纪律：
 * 1. **判据不在这里**：清单来自后端 `shared/start_eligibility.py`（与 `/start`
 *    同一份实现）。本 hook 只负责取数与「该不该参加校验」。
 * 2. **不替执行点拦人**：未选策略 / 内置模板 / 读取失败一律 `uncheckedReason` 放行
 *    ——展示面读不到 ≠ 不能启动，真正的闸门在执行点，误禁按钮才是回归。
 * 3. 未通过项的 `reason` 与 `next_step` 必须原样透出，供 UI 给出**可见理由**。
 */
export interface StartEligibilityState {
    loading: boolean;
    /** 只在「读到清单且清单不允许」时为 false；未校验/读取失败都保持 true */
    allowed: boolean;
    items: StartEligibilityItem[];
    /** 首个未通过项的下一步；全通过时为空串 */
    nextStep: string;
    /** 未参加校验的原因（未选策略 / 内置模板 / 读取失败）；空串 = 清单有效 */
    uncheckedReason: string;
}

const UNCHECKED: Omit<StartEligibilityState, 'uncheckedReason'> = {
    loading: false,
    allowed: true,
    items: [],
    nextStep: '',
};

/** 只有数字 id 才会命中用户策略；模板（`sys_*`）与文件上传不吃门禁。 */
const isUserStrategyId = (id: string | undefined): boolean =>
    !!id && /^\d+$/.test(String(id).trim());

export function useStartEligibility(
    strategy: StrategyFile | undefined,
    mode: 'SIMULATION' | 'REAL',
    enabled = true,
    refreshKey = 0,
): StartEligibilityState {
    const [state, setState] = useState<StartEligibilityState>({
        ...UNCHECKED,
        uncheckedReason: '未选择策略',
    });

    const strategyId = strategy?.id;
    const isTemplate = !!strategy?.is_system;
    // 模板策略的 id 就是 `sys_*`，与上面的数字判据同向；两个条件都留着是为了
    // 后端若把模板 id 改成数字，也不会误拿模板去查资格清单。
    const checkable = enabled && !isTemplate && isUserStrategyId(strategyId);

    useEffect(() => {
        if (!checkable) {
            setState({
                ...UNCHECKED,
                uncheckedReason: !enabled
                    ? '当前模式不校验启动资格'
                    : isTemplate
                      ? '内置模板策略不吃门禁'
                      : '未选择策略',
            });
            return;
        }

        let cancelled = false;
        setState((prev) => ({ ...prev, loading: true }));

        (async () => {
            try {
                // limit 只影响 `latest`/`history`，资格清单与它无关 —— 取 1 即可。
                const resp = await getStrategyHealth(String(strategyId), 1, mode);
                if (cancelled) return;
                const eligibility = resp?.data?.eligibility?.[mode];
                if (!eligibility) {
                    // 后端没给这一档（老版本 / 非 user_strategy）→ 不拦，说明原因
                    setState({ ...UNCHECKED, uncheckedReason: '接口未返回该模式的资格清单' });
                    return;
                }
                setState({
                    loading: false,
                    allowed: eligibility.allowed !== false,
                    items: Array.isArray(eligibility.items) ? eligibility.items : [],
                    nextStep: eligibility.next_step || '',
                    uncheckedReason: '',
                });
            } catch {
                if (cancelled) return;
                // 读失败不拦：真正的闸门在执行点，点启动仍会得到逐项 400
                setState({ ...UNCHECKED, uncheckedReason: '资格清单读取失败（启动时仍会校验）' });
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [checkable, strategyId, mode, enabled, isTemplate, refreshKey]);

    return state;
}
