<script lang="ts">
  import type { AgentImageRef, AgentMessage } from '$lib/api/types/agent';
  import { t } from '$lib/i18n';
  import { groupAgentBlocks } from '$lib/utils/agentBlocks';
  import { splitMentions } from '$lib/utils/agentRefs';
  import { thumbnailUrl } from '$lib/utils/format';
  import AgentImageTask from './AgentImageTask.svelte';
  import AgentMarkdown from './AgentMarkdown.svelte';
  import AgentSources from './AgentSources.svelte';
  import { withAgentCitations } from '$lib/utils/agentMarkdown';

  let {
    message,
    attachments = [],
    onOpenImage,
    busy = false,
    onFork = async () => false
  }: {
    message: AgentMessage;
    attachments?: AgentImageRef[];
    onOpenImage: (imageId: string) => void;
    busy?: boolean;
    onFork?: (message: AgentMessage, text?: string) => Promise<boolean>;
  } = $props();

  let editing = $state(false);
  let editedText = $state('');
  async function saveEdit() {
    if (editedText.trim() && await onFork(message, editedText)) editing = false;
  }
  const sources = $derived(message.blocks.flatMap((block) => block.type === 'sources' ? block.sources : []));
  const groups = $derived(groupAgentBlocks(message.blocks));
  const parts = $derived(message.role === 'user' ? splitMentions(message.text) : []);
  const hasError = $derived(message.blocks.some((block) => block.type === 'error'));
  const statusNote = $derived(
    message.status === 'streaming'
      ? groups.length === 0
        ? $t.agent.statusStreaming
        : ''
      : message.status === 'failed'
        ? hasError
          ? ''
          : $t.agent.statusFailed
        : message.status === 'cancelled'
          ? $t.agent.statusCancelled
          : message.status === 'interrupted'
            ? $t.agent.statusInterrupted
            : groups.length === 0
              ? $t.agent.emptyReply
              : ''
  );
</script>

{#if message.role === 'user'}
  <li class="flex justify-end" data-testid="agent-message-user">
    <div class="max-w-[85%] space-y-2 rounded-xl bg-stone-100 px-3.5 py-2.5 dark:bg-zinc-800">
      <p class="sr-only">{$t.agent.you}</p>
      <p class="whitespace-pre-wrap break-words text-sm leading-6 text-stone-900 dark:text-zinc-100">
        {#each parts as part}
          {#if part.type === 'mention'}
            <code class="rounded bg-stone-200 px-1 py-0.5 font-mono text-xs dark:bg-zinc-700">{part.value}</code>
          {:else}{part.value}{/if}
        {/each}
      </p>
      <button type="button" class="ui-button-secondary text-xs" disabled={busy} onclick={() => { editedText = message.text; editing = !editing; }}>{$t.agent.editMessage}</button>
      {#if editing}
        <form class="space-y-2" onsubmit={(event) => { event.preventDefault(); void saveEdit(); }}>
          <textarea class="ui-field w-full p-2" rows="4" maxlength="8000" aria-label={$t.agent.editMessage} bind:value={editedText}></textarea>
          <button type="submit" class="ui-button-primary" disabled={busy || !editedText.trim()}>{$t.agent.sendBranch}</button>
          <button type="button" class="ui-button-secondary" onclick={() => editing = false}>{$t.agent.cancel}</button>
        </form>
      {/if}
      {#if attachments.length}
        <ul class="flex flex-wrap gap-2" aria-label={$t.agent.attachments}>
          {#each attachments as attachment (attachment.ref_label)}
            <li class="w-16">
              {#if attachment.filename && !attachment.deleted}
                <button
                  type="button"
                  class="control-focus block w-full overflow-hidden rounded-md border border-stone-300 dark:border-zinc-700"
                  aria-label={$t.agent.openImage($t.agent.attachmentLabel(attachment.path_round_no ?? attachment.round_no, attachment.image_index))}
                  onclick={() => attachment.image_id && onOpenImage(attachment.image_id)}
                >
                  <img src={thumbnailUrl(attachment.filename)} alt="" loading="lazy" class="aspect-square w-full object-cover" />
                </button>
              {:else}
                <div class="flex aspect-square w-full items-center justify-center rounded-md border border-dashed border-stone-300 p-1 text-center text-[10px] text-stone-500 dark:border-zinc-700 dark:text-zinc-400">
                  {$t.agent.imageDeleted}
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </li>
{:else}
  <li class="flex justify-start" data-testid="agent-message-assistant" data-status={message.status}>
    <div class="app-surface w-full max-w-[95%] space-y-3 p-3.5 sm:p-4">
      <p class="sr-only">{$t.agent.agent}</p>
      {#each groups as group (group.key)}
        {#if group.kind === 'text'}
          <AgentMarkdown text={withAgentCitations(group.block.text, group.block.id, sources)} />
        {:else if group.kind === 'batch'}
          {#if group.block.status === 'ready' && group.block.items.length}
            <details class="rounded-md border border-stone-200 px-3 py-2 text-xs text-stone-600 dark:border-zinc-800 dark:text-zinc-400">
              <summary class="control-focus cursor-pointer font-medium">{$t.agent.imageRequests(group.block.items.length)}</summary>
              <ol class="mt-2 list-decimal space-y-1.5 pl-4">
                {#each group.block.items as item (item.id)}
                  <li class="break-words">{item.prompt}</li>
                {/each}
              </ol>
            </details>
          {:else if group.block.status === 'invalid'}
            <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.agent.invalidRequest}</p>
          {:else if group.block.status === 'streaming'}
            <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.agent.preparing}</p>
          {/if}
        {:else if group.kind === 'images'}
          <div class="flex flex-wrap gap-3">
            {#each group.blocks as block (block.id)}
              <AgentImageTask {block} onOpen={onOpenImage} />
            {/each}
          </div>
        {:else if group.kind === 'search'}
          <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-stone-600 dark:text-zinc-400" data-testid="agent-search-status" role="status">
            <span>{$t.agent.searchStatus(group.block.status)}</span>
            {#if $t.agent.searchAction(group.block.action)}<span>{$t.agent.searchAction(group.block.action)}</span>{/if}
            {#if group.block.queries.length}<span>{group.block.queries.join('; ')}</span>{/if}
            {#if group.block.url}<span class="break-all">{group.block.url}</span>{/if}
          </div>
        {:else if group.kind === 'sources'}
          <AgentSources sources={group.block.sources} />
        {:else}
          <p role="alert" class="status-error px-3 py-2 text-sm" data-testid="agent-error-block">{group.block.message}</p>
        {/if}
      {/each}
      <button type="button" class="ui-button-secondary text-xs" disabled={busy || message.status === 'streaming'} onclick={() => void onFork(message)}>{$t.agent.regenerate}</button>
      {#if statusNote}
        <p class="text-xs text-stone-500 dark:text-zinc-500">{statusNote}</p>
      {/if}
    </div>
  </li>
{/if}
