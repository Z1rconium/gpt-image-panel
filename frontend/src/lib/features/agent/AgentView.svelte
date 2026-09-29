<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import { apiFetch } from '$lib/api/client';
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import { agentStore } from '$lib/stores/agent';
  import { confirmStore } from '$lib/stores/confirm';
  import { galleryStore } from '$lib/stores/gallery';
  import { uiStore } from '$lib/stores/ui';
  import AgentComposer from './AgentComposer.svelte';
  import AgentConversationList from './AgentConversationList.svelte';
  import AgentThread from './AgentThread.svelte';

  let {
    conversationId = null,
    onConversationChange = () => {},
    onImagesChanged = () => {},
    onOpenGalleryImage
  }: {
    conversationId?: string | null;
    onConversationChange?: (id: string | null) => void;
    onImagesChanged?: () => void;
    onOpenGalleryImage: (entry: GalleryEntry) => void;
  } = $props();

  let showList = $state(false);
  let appliedProp: string | null | undefined = undefined;

  const galleryImages = $derived($galleryStore.gallery?.images ?? []);
  const gallerySelectedIds = $derived([...$galleryStore.selectedIds]);
  const activeTitle = $derived(
    $agentStore.conversations.find((item) => item.id === $agentStore.activeId)?.title.trim() || ''
  );

  onMount(() => {
    agentStore.setImageSyncHandler(() => onImagesChanged());
    const removeVisibility = agentStore.installVisibilityHandling();
    void agentStore.loadList();
    return removeVisibility;
  });

  onDestroy(() => agentStore.dispose());

  // The URL owns which conversation is open; follow it, and report changes back.
  // On first mount with no URL conversation, resume the one the store still holds.
  let initialized = false;
  $effect(() => {
    const id = conversationId;
    if (id === appliedProp) return;
    const first = !initialized;
    initialized = true;
    appliedProp = id;
    const current = untrack(() => $agentStore.activeId);
    const target = first && id === null ? current : id;
    if (first || target !== current) void agentStore.open(target);
  });

  $effect(() => {
    const id = $agentStore.activeId;
    if (id === untrack(() => conversationId)) return;
    appliedProp = id;
    onConversationChange(id);
  });

  async function createConversation() {
    showList = false;
    await agentStore.open(null);
  }

  async function selectConversation(id: string) {
    showList = false;
    await agentStore.open(id);
  }

  async function deleteConversation(id: string) {
    const confirmed = await confirmStore.confirm({
      title: $t.agent.deleteTitle,
      message: $t.agent.deleteMessage,
      confirmLabel: $t.agent.deleteConfirm,
      cancelLabel: $t.agent.cancel,
      closeLabel: $t.agent.cancel,
      variant: 'danger'
    });
    if (confirmed) await agentStore.remove(id);
  }

  async function openImage(imageId: string) {
    try {
      const entry = await apiFetch<GalleryEntry>(`/api/gallery/${encodeURIComponent(imageId)}`, {}, 'opening the image');
      onOpenGalleryImage(entry);
    } catch {
      uiStore.showToast($t.agent.errorOpenImage, 'error');
    }
  }
</script>

<section aria-labelledby="agent-heading" class="space-y-3" data-testid="agent-view">
  <div class="flex items-center justify-between gap-3">
    <h2 id="agent-heading" class="min-w-0 truncate text-sm font-semibold text-stone-950 dark:text-zinc-100">
      {$t.agent.title}{activeTitle ? ` · ${activeTitle}` : ''}
    </h2>
    <button
      type="button"
      class="ui-button-secondary lg:hidden"
      aria-expanded={showList}
      aria-controls="agent-conversations"
      onclick={() => (showList = !showList)}
    >
      {showList ? $t.agent.hideConversations : $t.agent.showConversations}
    </button>
  </div>

  <div class="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
    <div id="agent-conversations" class={showList ? 'block' : 'hidden lg:block'}>
      <AgentConversationList
        conversations={$agentStore.conversations}
        activeId={$agentStore.activeId}
        loading={$agentStore.listLoading}
        error={$agentStore.listError}
        onSelect={selectConversation}
        onCreate={createConversation}
        onRename={agentStore.rename}
        onDelete={deleteConversation}
        onRetry={() => void agentStore.loadList()}
      />
    </div>

    <div class="app-surface flex min-w-0 flex-col overflow-hidden">
      <AgentThread
        messages={$agentStore.messages}
        imageRefs={$agentStore.imageRefs}
        loading={$agentStore.detailLoading}
        busy={Boolean($agentStore.activeTurnId)}
        hasMore={$agentStore.hasMore}
        error={$agentStore.detailError}
        onLoadEarlier={() => void agentStore.loadEarlier()}
        onOpenImage={openImage}
        onRetry={() => void agentStore.open($agentStore.activeId)}
      />
      <AgentComposer
        imageRefs={$agentStore.imageRefs}
        {galleryImages}
        {gallerySelectedIds}
        turnActive={Boolean($agentStore.activeTurnId)}
        sending={$agentStore.sending}
        cancelling={$agentStore.cancelling}
        error={$agentStore.actionError}
        onSend={agentStore.send}
        onStop={() => void agentStore.cancel()}
        onDismissError={agentStore.clearActionError}
      />
    </div>
  </div>

  <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{$agentStore.announcement}</p>
</section>
