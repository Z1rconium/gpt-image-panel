import { Marked } from 'marked';

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character] ?? character);
}

export function safeAgentUrl(value: string): string | null {
  if (value.length > 2048 || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

// HTML tokens and images are always rendered as text. Only the compiler's
// fixed markup reaches {@html}; links use an explicit protocol allowlist.
const markdown = new Marked({
  gfm: true,
  breaks: true,
  async: false,
  renderer: {
    html({ text }) { return escapeHtml(text); },
    image({ text }) { return escapeHtml(text); },
    link({ href, tokens }) {
      const text = this.parser.parseInline(tokens);
      const url = safeAgentUrl(href);
      return url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
    }
  }
});

export function renderAgentMarkdown(text: string): string {
  try {
    return markdown.parse(text, { async: false });
  } catch {
    return `<p>${escapeHtml(text)}</p>`;
  }
}
