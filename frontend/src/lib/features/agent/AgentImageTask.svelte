<script lang="ts">
  import type { AgentImageTaskBlock } from '$lib/api/types/agent';
  import { t } from '$lib/i18n';
  import { imageUrl, thumbnailUrl } from '$lib/utils/format';

  let { block, onOpen }: { block: AgentImageTaskBlock; onOpen: (imageId: string) => void } = $props();

  let thumbFailed = $state(false);

  const label = $derived($t.agent.imageLabel(block.round_no, block.image_index));
  const deleted = $derived(block.status === 'succeeded' && (Boolean(block.deleted) || !block.image_id));
  const ready = $derived(block.status === 'succeeded' && !deleted && Boolean(block.image_id && block.filename));
  const stageLabel = $derived(($t.stages as Record<string, string>)[block.stage] ?? '');
  const statusLabel = $derived(
    block.status === 'succeeded'
      ? deleted
        ? $t.agent.imageDeleted
        : $t.agent.imageSucceeded
      : block.status === 'failed'
        ? $t.agent.imageFailed
        : block.status === 'cancelled'
          ? $t.agent.imageCancelled
          : stageLabel || $t.agent.imageWaiting
  );
  const source = $derived(
    block.filename ? (thumbFailed ? imageUrl(block.filename) : thumbnailUrl(block.filename)) : ''
  );
</script>

<figure class="w-36 sm:w-40" data-testid="agent-image-task" data-status={block.status}>
  {#if ready && block.image_id}
    <button
      type="button"
      class="control-focus app-well block w-full overflow-hidden rounded-lg border border-stone-200 dark:border-zinc-800"
      aria-label={$t.agent.openImage(label)}
      onclick={() => onOpen(block.image_id as string)}
    >
      <img
        src={source}
        alt={block.prompt.slice(0, 140)}
        loading="lazy"
        class="aspect-square w-full object-cover"
        onerror={() => (thumbFailed = true)}
      />
    </button>
  {:else}
    <div
      class="app-well flex aspect-square w-full items-center justify-center rounded-lg border border-dashed border-stone-300 p-2 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-400"
    >
      {statusLabel}
    </div>
  {/if}
  <figcaption class="mt-1.5 space-y-0.5 text-xs">
    <span class="block font-medium text-stone-800 dark:text-zinc-200">{label}</span>
    {#if block.mode === 'edit' && block.source_refs.length}
      <span class="block truncate text-stone-500 dark:text-zinc-500" title={block.source_refs.join(', ')}>
        {$t.agent.editsFrom(block.source_refs.join(', '))}
      </span>
    {/if}
    {#if ready}
      <span class="sr-only">{statusLabel}</span>
    {:else if block.status === 'failed' && block.error}
      <span class="block text-red-700 dark:text-red-200" data-testid="agent-image-error">{block.error}</span>
    {/if}
  </figcaption>
</figure>
