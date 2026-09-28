<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import { t } from '$lib/i18n';
  import { confirmStore } from '$lib/stores/confirm';
  import { galleryActivityStore, galleryStore } from '$lib/stores/gallery';
  import { galleryCollectionsStore, moveCollectionId } from '$lib/stores/galleryCollections';
  import { uiStore } from '$lib/stores/ui';
  import ArrowDown from 'lucide-svelte/icons/arrow-down';
  import ArrowUp from 'lucide-svelte/icons/arrow-up';
  import Download from 'lucide-svelte/icons/download';
  import GripVertical from 'lucide-svelte/icons/grip-vertical';
  import Pencil from 'lucide-svelte/icons/pencil';
  import Trash2 from 'lucide-svelte/icons/trash-2';

  interface Props {
    open?: boolean;
    onClose?: () => void;
  }

  let { open = false, onClose = () => {} }: Props = $props();

  const collections = $derived($galleryCollectionsStore.collections);
  let newName = $state('');
  let renamingId = $state('');
  let renameValue = $state('');
  let dragIndex = $state<number | null>(null);
  let overIndex = $state<number | null>(null);
  let busy = $state(false);

  $effect(() => {
    if (open) {
      newName = '';
      renamingId = '';
      void galleryCollectionsStore.load(true).catch(showError);
    }
  });

  function showError(error: unknown) {
    uiStore.showToast(error instanceof Error ? error.message : $t.messages.requestFailed, 'error');
  }

  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    busy = true;
    try {
      await action();
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
    }
  }

  function createCollection() {
    const name = newName.trim();
    if (!name) return;
    void run(async () => {
      await galleryCollectionsStore.create(name);
      newName = '';
    });
  }

  function startRename(id: string, name: string) {
    renamingId = id;
    renameValue = name;
  }

  function commitRename() {
    const id = renamingId;
    const name = renameValue.trim();
    const current = collections.find((item) => item.id === id);
    renamingId = '';
    if (!id || !name || name === current?.name) return;
    void run(() => galleryCollectionsStore.rename(id, name));
  }

  function move(fromIndex: number, toIndex: number) {
    const currentIds = collections.map((item) => item.id);
    const ids = moveCollectionId(currentIds, fromIndex, toIndex);
    if (ids === currentIds) return;
    void run(() => galleryCollectionsStore.reorder(ids));
  }

  async function deleteCollection(id: string, name: string) {
    const confirmed = await confirmStore.confirm({
      title: $t.collections.deleteTitle,
      message: $t.collections.deleteMessage(name),
      details: [$t.collections.deleteDetail],
      confirmLabel: $t.common.delete,
      cancelLabel: $t.confirm.cancel,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
    void run(() => galleryCollectionsStore.remove(id));
  }

  function downloadCollection(id: string, imageCount: number) {
    void run(() => galleryStore.exportCollection(id, imageCount, (message) => uiStore.showToast(message)));
  }

  function handleDrop(event: DragEvent, index: number) {
    event.preventDefault();
    const from = dragIndex;
    dragIndex = null;
    overIndex = null;
    if (from !== null) move(from, index);
  }
</script>

<Overlay {open} {onClose} closeLabel={$t.common.close} labelledBy="collections-dialog-title" z={80} maxWidthClass="max-w-xl" panelClass="p-5" backdropBlur>
  <div class="mb-4 flex items-start justify-between gap-3">
    <div>
      <h2 id="collections-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.collections.manageTitle}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.collections.manageSubtitle}</p>
    </div>
    <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.common.close} onclick={onClose}>x</button>
  </div>

  <form
    class="mb-4 flex gap-2"
    onsubmit={(event) => {
      event.preventDefault();
      createCollection();
    }}
  >
    <label for="new-collection-name" class="sr-only">{$t.collections.newName}</label>
    <input
      id="new-collection-name"
      bind:value={newName}
      maxlength="60"
      autocomplete="off"
      placeholder={$t.collections.newName}
      class="control-focus min-w-0 flex-1 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
    />
    <button type="submit" class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50" disabled={busy || !newName.trim()}>{$t.collections.create}</button>
  </form>

  {#if !collections.length}
    <p class="rounded-lg border border-dashed border-stone-300 px-3 py-6 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
      {$galleryCollectionsStore.loading ? $t.gallery.loading : $t.collections.empty}
    </p>
  {:else}
    <ul class="max-h-[55dvh] space-y-1.5 overflow-y-auto pr-1" aria-label={$t.collections.listLabel}>
      {#each collections as collection, index (collection.id)}
        <li
          class={`flex items-center gap-2 rounded-lg border px-2 py-2 ${overIndex === index && dragIndex !== index ? 'border-emerald-500 bg-emerald-500/5' : 'border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-950/60'} ${dragIndex === index ? 'opacity-50' : ''}`}
          draggable={renamingId !== collection.id}
          data-collection-row={collection.name}
          ondragstart={(event) => {
            dragIndex = index;
            event.dataTransfer?.setData('text/plain', collection.id);
            if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
          }}
          ondragover={(event) => {
            event.preventDefault();
            overIndex = index;
          }}
          ondragleave={() => {
            if (overIndex === index) overIndex = null;
          }}
          ondrop={(event) => handleDrop(event, index)}
          ondragend={() => {
            dragIndex = null;
            overIndex = null;
          }}
        >
          <span class="cursor-grab text-stone-400 dark:text-zinc-500" aria-hidden="true"><GripVertical class="h-4 w-4" /></span>
          <div class="min-w-0 flex-1">
            {#if renamingId === collection.id}
              <input
                bind:value={renameValue}
                maxlength="60"
                aria-label={$t.collections.renameLabel(collection.name)}
                class="control-focus w-full rounded-md border border-emerald-500 bg-stone-50 px-2 py-1 text-sm text-stone-900 dark:bg-zinc-950 dark:text-zinc-100"
                onkeydown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    commitRename();
                  } else if (event.key === 'Escape') {
                    event.stopPropagation();
                    renamingId = '';
                  }
                }}
                onblur={commitRename}
              />
            {:else}
              <p class="truncate text-sm font-medium text-stone-800 dark:text-zinc-200">{collection.name}</p>
            {/if}
            <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.collections.imageCount(collection.image_count)}</p>
          </div>
          <label class={`flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-xs ${collection.is_default ? 'text-emerald-700 dark:text-emerald-300' : 'text-stone-500 dark:text-zinc-400'}`}>
            <input
              type="radio"
              name="default-collection"
              class="accent-emerald-500"
              checked={collection.is_default}
              disabled={busy}
              onchange={() => void run(() => galleryCollectionsStore.setDefault(collection.id, true))}
            />
            {$t.collections.default}
          </label>
          <div class="flex shrink-0 items-center gap-0.5">
            <button type="button" class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label={$t.collections.moveUp(collection.name)} title={$t.collections.moveUp(collection.name)} disabled={busy || index === 0} onclick={() => move(index, index - 1)}><ArrowUp aria-hidden="true" /></button>
            <button type="button" class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label={$t.collections.moveDown(collection.name)} title={$t.collections.moveDown(collection.name)} disabled={busy || index === collections.length - 1} onclick={() => move(index, index + 1)}><ArrowDown aria-hidden="true" /></button>
            <button type="button" class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label={$t.collections.renameLabel(collection.name)} title={$t.collections.renameLabel(collection.name)} onclick={() => startRename(collection.id, collection.name)}><Pencil aria-hidden="true" /></button>
            <button type="button" class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label={$t.collections.downloadZip(collection.name)} title={$t.collections.downloadZip(collection.name)} disabled={busy || !collection.image_count || Boolean($galleryActivityStore.operationStatus)} onclick={() => downloadCollection(collection.id, collection.image_count)}><Download aria-hidden="true" /></button>
            <button type="button" class="gallery-icon-action control-focus border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300" aria-label={$t.collections.deleteLabel(collection.name)} title={$t.collections.deleteLabel(collection.name)} disabled={busy} onclick={() => void deleteCollection(collection.id, collection.name)}><Trash2 aria-hidden="true" /></button>
          </div>
        </li>
      {/each}
    </ul>
    <p class="mt-3 text-xs text-stone-500 dark:text-zinc-500">{$t.collections.reorderHint}</p>
  {/if}
</Overlay>
