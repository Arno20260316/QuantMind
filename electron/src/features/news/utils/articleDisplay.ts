/**
 * 资讯列表展示层小工具 —— 只决定「怎么显示」，不改数据语义。
 *
 * 单开文件的理由：列表摘要行在 NewsPanel 里，而「占位摘要」是上游 feed 的形态
 * 问题（Huntly 抓到的部分源只给「点击标题查看详细内容」这类占位串），把它沉成
 * 纯函数才好钉测试；以后移动端卡片等其它展示面要复用也不会再抄一份判据。
 */

/** 上游常见占位摘要：整串等值即命中（不做子串匹配——真摘要里出现这几个字眼不算）。
 *  真实形态多带媒体名前缀（2026-10-15 实测财联社源整页 50 条全是
 *  「[财联社] 点击标题查看详细内容」），故前缀 `[媒体名]` 可选。 */
const PLACEHOLDER_SUMMARY_RE =
  /^(?:\[[^\]]{1,20}\]\s*)?(?:点击标题查看详细内容|点击查看详细内容|查看全文|点击查看全文|阅读全文|阅读更多)$/;

/**
 * 摘要零信息量判据（含空摘要）：这类「摘要」显示出来只会让列表更嘈杂。
 *
 * @param summary 文章摘要（可空）
 * @param title   标题 —— 摘要与标题完全相同 = 复读机，同样不显示
 */
export function isPlaceholderSummary(
  summary?: string | null,
  title?: string | null,
): boolean {
  const s = (summary ?? '').trim();
  if (s === '') return true;
  if (title != null && s === title.trim()) return true;
  return PLACEHOLDER_SUMMARY_RE.test(s);
}

/** 粗剥 HTML 标签取纯文本——只为「正文位占位判据」服务（整串等值；真正文剥完带出多余
 *  空白/实体残字也不影响判据）。不用于展示：要渲染还是走 sanitizeHtml。 */
export function htmlToPlainText(html?: string | null): string {
  return (html ?? '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}
