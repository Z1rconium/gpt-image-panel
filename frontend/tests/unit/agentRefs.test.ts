import { describe, expect, it } from 'vitest';
import type { AgentImageRef } from '$lib/api/types/agent';
import { applyMention, filterMentionOptions, findMentionQuery, splitMentions } from '$lib/utils/agentRefs';

function ref(round: number, index: number, overrides: Partial<AgentImageRef> = {}): AgentImageRef {
  return {
    ref_label: `round-${round}-${overrides.role === 'input' ? 'input' : 'image'}-${index}`,
    round_no: round,
    image_index: index,
    role: 'output',
    image_id: `g${round}${index}`,
    filename: `g${round}${index}.png`,
    job_id: null,
    item_id: null,
    prompt: '',
    mode: 'generate',
    status: 'succeeded',
    error: null,
    deleted: false,
    message_id: 'm',
    ...overrides
  };
}

describe('findMentionQuery', () => {
  it('finds an @ token at the start or after whitespace up to the caret', () => {
    expect(findMentionQuery('@ro', 3)).toEqual({ start: 0, end: 3, query: 'ro' });
    expect(findMentionQuery('draw @round-1', 13)).toEqual({ start: 5, end: 13, query: 'round-1' });
    expect(findMentionQuery('draw @', 6)).toEqual({ start: 5, end: 6, query: '' });
  });

  it('ignores emails, closed mentions and carets outside the token', () => {
    expect(findMentionQuery('me@example.com', 14)).toBeNull();
    expect(findMentionQuery('@round-1-image-1 more', 21)).toBeNull();
    expect(findMentionQuery('no mention here', 5)).toBeNull();
    expect(findMentionQuery(`@${'x'.repeat(40)}`, 41)).toBeNull();
    expect(findMentionQuery('@ab cd', 3)).toEqual({ start: 0, end: 3, query: 'ab' });
  });
});

describe('filterMentionOptions', () => {
  const refs = [
    ref(1, 1),
    ref(1, 2),
    ref(2, 1),
    ref(2, 1, { role: 'input' }),
    ref(2, 2, { deleted: true, image_id: null }),
    ref(3, 1, { status: 'failed', image_id: null }),
    ref(3, 2, { status: 'pending', image_id: null })
  ];

  it('offers only live images, newest round first', () => {
    expect(filterMentionOptions(refs, '').map((item) => item.ref_label)).toEqual([
      'round-2-image-1',
      'round-2-input-1',
      'round-1-image-1',
      'round-1-image-2'
    ]);
  });

  it('matches the canonical label and the Chinese spelling', () => {
    expect(filterMentionOptions(refs, 'round-1').map((item) => item.ref_label)).toEqual(['round-1-image-1', 'round-1-image-2']);
    expect(filterMentionOptions(refs, 'input').map((item) => item.ref_label)).toEqual(['round-2-input-1']);
    expect(filterMentionOptions(refs, '第2轮图1').map((item) => item.ref_label)).toEqual(['round-2-image-1']);
    expect(filterMentionOptions(refs, 'ROUND-2-IMAGE').map((item) => item.ref_label)).toEqual(['round-2-image-1']);
    expect(filterMentionOptions(refs, 'zzz')).toEqual([]);
  });
});

describe('applyMention', () => {
  it('replaces the query, adds a trailing space and places the caret after it', () => {
    const range = { start: 5, end: 9, query: 'rou' };
    expect(applyMention('draw @rou please', range, 'round-1-image-1')).toEqual({
      text: 'draw @round-1-image-1 please',
      caret: 22
    });
    expect(applyMention('draw @rou', range, 'round-1-image-1')).toEqual({ text: 'draw @round-1-image-1 ', caret: 22 });
  });
});

describe('splitMentions', () => {
  it('splits text around canonical and Chinese mentions', () => {
    expect(splitMentions('use @round-1-image-2 and @第3轮图1 now')).toEqual([
      { type: 'text', value: 'use ' },
      { type: 'mention', value: '@round-1-image-2', label: 'round-1-image-2' },
      { type: 'text', value: ' and ' },
      { type: 'mention', value: '@第3轮图1', label: 'round-3-image-1' },
      { type: 'text', value: ' now' }
    ]);
    expect(splitMentions('plain')).toEqual([{ type: 'text', value: 'plain' }]);
    expect(splitMentions('')).toEqual([]);
  });
});
