import { describe, expect, it } from 'vitest';
import { escapeHtml, htmlToText, sanitizeHtml } from './html.ts';

describe('escapeHtml', () => {
  it('escapes the five HTML-significant characters', () => {
    expect(escapeHtml(`<a href="x">'&'</a>`)).toBe('&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
  });
});

describe('sanitizeHtml', () => {
  it('keeps simple formatting and drops every attribute', () => {
    expect(
      sanitizeHtml('<p style="text-align: center;" data-pm-slice="1"><span>Hi</span> <strong>there</strong></p>'),
    ).toBe('<p>Hi <strong>there</strong></p>');
  });

  it('drops scripts, styles, stylesheets, forms and their content', () => {
    const html = sanitizeHtml(
      '<link rel="stylesheet" href="https://cdn.shopify.com/x.css"><meta charset="utf-8"><style>p{}</style><p>ok</p>' +
        '<script src="https://cdn.shopify.com/x.js"></script><form><input></form><iframe src="x"></iframe>',
    );
    expect(html).toBe('<p>ok</p>');
  });

  it('removes empty paragraphs and headings, including non-breaking spaces', () => {
    expect(sanitizeHtml('<p>ROSART</p><h1></h1><p style="x">&nbsp;</p>')).toBe('<p>ROSART</p>');
  });

  it('trims whitespace at the edges of block elements', () => {
    expect(sanitizeHtml('<p>&nbsp;Text with a trailing space. </p><h2> Title </h2>')).toBe(
      '<p>Text with a trailing space.</p><h2>Title</h2>',
    );
  });

  it('re-escapes decoded entities so text stays text', () => {
    expect(sanitizeHtml('<p>MinervaBelle &amp; Co. &lt;b&gt;</p>')).toBe('<p>MinervaBelle &amp; Co. &lt;b&gt;</p>');
  });

  it('normalises b and i to strong and em, and keeps line breaks', () => {
    expect(sanitizeHtml('<p><b>a</b><br/><i>b</i></p>')).toBe('<p><strong>a</strong><br><em>b</em></p>');
  });

  it('resolves link targets and drops links with no safe target', () => {
    const resolveLink = (href: string) => (href === '/pages/contact' ? '/contact/' : undefined);
    expect(
      sanitizeHtml('<p><a href="/pages/contact" onclick="x()">us</a> <a href="javascript:x()">bad</a></p>', {
        resolveLink,
      }),
    ).toBe('<p><a href="/contact/">us</a> bad</p>');
  });

  it('marks external links with rel noopener', () => {
    expect(
      sanitizeHtml('<a href="https://orizen.com.au/" target="_blank">ORIZEN</a>', { resolveLink: (h) => h }),
    ).toBe('<a href="https://orizen.com.au/" rel="noopener noreferrer">ORIZEN</a>');
  });

  it('drops images, so no store CDN reference survives', () => {
    expect(sanitizeHtml('<p>a<img src="https://cdn.shopify.com/x.png">b</p>')).toBe('<p>ab</p>');
  });
});

describe('htmlToText', () => {
  it('returns decoded, whitespace-collapsed text', () => {
    expect(htmlToText('&nbsp;From Bulgaria&#39;s Valley of Roses,\n <em>with</em> Care&nbsp;')).toBe(
      "From Bulgaria's Valley of Roses, with Care",
    );
  });

  it('separates block elements with a space', () => {
    expect(htmlToText('<p>One</p><p>Two<br>Three</p>')).toBe('One Two Three');
  });
});
