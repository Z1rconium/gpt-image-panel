<script lang="ts">
  import { t } from '$lib/i18n';
  import { copyText } from '$lib/utils/format';

  type TraceImage = {
    image_width?: number | null;
    image_height?: number | null;
    sent_prompt?: string | null;
    revised_prompt?: string | null;
    reported_size?: string | null;
    reported_quality?: string | null;
    upstream_duration_ms?: number | null;
    chroma_status?: string | null;
    diagnostics?: string[] | null;
  };

  let { image, prompt, requestedSize, requestedQuality, presetName }: {
    image: TraceImage;
    prompt: string;
    requestedSize?: string | null;
    requestedQuality?: string | null;
    presetName?: string | null;
  } = $props();

  const diagnosticMessages = $derived(
    (image.diagnostics || []).flatMap((code) => {
      const messages = $t.trace.diagnostics;
      switch (code) {
        case 'prompt_rewritten':
          return [messages.promptRewritten(presetName || '')];
        case 'prompt_rewritten_despite_guard':
          return [messages.promptRewrittenDespiteGuard];
        case 'size_ignored':
          return [messages.sizeIgnored];
        case 'quality_ignored':
          return [messages.qualityIgnored];
        case 'params_not_sent':
          return [messages.paramsNotSent];
        default:
          return [];
      }
    })
  );

  const actualPixels = $derived(
    image.image_width && image.image_height ? `${image.image_width} × ${image.image_height}` : $t.trace.unprovided
  );
  const sentPrompt = $derived(image.sent_prompt || prompt);

  type FieldComparison = 'match' | 'differs' | 'unreported' | 'none';

  // Only compare values upstream actually reported; a missing report is not
  // evidence that the model ignored the parameter.
  function compareField(requested: string | null | undefined, reported: string | null | undefined): FieldComparison {
    const left = (requested || '').trim().toLowerCase();
    const right = (reported || '').trim().toLowerCase();
    if (!left) return 'none';
    if (!right) return 'unreported';
    return left === right ? 'match' : 'differs';
  }

  const sizeComparison = $derived(compareField(requestedSize, image.reported_size));
  const qualityComparison = $derived(compareField(requestedQuality, image.reported_quality));
</script>

<section class="rounded-lg border border-stone-200 bg-stone-50/70 p-3 text-xs text-stone-700 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-300" aria-label={$t.trace.title}>
  <h3 class="font-semibold text-stone-800 dark:text-zinc-200">{$t.trace.title}</h3>
  {#if diagnosticMessages.length}
    <ul class="mt-2 space-y-1.5" aria-label={$t.trace.diagnostics.title}>
      {#each diagnosticMessages as message}
        <li class="rounded-md border border-amber-400/40 bg-amber-500/10 px-2 py-1.5 text-amber-800 dark:text-amber-200">{message}</li>
      {/each}
    </ul>
  {/if}
  {#if image.chroma_status === 'not_detected'}
    <p class="mt-2 rounded-md border border-amber-400/40 bg-amber-500/10 px-2 py-1.5 text-amber-800 dark:text-amber-200" role="status">{$t.trace.chromaNotDetected}</p>
  {:else if image.chroma_status === 'applied'}
    <p class="mt-2 text-emerald-700 dark:text-emerald-300">{$t.trace.chromaApplied}</p>
  {/if}
  <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
    <div><dt class="text-stone-500">{$t.trace.actualPixels}</dt><dd>{actualPixels}</dd></div>
    <div><dt class="text-stone-500">{$t.trace.apiWait}</dt><dd>{image.upstream_duration_ms == null ? $t.trace.unprovided : `${image.upstream_duration_ms} ms`}</dd></div>
    <div><dt class="text-stone-500">{$t.trace.requestedSize}</dt><dd>{requestedSize || $t.trace.unprovided}</dd></div>
    <div>
      <dt class="text-stone-500">{$t.trace.reportedSize}</dt>
      <dd class="flex flex-wrap items-center gap-1.5">
        <span>{image.reported_size || $t.trace.unprovided}</span>
        {#if sizeComparison === 'differs'}
          <span class="rounded border border-amber-400/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">{$t.trace.differs}</span>
        {:else if sizeComparison === 'match'}
          <span class="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">{$t.trace.matches}</span>
        {:else if sizeComparison === 'unreported'}
          <span class="text-[10px] text-stone-400 dark:text-zinc-500">{$t.trace.notReported}</span>
        {/if}
      </dd>
    </div>
    <div><dt class="text-stone-500">{$t.trace.requestedQuality}</dt><dd>{requestedQuality || $t.trace.unprovided}</dd></div>
    <div>
      <dt class="text-stone-500">{$t.trace.reportedQuality}</dt>
      <dd class="flex flex-wrap items-center gap-1.5">
        <span>{image.reported_quality || $t.trace.unprovided}</span>
        {#if qualityComparison === 'differs'}
          <span class="rounded border border-amber-400/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">{$t.trace.differs}</span>
        {:else if qualityComparison === 'match'}
          <span class="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">{$t.trace.matches}</span>
        {:else if qualityComparison === 'unreported'}
          <span class="text-[10px] text-stone-400 dark:text-zinc-500">{$t.trace.notReported}</span>
        {/if}
      </dd>
    </div>
  </dl>
  {#if image.revised_prompt}
    <div class="mt-3 grid gap-2 sm:grid-cols-2">
      <div class="min-w-0 rounded-md border border-stone-200 p-2 dark:border-zinc-800">
        <div class="flex items-center justify-between gap-2"><strong>{$t.trace.originalPrompt}</strong><button type="button" class="control-focus text-emerald-700 dark:text-emerald-300" onclick={() => void copyText(prompt)}>{$t.trace.copy}</button></div>
        <p class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words">{prompt}</p>
      </div>
      <div class="min-w-0 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2">
        <div class="flex items-center justify-between gap-2"><strong>{$t.trace.revisedPrompt}</strong><button type="button" class="control-focus text-emerald-700 dark:text-emerald-300" onclick={() => void copyText(image.revised_prompt || '')}>{$t.trace.copy}</button></div>
        <p class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words">{image.revised_prompt}</p>
      </div>
    </div>
  {/if}
  {#if sentPrompt && sentPrompt !== prompt}
    <details class="mt-3"><summary class="cursor-pointer font-medium">{$t.trace.sentPrompt}</summary><p class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words">{sentPrompt}</p><button type="button" class="control-focus mt-1 text-emerald-700 dark:text-emerald-300" onclick={() => void copyText(sentPrompt)}>{$t.trace.copy}</button></details>
  {/if}
</section>
