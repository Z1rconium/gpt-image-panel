import { describe, expect, it } from 'vitest';
import { renderAgentMarkdown, safeAgentUrl, withAgentCitations } from '$lib/utils/agentMarkdown';

describe('Agent Markdown', () => {
  it('renders lists, tables, code and links, including an unfinished fence', () => {
    const html = renderAgentMarkdown('# Heading\n\n- first\n- second\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n[Source](https://example.com)\n\n```ts\nconst value = "<script>";');
    expect(html).toContain('<h1>Heading</h1>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<table>');
    expect(html).toContain('<pre><code');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it.each(['javascript:alert(1)', 'data:text/html,hello', 'vbscript:hello', 'javascript&#58;alert(1)', 'java\nscript:alert(1)', 'https://user:secret@example.com'])('refuses dangerous or credential-bearing links: %s', (url) => {
    expect(safeAgentUrl(url)).toBeNull();
    expect(renderAgentMarkdown(`[click](${url})`)).not.toContain('<a ');
  });

  it('escapes raw HTML and never loads Markdown images', () => {
    const html = renderAgentMarkdown('<img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n![image](https://tracker.example.com/pixel)');
    expect(html).not.toMatch(/<(?:img|script|iframe)\b/);
    expect(html).toContain('&lt;img');
    expect(html).toContain('&lt;script');
    expect(html).not.toContain('tracker.example.com');
  });

  it('places typed citations at Unicode positions and excludes another text block', () => {
    const sources = [{ id: 'source-1', title: 'source', url: 'https://example.com', text_block_id: 't1', start_index: 1, end_index: 6, excerpt: 'facts' }];
    expect(withAgentCitations('🌲facts.', 't1', sources)).toBe('🌲facts [1](https://example.com).');
    expect(withAgentCitations('🌲facts.', 'other', sources)).toBe('🌲facts.');
    expect(withAgentCitations('short', 't1', sources)).toBe('short');
  });

  it('renders a long streaming reply with an unfinished tail without truncation', () => {
    const text = '- A streamed paragraph with **emphasis**.\n'.repeat(2000) + '\n```js\nconst unfinished = "<script>";';
    const html = renderAgentMarkdown(text);
    expect(html.match(/<li>/g)).toHaveLength(2000);
    expect(html).toContain('unfinished');
    expect(html).not.toContain('<script>');
  });
});
