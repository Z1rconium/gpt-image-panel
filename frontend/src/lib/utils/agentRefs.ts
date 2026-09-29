import type { AgentImageRef } from '$lib/api/types/agent';

export type MentionQuery = { start: number; end: number; query: string };
export type MentionPart = { type: 'text'; value: string } | { type: 'mention'; value: string; label: string };

const MENTION_RE = /@(round-\d+-(?:image|input)-\d+)|@第?(\d+)轮图(\d+)/g;
const MAX_QUERY_LENGTH = 32;

/** The `@query` the caret is inside of, or null when the caret is not in a mention. */
export function findMentionQuery(text: string, caret: number): MentionQuery | null {
  const position = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, position);
  const at = before.lastIndexOf('@');
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (query.length > MAX_QUERY_LENGTH || /\s/.test(query)) return null;
  return { start: at, end: position, query };
}

export function isMentionable(ref: AgentImageRef): boolean {
  return ref.status === 'succeeded' && !ref.deleted && Boolean(ref.image_id);
}

function searchKeys(ref: AgentImageRef): string[] {
  const keys = [ref.ref_label];
  if (ref.role === 'output') keys.push(`第${ref.round_no}轮图${ref.image_index}`);
  return keys;
}

/** Live images matching the typed query, newest round first. */
export function filterMentionOptions(refs: AgentImageRef[], query: string): AgentImageRef[] {
  const needle = query.trim().toLowerCase();
  return refs
    .filter(isMentionable)
    .filter((ref) => !needle || searchKeys(ref).some((key) => key.toLowerCase().includes(needle)))
    .sort((a, b) => b.round_no - a.round_no || a.image_index - b.image_index || (a.role === 'output' ? -1 : 1));
}

export function applyMention(text: string, range: MentionQuery, label: string): { text: string; caret: number } {
  const after = text.slice(range.end);
  // Reuse a space that already follows the caret instead of doubling it.
  const alreadySpaced = after.startsWith(' ');
  const insertion = `@${label}${alreadySpaced ? '' : ' '}`;
  const next = `${text.slice(0, range.start)}${insertion}${after}`;
  return { text: next, caret: range.start + insertion.length + (alreadySpaced ? 1 : 0) };
}

/** Canonical label for a mention token, whichever spelling the user typed. */
function mentionLabel(match: RegExpMatchArray): string {
  return match[1] ?? `round-${match[2]}-image-${match[3]}`;
}

export function splitMentions(text: string): MentionPart[] {
  const parts: MentionPart[] = [];
  let last = 0;
  for (const match of text.matchAll(MENTION_RE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ type: 'text', value: text.slice(last, index) });
    parts.push({ type: 'mention', value: match[0], label: mentionLabel(match) });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ type: 'text', value: text.slice(last) });
  return parts;
}
