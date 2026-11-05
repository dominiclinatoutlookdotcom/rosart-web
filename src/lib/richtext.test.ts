import { describe, expect, it } from 'vitest';
import { metafieldToHtml, multilineToHtml, richTextToHtml } from './richtext.ts';

const doc = (...children: unknown[]) => JSON.stringify({ type: 'root', children });
const text = (value: string, extra: Record<string, unknown> = {}) => ({ type: 'text', value, ...extra });

describe('richTextToHtml', () => {
  it('renders paragraphs from the JSON string Shopify stores', () => {
    expect(richTextToHtml(doc({ type: 'paragraph', children: [text('For external use only.')] }))).toBe(
      '<p>For external use only.</p>',
    );
  });

  it('escapes text so markup in content cannot inject HTML', () => {
    const html = richTextToHtml(doc({ type: 'paragraph', children: [text('<script>alert("x")</script> & co')] }));
    expect(html).toBe('<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; co</p>');
  });

  it('renders bold and italic text', () => {
    const html = richTextToHtml(
      doc({
        type: 'paragraph',
        children: [text('a', { bold: true }), text('b', { italic: true }), text('c', { bold: true, italic: true })],
      }),
    );
    expect(html).toBe('<p><strong>a</strong><em>b</em><strong><em>c</em></strong></p>');
  });

  it('renders headings no higher than the minimum level, and lists', () => {
    const html = richTextToHtml(
      doc(
        { type: 'heading', level: 1, children: [text('Title')] },
        { type: 'heading', level: 5, children: [text('Small')] },
        { type: 'list', listType: 'unordered', children: [{ type: 'list-item', children: [text('one')] }] },
        { type: 'list', listType: 'ordered', children: [{ type: 'list-item', children: [text('two')] }] },
      ),
    );
    expect(html).toBe('<h3>Title</h3><h5>Small</h5><ul><li>one</li></ul><ol><li>two</li></ol>');
  });

  it('resolves links through the given resolver and escapes their attributes', () => {
    const html = richTextToHtml(
      doc({
        type: 'paragraph',
        children: [{ type: 'link', url: '/pages/our-story', title: 'Our "story"', children: [text('story')] }],
      }),
      { resolveLink: (href) => (href === '/pages/our-story' ? '/our-story/' : undefined) },
    );
    expect(html).toBe('<p><a href="/our-story/" title="Our &quot;story&quot;">story</a></p>');
  });

  it('keeps the text of a link that has no safe target', () => {
    const html = richTextToHtml(
      doc({ type: 'paragraph', children: [{ type: 'link', url: 'javascript:alert(1)', children: [text('click')] }] }),
    );
    expect(html).toBe('<p>click</p>');
  });

  it('renders the children of node types it does not know', () => {
    expect(richTextToHtml(doc({ type: 'mystery', children: [text('kept')] }))).toBe('kept');
  });

  it('refuses a value that is not rich text JSON', () => {
    expect(() => richTextToHtml('not json')).toThrow(/rich text/i);
  });
});

describe('multilineToHtml', () => {
  it('makes a paragraph per blank-line block and a line break per single newline', () => {
    expect(multilineToHtml('One\r\nstill one\n\nTwo')).toBe('<p>One<br>still one</p><p>Two</p>');
  });

  it('escapes the text', () => {
    expect(multilineToHtml('a < b & "c"')).toBe('<p>a &lt; b &amp; &quot;c&quot;</p>');
  });

  it('returns an empty string for blank text', () => {
    expect(multilineToHtml('  \n\n ')).toBe('');
  });
});

describe('metafieldToHtml', () => {
  it('picks the renderer from the metafield type', () => {
    expect(metafieldToHtml({ type: 'rich_text_field', value: doc({ type: 'paragraph', children: [text('x')] }) })).toBe(
      '<p>x</p>',
    );
    expect(metafieldToHtml({ type: 'multi_line_text_field', value: 'a\n\nb' })).toBe('<p>a</p><p>b</p>');
    expect(metafieldToHtml({ type: 'single_line_text_field', value: 'a' })).toBe('<p>a</p>');
  });

  it('refuses metafield types it cannot render', () => {
    expect(() => metafieldToHtml({ type: 'file_reference', value: 'gid://x' })).toThrow(/file_reference/);
  });
});
