<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import type { GenerateJobStatus } from '$lib/api/types/jobs';
  import { t } from '$lib/i18n';
  import { previewStore, type PreviewCompareSource, type PreviewSlot } from '$lib/stores/preview';
  import { displayImageSize, downloadUrl, formatLocalTime, isGifFilename, stageLabel, statusLabel } from '$lib/utils/format';
  import { canRunExposure, runExposure, type ExposureHandle } from '$lib/webgl/exposureScene';
  import ImageTrace from '$lib/components/ImageTrace.svelte';

  interface Props {
    onRegenerate?: () => void;
    onClear?: () => void;
    aiAssistantEnabled?: boolean;
  }

  let { onRegenerate = () => {}, onClear = () => {}, aiAssistantEnabled = false }: Props = $props();

  const view = $derived($previewStore);
  const loading = $derived(view.loading);
  const error = $derived(view.error);
  const job = $derived(view.job);
  const prompt = $derived(view.prompt);
  const slots = $derived(view.slots);
  const completedSlots = $derived(view.completedSlots);
  const pendingSlots = $derived(view.pendingSlots);
  const compareSource = $derived(view.compareSource);
  const submissionErrors = $derived(view.submissionErrors);
  const selectedSlot = $derived(view.selectedSlot);
  const selectedImage = $derived(selectedSlot?.image || null);
  // Metadata belongs to the image's own job, never the last submitted one.
  const selectedJob = $derived(selectedSlot?.job || job);
  const selectedPrompt = $derived(selectedSlot?.prompt || prompt);
  const activeJobId = $derived(view.job?.job_id || '');
  const hasFinalImage = $derived(view.hasFinalImage);
  const showStreamingGrid = $derived(!hasFinalImage && slots.length > 1);
  const activePendingJob = $derived(pendingSlots.find((slot) => slot.job)?.job || null);
  const isStreamingPending = $derived(pendingSlots.some((slot) => slot.status === 'streaming') || Boolean(activePendingJob?.streaming));
  const singlePendingSlot = $derived(slots[0] || null);

  let loadedPreviewSrc = $state('');
  let failedPreviewSrc = $state('');
  let failedStripThumbs = $state<Record<string, boolean>>({});
  let compareUpload = $state.raw<{ source: PreviewCompareSource; url: string } | null>(null);
  let showOriginal = $state(false);
  let failedCompareUrl = $state('');
  let previousCompareSource: PreviewCompareSource | null = null;
  let previousComparedSlotId = '';

  $effect(() => {
    const source = compareSource;
    if (source?.kind !== 'upload') {
      compareUpload = null;
      return;
    }
    const url = URL.createObjectURL(source.file);
    compareUpload = { source, url };
    return () => URL.revokeObjectURL(url);
  });

  const selectedImageUrl = $derived(selectedImage?.image_url || '');
  const compareUrl = $derived(
    compareSource?.kind === 'gallery'
      ? compareSource.url
      : compareUpload?.source === compareSource ? compareUpload.url : ''
  );

  $effect(() => {
    const slotId = selectedSlot?.id || '';
    if (compareSource !== previousCompareSource || slotId !== previousComparedSlotId) {
      showOriginal = false;
      failedCompareUrl = '';
      previousCompareSource = compareSource;
      previousComparedSlotId = slotId;
    }
  });
  const selectedFilename = $derived(selectedImage?.filename || '');
  const selectedImageIndex = $derived(selectedSlot ? completedSlots.findIndex((slot) => slot.id === selectedSlot.id) : -1);
  const previewWidth = $derived(selectedImage?.image_width || selectedJob?.image_width || undefined);
  const previewHeight = $derived(selectedImage?.image_height || selectedJob?.image_height || undefined);
  // The result seats into the well once it has actually arrived and decoded.
  const seated = $derived(Boolean(selectedImageUrl) && loadedPreviewSrc === selectedImageUrl && failedPreviewSrc !== selectedImageUrl);
  const previewFailed = $derived(Boolean(selectedImageUrl) && failedPreviewSrc === selectedImageUrl);
  const previewSize = $derived(
    displayImageSize({
      size: selectedJob?.size || null,
      image_width: selectedImage?.image_width ?? selectedJob?.image_width ?? null,
      image_height: selectedImage?.image_height ?? selectedJob?.image_height ?? null
    })
  );

  function slotStatusLabel(slot: PreviewSlot) {
    if (slot.status === 'waiting') return $t.preview.streamingSlotWaiting;
    if (slot.status === 'streaming') return $t.preview.streamingPreviewBadge;
    if (slot.status === 'cancelled') return $t.statuses.cancelled;
    if (slot.status === 'failed') return $t.statuses.error;
    return $t.statuses.success;
  }

  function slotAlt(slot: PreviewSlot, index: number) {
    if (slot.status === 'streaming') return $t.preview.streamingSlotAlt(index + 1);
    if (slot.status === 'waiting') return $t.preview.pendingSlotAlt(index + 1);
    return $t.preview.resultThumbAlt(index + 1);
  }

  function slotThumbUrl(slot: PreviewSlot) {
    if (slot.image) return failedStripThumbs[slot.id] ? slot.image.image_url : slot.image.thumb_url;
    return slot.previewUrl;
  }

  // The exposure: DESIGN.md's one WebGL moment. Runs once per result, over
  // the plain CSS fade+scale below, and falls back to it entirely when
  // motion is reduced or WebGL is unavailable.
  let previewImageEl = $state<HTMLImageElement | null>(null);
  let exposureCanvasEl = $state<HTMLCanvasElement | null>(null);
  let showExposureCanvas = $state(false);
  let exposedKey = '';
  let exposureRunId = 0;
  let exposureHandle: ExposureHandle | null = null;

  const activeExposureKey = $derived(seated && selectedSlot ? `${selectedSlot.jobId}::${selectedSlot.image?.image_id || selectedSlot.id}` : '');

  $effect(() => {
    if (activeExposureKey && activeExposureKey !== exposedKey) {
      exposedKey = activeExposureKey;
      void triggerExposure(activeExposureKey);
    }
  });

  function signatureDurationMs(): number {
    const fallback = 380;
    if (typeof window === 'undefined') return fallback;
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--dur-signature').trim();
    const ms = parseFloat(raw);
    return Number.isFinite(ms) && ms > 0 ? ms : fallback;
  }

  async function triggerExposure(key: string) {
    const runId = ++exposureRunId;
    exposureHandle?.cancel();
    exposureHandle = null;
    showExposureCanvas = false;
    if (!canRunExposure()) return;
    showExposureCanvas = true;
    await tick();
    if (runId !== exposureRunId) return;
    const canvas = exposureCanvasEl;
    const img = previewImageEl;
    if (!canvas || !img || !img.complete || img.naturalWidth === 0) {
      if (runId === exposureRunId) showExposureCanvas = false;
      return;
    }
    const handle = runExposure(canvas, img, signatureDurationMs());
    exposureHandle = handle;
    try {
      await handle.done;
    } finally {
      // Only the latest run may clear the shared handle/canvas flags.
      if (runId === exposureRunId) {
        exposureHandle = null;
        showExposureCanvas = false;
      }
    }
  }

  onDestroy(() => {
    exposureRunId += 1;
    exposureHandle?.cancel();
    exposureHandle = null;
  });
</script>

<section id="preview-panel" class="app-section px-1 py-1 sm:px-0">
  <div class="mb-4 flex items-center justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.preview.title}</h2>
      <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{prompt || $t.preview.subtitle}</p>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      {#if selectedFilename}
        <a href={downloadUrl(selectedFilename)} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">{$t.common.download}</a>
      {/if}
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!job && !hasFinalImage} onclick={onRegenerate}>
        {$t.preview.regenerate}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={onClear}>
        {$t.common.clear}
      </button>
    </div>
  </div>

  {#if error}
    <div
      class={`${job?.status === 'partial_failure' ? 'status-warning' : 'status-error'} px-4 py-3 text-sm break-words`}
      role={job?.status === 'partial_failure' ? 'status' : 'alert'}
    >
      {error}
    </div>
  {/if}
  {#each submissionErrors as submissionError (submissionError.id)}
    <div class="status-error mt-2 px-4 py-3 text-sm break-words" role="alert" data-submission-error={submissionError.id}>
      {submissionError.message}
    </div>
  {/each}

  {#snippet loadingBanner()}
    <div class="flex min-w-0 items-center gap-3 border-b border-stone-200 bg-white/80 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/80" role="status" aria-live="polite" aria-atomic="true">
      <span class="spinner shrink-0"></span>
      <div class="min-w-0 flex-1">
        {#if isStreamingPending}
          <p class="truncate text-sm font-semibold text-stone-900 dark:text-zinc-100">{$t.preview.streamingPreviewLabel}</p>
          <p class="mt-0.5 text-xs text-stone-500 dark:text-zinc-400">{stageLabel(activePendingJob, $t.stages) || $t.preview.working}</p>
        {:else}
          <p class="truncate text-sm font-semibold text-stone-900 dark:text-zinc-100">{stageLabel(activePendingJob, $t.stages) || $t.preview.working}</p>
          <p class="mt-0.5 text-xs text-stone-500 dark:text-zinc-400">{statusLabel(activePendingJob?.status, $t.statuses) || $t.preview.queued}</p>
        {/if}
      </div>
    </div>
  {/snippet}

  <div
    class={`preview-well mt-4 flex min-h-[360px] items-center justify-center overflow-hidden rounded-xl border border-stone-200 dark:border-zinc-800 ${selectedImageUrl || pendingSlots.length ? 'bg-stone-100 dark:bg-zinc-950' : 'preview-empty'}`}
    data-seated={seated}
  >
    {#if showStreamingGrid}
      <div class="flex h-full w-full flex-col">
        {@render loadingBanner()}
        <div class="grid flex-1 grid-cols-2 content-start gap-3 overflow-y-auto p-3 lg:grid-cols-3">
          {#each slots as slot, slotIndex (slot.id)}
            {@const unitStatus = slot.job?.unit_statuses?.[String(slot.unitIndex)]}
            <div class="relative aspect-square" data-slot-status={slot.status}>
              {#if slot.previewUrl}
                <div class="preview-image-frame relative h-full w-full">
                  <img
                    src={slot.previewUrl}
                    alt={slotAlt(slot, slotIndex)}
                    class="h-full w-full rounded-lg object-contain opacity-90"
                    loading="eager"
                    decoding="async"
                  />
                  <span class="absolute left-2 top-2 rounded-sm bg-black/60 px-2 py-1 text-xs font-medium text-white">{slotStatusLabel(slot)} {slotIndex + 1}</span>
                </div>
              {:else}
                <div class="flex h-full w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-stone-300 dark:border-zinc-700">
                  <span class:spinner={slot.status === 'waiting' || slot.status === 'streaming'}></span>
                  <span class="text-xs text-stone-500 dark:text-zinc-400">{unitStatus && unitStatus !== 'running' && unitStatus !== 'queued' ? statusLabel(unitStatus, $t.statuses) : slotStatusLabel(slot)}</span>
                  <span class="absolute left-2 top-2 rounded-sm bg-black/60 px-2 py-1 text-xs font-medium text-white">{slotIndex + 1}</span>
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {:else if selectedImageUrl}
      <div class="flex h-full w-full flex-col">
        {#if loading}
          {@render loadingBanner()}
        {/if}
        {#if compareUrl && seated && selectedJob?.operation === 'edit'}
          <div class="flex justify-end border-b border-stone-200 bg-white/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900/80">
            {#if failedCompareUrl === compareUrl}
              <span class="text-xs text-stone-500 dark:text-zinc-400" role="status">{$t.preview.originalUnavailable}</span>
            {:else}
              <button
                type="button"
                class={`control-focus rounded-md border px-3 py-1.5 text-xs font-medium ${showOriginal ? 'border-emerald-500 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200' : 'border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800'}`}
                aria-pressed={showOriginal}
                title={compareSource?.label || ''}
                onclick={() => (showOriginal = !showOriginal)}
              >
                {showOriginal ? $t.preview.showEditedResult : $t.preview.compareOriginal}
              </button>
            {/if}
          </div>
        {/if}
        <div class="flex min-h-[320px] flex-1 items-center justify-center p-3">
          <div class="preview-image-frame relative">
            <img
              bind:this={previewImageEl}
              src={selectedImageUrl}
              alt={$t.preview.generatedAlt}
              aria-hidden={showOriginal}
              class={`preview-image max-h-[640px] max-w-full rounded-lg object-contain ${seated ? 'preview-image-seated' : ''}`}
              loading="eager"
              fetchpriority="high"
              decoding="async"
              width={previewWidth}
              height={previewHeight}
              onload={() => {
                loadedPreviewSrc = selectedImageUrl;
                failedPreviewSrc = '';
              }}
              onerror={() => {
                failedPreviewSrc = selectedImageUrl;
              }}
            />
            {#if showExposureCanvas}
              <canvas bind:this={exposureCanvasEl} class="preview-exposure-canvas rounded-lg" aria-hidden="true"></canvas>
            {/if}
            {#if showOriginal && compareUrl && failedCompareUrl !== compareUrl}
              <img
                src={compareUrl}
                alt={$t.preview.originalAlt(compareSource?.label || '')}
                class="absolute inset-0 h-full w-full rounded-lg bg-stone-100 object-contain dark:bg-zinc-950"
                onerror={() => {
                  failedCompareUrl = compareUrl;
                  showOriginal = false;
                }}
              />
            {/if}
            {#if selectedJob?.mask_applied && !showOriginal}
              <span class="absolute left-2 top-2 rounded-sm border border-emerald-500/40 bg-black/60 px-2 py-1 text-xs font-medium text-emerald-300">{$t.jobs.maskedBadge}</span>
              {#if selectedImage?.paste_back}
                <span class="absolute left-2 top-10 rounded-sm border border-emerald-500/40 bg-black/60 px-2 py-1 text-xs font-medium text-emerald-300" title={$t.jobs.pasteBackDetail(selectedImage.paste_back, selectedImage.paste_back_scale ?? null)}>{$t.jobs.pasteBackBadge(selectedImage.paste_back)}</span>
              {/if}
            {/if}
            {#if previewFailed}
              <div class="preview-image-error" role="status">{$t.lightbox.originalLoadFailed}</div>
            {/if}
          </div>
        </div>
        {#if slots.length > 1}
          <div class="border-t border-stone-200 p-3 dark:border-zinc-800">
            <div class="mb-2 flex items-center justify-between gap-3 text-xs text-stone-500 dark:text-zinc-500">
              <span>{$t.preview.resultCount(completedSlots.length)}</span>
              {#if pendingSlots.length > 0}
                <span role="status" class="inline-flex items-center gap-1.5">
                  <span class="spinner spinner-inline"></span>
                  {$t.preview.pendingCount(pendingSlots.length)}
                </span>
              {:else}
                <span>{selectedImageIndex + 1} / {completedSlots.length}</span>
              {/if}
            </div>
            <div class="flex gap-2 overflow-x-auto pb-1" data-testid="preview-slot-strip">
              {#each slots as slot, index (slot.id)}
                {#if slot.status === 'success' && slot.image}
                  <button
                    type="button"
                    aria-label={$t.preview.selectResult(index + 1)}
                    aria-pressed={slot.id === selectedSlot?.id}
                    class={`control-focus relative aspect-square w-20 shrink-0 overflow-hidden rounded-lg border ${slot.id === selectedSlot?.id ? 'border-emerald-400 ring-1 ring-emerald-400/40' : 'border-stone-200 hover:border-stone-400 dark:border-zinc-800 dark:hover:border-zinc-600'}`}
                    onclick={() => previewStore.selectSlot(slot.id)}
                  >
                    <img
                      src={slotThumbUrl(slot)}
                      alt={slotAlt(slot, index)}
                      class="h-full w-full object-cover"
                      loading="lazy"
                      decoding="async"
                      onerror={() => (failedStripThumbs = { ...failedStripThumbs, [slot.id]: true })}
                    />
                    <span class="absolute left-1.5 top-1.5 rounded-sm bg-white/90 px-1.5 py-0.5 text-xs font-semibold text-stone-700 dark:bg-zinc-950/80 dark:text-zinc-200">{index + 1}</span>
                  </button>
                {:else if slot.previewUrl}
                  <div
                    class="relative aspect-square w-20 shrink-0 overflow-hidden rounded-lg border border-stone-300 dark:border-zinc-700"
                    data-slot-status={slot.status}
                  >
                    <img src={slot.previewUrl} alt={slotAlt(slot, index)} class="h-full w-full object-cover opacity-70" loading="lazy" decoding="async" />
                    <span class="absolute inset-0 flex items-center justify-center"><span class="spinner spinner-inline"></span></span>
                    <span class="absolute left-1.5 top-1.5 rounded-sm bg-black/60 px-1.5 py-0.5 text-xs font-semibold text-white">{index + 1}</span>
                  </div>
                {:else}
                  <div
                    class={`relative flex aspect-square w-20 shrink-0 flex-col items-center justify-center gap-1 overflow-hidden rounded-lg border border-dashed ${slot.status === 'failed' ? 'border-amber-400/60 bg-amber-500/5' : slot.status === 'cancelled' ? 'border-stone-300 bg-stone-100 dark:border-zinc-700 dark:bg-zinc-900' : 'border-stone-300 dark:border-zinc-700'}`}
                    data-slot-status={slot.status}
                    role="group"
                    aria-label={slotAlt(slot, index)}
                  >
                    {#if slot.status === 'waiting' || slot.status === 'streaming'}
                      <span class="spinner spinner-inline"></span>
                    {/if}
                    <span class="px-1 text-center text-[10px] leading-tight text-stone-500 dark:text-zinc-400">{slotStatusLabel(slot)}</span>
                    <span class="absolute left-1.5 top-1.5 rounded-sm bg-black/60 px-1.5 py-0.5 text-xs font-semibold text-white">{index + 1}</span>
                  </div>
                {/if}
              {/each}
            </div>
          </div>
        {/if}
      </div>
    {:else if loading && singlePendingSlot?.previewUrl}
      <div class="flex h-full w-full flex-col" data-slot-status={singlePendingSlot.status}>
        {@render loadingBanner()}
        <div class="flex min-h-[320px] flex-1 items-center justify-center p-3">
          <div class="preview-image-frame relative">
            <img
              src={singlePendingSlot.previewUrl}
              alt={slotAlt(singlePendingSlot, 0)}
              class="preview-image max-h-[640px] max-w-full rounded-lg object-contain opacity-90"
              loading="eager"
              decoding="async"
            />
            <span class="absolute left-2 top-2 rounded-sm bg-black/60 px-2 py-1 text-xs font-medium text-white">{slotStatusLabel(singlePendingSlot)}</span>
          </div>
        </div>
      </div>
    {:else if loading}
      <div class="flex max-w-sm flex-col items-center px-6 text-center" role="status" aria-live="polite" aria-atomic="true" data-slot-status={singlePendingSlot?.status || 'waiting'}>
        <span class="spinner"></span>
        <p class="mt-4 text-sm font-semibold text-stone-900 dark:text-zinc-100">{stageLabel(activePendingJob, $t.stages) || $t.preview.working}</p>
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-400">{statusLabel(activePendingJob?.status, $t.statuses) || $t.preview.queued}</p>
      </div>
    {:else}
      <div class="px-6 text-center">
        <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{$t.preview.noPreview}</p>
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.preview.noPreviewHint}</p>
      </div>
    {/if}
  </div>

  {#if selectedJob}
    <dl class="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 border-t border-stone-200 pt-4 text-xs dark:border-zinc-800 sm:grid-cols-5">
      <div><dt class="text-stone-500 dark:text-zinc-400">{$t.common.status}</dt><dd class="mt-1 text-stone-800 dark:text-zinc-200">{statusLabel(selectedJob.status, $t.statuses)}</dd></div>
      <div><dt class="text-stone-500 dark:text-zinc-400">{$t.common.completedAt}</dt><dd class="mt-1 whitespace-nowrap text-stone-800 dark:text-zinc-200">{formatLocalTime(selectedJob.completed_at)}</dd></div>
      <div><dt class="text-stone-500 dark:text-zinc-400">{$t.common.size}</dt><dd class="mt-1 text-stone-800 dark:text-zinc-200">{previewSize}</dd></div>
      <div class="min-w-0"><dt class="text-stone-500 dark:text-zinc-400">{$t.common.model}</dt><dd class="mt-1 truncate text-stone-800 dark:text-zinc-200">{selectedJob.model || '-'}</dd></div>
      <div><dt class="text-stone-500 dark:text-zinc-400">{$t.common.duration}</dt><dd class="mt-1 text-stone-800 dark:text-zinc-200">{selectedJob.duration || '-'}</dd></div>
    </dl>
    {#if selectedImage}
      <div class="mt-3"><ImageTrace image={selectedImage} prompt={selectedJob.prompt || selectedPrompt} requestedSize={selectedJob.size} requestedQuality={selectedJob.quality} presetName={selectedJob.api_preset_name} aiAssistantEnabled={aiAssistantEnabled} /></div>
    {/if}
  {/if}
</section>
