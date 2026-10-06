<script lang="ts">
  import { tick } from 'svelte';
  import type { AgentConversationSummary } from '$lib/api/types/agent';
  import { t } from '$lib/i18n';

  let {
    conversations,
    activeId,
    loading = false,
    error = null,
    onSelect,
    onCreate,
    onRename,
    onDelete,
    onRetry
  }: {
    conversations: AgentConversationSummary[];
    activeId: string | null;
    loading?: boolean;
    error?: string | null;
    onSelect: (id: string) => void;
    onCreate: () => void;
    onRename: (id: string, title: string) => Promise<boolean>;
    onDelete: (id: string) => void;
    onRetry: () => void;
  } = $props();

  let renamingId = $state<string | null>(null);
  let renameValue = $state('');
  let renameInput: HTMLInputElement | undefined = $state();

  function titleOf(conversation: AgentConversationSummary) {
    return conversation.title.trim() || $t.agent.untitled;
  }

  async function startRename(conversation: AgentConversationSummary) {
    renamingId = conversation.id;
    renameValue = conversation.title;
    await tick();
    renameInput?.focus();
    renameInput?.select();
  }

  async function commitRename() {
    const id = renamingId;
    const value = renameValue.trim();
    if (!id) return;
    if (!value) {
      renamingId = null;
      return;
    }
    if (await onRename(id, value)) renamingId = null;
  }
</script>

<nav class="app-surface flex h-full flex-col p-2" aria-label={$t.agent.conversations}>
  <div class="mb-2 flex items-center justify-between gap-2 px-1">
    <h3 class="text-xs font-semibold uppercase tracking-normal text-stone-500 dark:text-zinc-500">{$t.agent.conversations}</h3>
    <button type="button" class="ui-button-secondary" onclick={onCreate} data-testid="agent-new">{$t.agent.newConversation}</button>
  </div>
  {#if error}
    <div class="status-error mb-2 flex items-center justify-between gap-2 px-2 py-1.5 text-xs" role="alert">
      <span>{error}</span>
      <button type="button" class="underline" onclick={onRetry}>{$t.agent.retry}</button>
    </div>
  {/if}
  {#if loading && conversations.length === 0}
    <p class="px-2 py-3 text-xs text-stone-500 dark:text-zinc-500">{$t.agent.loadingList}</p>
  {:else if conversations.length === 0}
    <p class="px-2 py-3 text-xs text-stone-500 dark:text-zinc-500">{$t.agent.emptyList}</p>
  {:else}
    <ul class="max-h-[22rem] space-y-1 overflow-y-auto">
      {#each conversations as conversation (conversation.id)}
        {@const active = conversation.id === activeId}
        <li>
          {#if renamingId === conversation.id}
            <form
              class="flex items-center gap-1 p-1"
              onsubmit={(event) => {
                event.preventDefault();
                void commitRename();
              }}
            >
              <label class="sr-only" for="agent-rename-{conversation.id}">{$t.agent.renameLabel}</label>
              <input
                id="agent-rename-{conversation.id}"
                bind:this={renameInput}
                bind:value={renameValue}
                maxlength="200"
                class="ui-field min-w-0 flex-1"
                onkeydown={(event) => {
                  if (event.key === 'Escape') {
                    event.stopPropagation();
                    renamingId = null;
                  }
                }}
              />
              <button type="submit" class="ui-button-primary">{$t.agent.save}</button>
            </form>
          {:else}
            <div class="rounded-md {active ? 'bg-stone-100 dark:bg-zinc-800' : ''}">
              <button
                type="button"
                class="control-focus flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-sm {active
                  ? 'font-semibold text-stone-950 dark:text-zinc-100'
                  : 'text-stone-700 hover:bg-stone-50 dark:text-zinc-300 dark:hover:bg-zinc-800/60'}"
                aria-current={active ? 'true' : undefined}
                onclick={() => onSelect(conversation.id)}
              >
                <span class="min-w-0 truncate">{titleOf(conversation)}</span>
                {#if conversation.active_turn_id}
                  <span class="shrink-0 rounded-sm bg-emerald-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">{$t.agent.activeBadge}</span>
                {/if}
              </button>
              {#if active}
                <div class="flex justify-end gap-1 px-1.5 pb-1.5" role="group" aria-label={$t.agent.conversationActions(titleOf(conversation))}>
                  <button type="button" class="ui-button-secondary" onclick={() => startRename(conversation)}>{$t.agent.rename}</button>
                  <button type="button" class="ui-button-danger" onclick={() => onDelete(conversation.id)}>{$t.agent.delete}</button>
                </div>
              {/if}
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</nav>
