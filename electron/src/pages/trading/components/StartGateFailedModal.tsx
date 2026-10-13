/** 启动被门禁拦下的常驻说明（T-P3-05「失败后」面）。
 *
 * 为什么不是 toast：`/start` 的 400 原先弹一次就消失，用户只看到一句
 * 「策略尚未模拟验证」，看不出**差哪一项、下一步做什么**，回头还得再点一次启动。
 * 这里把后端信封里的逐项清单（与展示面同源）原样摊开，用户关掉之前一直留在屏幕上。
 *
 * 纪律：**只展示，不判据**——`items` 全部来自后端 `shared/start_eligibility.py`，
 * 前端不新增阈值、不重算通过与否。
 */
import React from 'react';
import { Button, Modal, Tag } from 'antd';
import { AlertTriangle, ArrowRight, Check, X } from 'lucide-react';
import type { StartGateFailure } from '../../../services/realTradingService';

interface StartGateFailedModalProps {
    /** 命中门禁信封时非空；空 = 不渲染 */
    failure: StartGateFailure | null;
    onClose: () => void;
}

const MODE_LABEL: Record<string, string> = {
    REAL: '实盘',
    SIMULATION: '模拟盘',
};

/** 闸门来源：两个门的处置动作不同，必须说清是哪一个拦的。 */
const GATE_LABEL: Record<string, string> = {
    lifecycle: '生命周期状态机',
    promotion: '回测体检晋级门禁',
};

const StartGateFailedModal: React.FC<StartGateFailedModalProps> = ({ failure, onClose }) => {
    if (!failure) return null;

    const modeText = MODE_LABEL[failure.mode] || failure.mode || '当前模式';
    const gateText = GATE_LABEL[failure.gate] || '启动门禁';
    const failed = failure.items.filter((it) => !it.passed);
    const passed = failure.items.filter((it) => it.passed);
    const nextStep = failure.next_step || failed.find((it) => it.next_step)?.next_step || '';

    return (
        <Modal
            open
            centered
            onCancel={onClose}
            width={620}
            title={
                <div className="flex items-center gap-2">
                    <AlertTriangle size={18} className="text-rose-500" />
                    <span className="text-base font-bold">启动未通过：{modeText}</span>
                </div>
            }
            footer={[
                <Button key="close" type="primary" onClick={onClose}>
                    知道了
                </Button>,
            ]}
            styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
        >
            <div className="space-y-3">
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3">
                    <div className="text-sm font-bold text-rose-800">{failure.message}</div>
                    <div className="mt-1 text-xs text-rose-600">
                        拦下它的是<span className="font-bold">{gateText}</span>
                        ——两个闸门相互独立，修好一项不等于各项都过。
                    </div>
                </div>

                {failed.length > 0 ? (
                    <div className="space-y-2" data-testid="start-gate-failed-items">
                        {failed.map((item) => (
                            <div
                                key={item.key}
                                className="rounded-lg border border-rose-200 bg-white px-3 py-2"
                            >
                                <div className="flex items-center gap-2">
                                    <X size={14} className="shrink-0 text-rose-600" />
                                    <span className="text-sm font-bold text-slate-800">{item.label}</span>
                                    <Tag color="error" className="m-0">
                                        未通过
                                    </Tag>
                                    <span className="text-sm font-bold text-rose-700">{item.value}</span>
                                </div>
                                {item.reason && (
                                    <div className="mt-1 pl-6 text-xs text-rose-600">{item.reason}</div>
                                )}
                                {item.next_step && (
                                    <div className="mt-1 flex items-start gap-1.5 pl-6 text-xs text-slate-600">
                                        <ArrowRight size={12} className="mt-0.5 shrink-0 text-blue-500" />
                                        <span>
                                            <span className="font-bold">下一步：</span>
                                            {item.next_step}
                                        </span>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                ) : (
                    // 后端没给逐项清单（老版本 / 非 user_strategy）时不假装有项——只留上面的原话。
                    <div className="text-xs text-slate-500">后端未返回逐项拆解，请按上方提示处理。</div>
                )}

                {passed.length > 0 && (
                    <div
                        className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                        data-testid="start-gate-passed-items"
                    >
                        <div className="mb-1 text-xs font-bold text-slate-500">
                            已通过（{passed.length} 项）
                        </div>
                        <div className="flex flex-col gap-1">
                            {passed.map((item) => (
                                <div key={item.key} className="flex items-center gap-2 text-xs text-slate-500">
                                    <Check size={12} className="shrink-0 text-emerald-600" />
                                    <span className="font-bold text-slate-600">{item.label}</span>
                                    <span>{item.value}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {nextStep && (
                    <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
                        <span className="font-bold">唯一入口：</span>
                        {nextStep}
                    </div>
                )}
            </div>
        </Modal>
    );
};

export default StartGateFailedModal;
