<script lang="ts">
  import { tick } from 'svelte';
  import type { AgentImageRef, AgentMessage as AgentMessageModel } from '$lib/api/types/agent';
  import { t } from '$lib/i18n';
  import AgentMessage from './AgentMessage.svelte';

  let {
    messages,
    imageRefs,
    loading = false,
    busy = false,
    hasMore = false,
    error = null,
    onLoadEarlier,
    onOpenImage,
    onRetry
  }: {
    messages: AgentMessageModel[];
    imageRefs: AgentImageRef[];
    loading?: boolean;
    busy?: boolean;
    hasMore?: boolean;
    error?: string | null;
    onLoadEarlier: () => void;
    onOpenImage: (imageId: string) => void;
    onRetry: () => void;
  } = $props();

  const NEAR_BOTTOM_PX = 96;
  let scroller: HTMLDivElement | undefined = $state();
  let stickToBottom = true;
  let lastCount = 0;

  const inputsByMessage = $derived.by(() => {
    const map = new Map<string, AgentImageRef[]>();
    for (const ref of imageRefs) {
      if (ref.role !== 'input') continue;
      const list = map.get(ref.message_id) ?? [];
      list.push(ref);
      map.set(ref.message_id, list);
    }
    return map;
  });

  // Changes whenever the visible content grows: new message, new block, or more text.
  const contentSignature = $derived.by(() => {
    const last = messages[messages.length - 1];
    if (!last) return '0';
    const size = last.blocks.reduce((total, block) => total + (block.type === 'text' ? block.text.length : 1), 0);
    return `${messages.length}:${last.id}:${last.blocks.length}:${size}:${last.status}`;
  });

  function onScroll() {
    if (!scroller) return;
    stickToBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < NEAR_BOTTOM_PX;
  }

  $effect(() => {
    contentSignature;
    const count = messages.length;
    if (count > lastCount) stickToBottom = true;
    lastCount = count;
    void tick().then(() => {
      if (scroller && stickToBottom) scroller.scrollTop = scroller.scrollHeight;
    });
  });
</script>

<!-- A scrollable region must be focusable so keyboard users can scroll it. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  bind:this={scroller}
  onscroll={onScroll}
  role="region"
  tabindex="0"
  aria-label={$t.agent.threadLabel}
  aria-busy={busy}
  class="control-focus min-h-[16rem] flex-1 overflow-y-auto overscroll-contain px-3 py-4 sm:px-4"
  style="max-height: min(62dvh, 40rem)"
  data-testid="agent-thread"
>
  {#if error}
    <div class="status-error mb-3 flex items-center justify-between gap-3 px-3 py-2 text-sm" role="alert">
      <span>{error}</span>
      <button type="button" class="ui-button-secondary" onclick={onRetry}>{$t.agent.retry}</button>
    </div>
  {/if}
  {#if hasMore}
    <div class="mb-3 flex justify-center">
      <button type="button" class="ui-button-secondary" onclick={onLoadEarlier}>{$t.agent.loadEarlier}</button>
    </div>
  {/if}
  {#if messages.length === 0}
    <p class="mx-auto max-w-md py-10 text-center text-sm text-stone-500 dark:text-zinc-500" data-testid="agent-empty">
      {loading ? $t.agent.loadingThread : $t.agent.emptyThread}
    </p>
  {:else}
    <ol class="space-y-4">
      {#each messages as message (message.id)}
        <AgentMessage {message} attachments={inputsByMessage.get(message.id) ?? []} {onOpenImage} />
      {/each}
    </ol>
  {/if}
</div>
