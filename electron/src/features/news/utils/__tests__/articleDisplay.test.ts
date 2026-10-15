/**
 * 资讯列表摘要行显示判据（2026-10-15 桌面排版批 D3）。
 *
 * 背景：部分 RSS 源只在摘要位放占位串（「点击标题查看详细内容」），列表里
 * 一行行占位比没有摘要更嘈杂；还有的摘要整串复读标题。展示层按本判据过滤。
 */

import { describe, expect, it } from 'vitest';

import { htmlToPlainText, isPlaceholderSummary } from '../articleDisplay';

describe('isPlaceholderSummary', () => {
  it('缺失或空摘要：不占行', () => {
    expect(isPlaceholderSummary(undefined)).toBe(true);
    expect(isPlaceholderSummary(null)).toBe(true);
    expect(isPlaceholderSummary('')).toBe(true);
    expect(isPlaceholderSummary('   ')).toBe(true);
  });

  it('摘要整串复读标题：不占行', () => {
    const title = '央行开展 5000 亿元 MLF 操作';
    expect(isPlaceholderSummary(title, title)).toBe(true);
    expect(isPlaceholderSummary(`  ${title}  `, title)).toBe(true);
  });

  it.each([
    '点击标题查看详细内容',
    '点击查看详细内容',
    '查看全文',
    '点击查看全文',
    '阅读全文',
    '阅读更多',
  ])('上游占位串 %j：不占行', (s) => {
    expect(isPlaceholderSummary(s, '某标题')).toBe(true);
  });

  it.each([
    '[财联社] 点击标题查看详细内容', // 2026-10-15 实测真实形态（财联社源整页都是它）
    '[证券时报] 阅读全文',
    '[公告] 查看全文',
  ])('带媒体名前缀的占位串 %j：同样不占行', (s) => {
    expect(isPlaceholderSummary(s, '某标题')).toBe(true);
  });

  it('占位串带前缀但后面还有正文：不误杀', () => {
    expect(isPlaceholderSummary('[财联社] 点击查看全文了解公司应对措施详情。', '某标题')).toBe(false);
  });

  it('占位串判定是整串等值：正文里顺带出现这些字眼不误杀', () => {
    expect(isPlaceholderSummary('点击查看全文了解公司应对措施详情。', '某标题')).toBe(false);
    expect(isPlaceholderSummary('阅读全文约需 3 分钟，核心结论如下。', '某标题')).toBe(false);
  });

  it('正常摘要：放行', () => {
    expect(isPlaceholderSummary('北向资金净买入 42 亿元，白酒板块领涨。', '北向资金动向')).toBe(false);
  });
});

describe('htmlToPlainText', () => {
  it('剥标签取纯文本，供正文位占位判据用', () => {
    expect(htmlToPlainText('<p>[财联社] 点击标题查看详细内容</p>')).toBe('[财联社] 点击标题查看详细内容');
    expect(htmlToPlainText(undefined)).toBe('');
    expect(htmlToPlainText('<div><span>真</span><span>正文</span></div>')).toBe('真 正文');
  });

  it('与占位判据串联：HTML 形态的占位正文同样命中', () => {
    // 2026-10-15 实测：财联社源 content_html 就长这样（16 字符裸占位，无标签也命中）
    expect(isPlaceholderSummary(htmlToPlainText('[财联社] 点击标题查看详细内容'), '某标题')).toBe(true);
    expect(isPlaceholderSummary(htmlToPlainText('<p>北向资金净买入 42 亿元，白酒板块领涨。</p>'), '北向资金动向')).toBe(false);
  });
});
