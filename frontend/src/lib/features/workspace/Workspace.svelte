<script lang="ts">
  import { onMount } from 'svelte';
  import AccessGate from '$lib/components/AccessGate.svelte';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import EditSourcePicker from '$lib/components/EditSourcePicker.svelte';
  import Header from '$lib/components/Header.svelte';
  import PreviewPanel from '$lib/components/PreviewPanel.svelte';
  import PromptForm from '$lib/components/PromptForm.svelte';
  import ToastHost from '$lib/components/ToastHost.svelte';
  import { apiFetch } from '$lib/api/client';
  import { language, t } from '$lib/i18n';
  import type { AssistantJobDiagnoseResponse, AssistantRecommendParamsResponse, PromptOptimizeResponse } from '$lib/api/types/assistant';
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import type { GenerateJobStatus } from '$lib/api/types/jobs';
  import type { AIAssistantSettingsInput, OverallConfigUpdateRequest, SettingsInput } from '$lib/api/types/settings';
  import type { PromptSnippet, PromptSnippetCreateInput, PromptSnippetUpdateInput } from '$lib/api/types/snippets';
  import { accessStore } from '$lib/stores/access';
  import { assistantStore, isAbortError } from '$lib/stores/assistant';
  import { confirmStore } from '$lib/stores/confirm';
  import { editMaskDiscards, editSourceCount, editSourceStore, isMaskValid, MAX_EDIT_SOURCE_IMAGES, primaryEditSourceId, type EditMask } from '$lib/stores/editSource';
  import { galleryActivityStore, galleryStore } from '$lib/stores/gallery';
  import { jobsStore } from '$lib/stores/jobs';
  import { lightboxStore } from '$lib/stores/lightbox';
  import { nodeImageResult } from '$lib/stores/nodeImage';
  import { initialPromptFormState, previewStore, type PromptFormState } from '$lib/stores/preview';
  import { promptSnippetsStore } from '$lib/stores/promptSnippets';
  import { settingsActivityStore, settingsStore } from '$lib/stores/settings';
  import { toastStore, uiStore, type ToastOptions } from '$lib/stores/ui';
  import { versionStore } from '$lib/stores/version';
  import { extractImageFilesFromClipboard } from '$lib/utils/clipboard';
  import { copyText, imageUrl } from '$lib/utils/format';
  import { canPrefetchNonCritical } from '$lib/utils/network';
  import { buildPromptOptimizeRequest } from '$lib/utils/promptOptimizer';
  import {
    aiAssistantPanel,
    editGalleryDialogPanel,
    editPreviewPanel,
    galleryGridPanel,
    imagePromptPanel,
    jobsPanel,
    lightboxPanel,
    maskEditorPanel,
    nodeImageResultPanel,
    optimizerPanel,
    settingsPanel,
    sizePanel,
    snippetsPanel
  } from '$lib/features/workspace/panels';
  import { promptForm } from '$lib/features/workspace/formState.svelte';
  import { createLightboxController } from '$lib/features/workspace/lightbox';
  import { createPanelController } from '$lib/features/workspace/panelController';
  import { installWorkspaceLifecycle } from '$lib/features/workspace/lifecycle';
  import { waitForGalleryAnalysis } from '$lib/features/workspace/gallery';
  import { createUrlSyncScheduler, readPageUrl, writePageUrl, type JobsTab } from '$lib/utils/pageUrlSync';
  import {
    galleryEntryToEditForm,
    galleryEntryToPromptForm,
    galleryEntryToPromptOnly,
    jobToPromptForm
  } from '$lib/utils/promptForm';

  let jobsTab = $state<JobsTab>('running');
  let editPicker: EditSourcePicker | undefined = $state();
  let galleryEditImage = $state<GalleryEntry | null>(null);
  let galleryEditDialogOpen = $state(false);
  let editPreviewUrl = $state('');
  let editPreviewLabel = $state('');
  let jobDiagnoses = $state<Record<string, AssistantJobDiagnoseResponse>>({});
  let optimizingPrompt = $state(false);
  let gallerySuccessRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  const handledTerminalJobIds = new Set<string>();

  const hasEditSource = $derived(editSourceCount($editSourceStore) > 0);
  const editSources = $derived.by(() => {
    const state = $editSourceStore;
    const primaryId = primaryEditSourceId(state);
    const mask = state.mask;
    return [
      ...(state.selectedGalleryImageId
        ? [
            {
              id: state.selectedGalleryImageId,
              label: state.galleryLabel || state.galleryPreviewLabel,
              previewUrl: state.galleryPreviewUrl,
              kind: 'gallery' as const
            }
          ]
        : []),
      ...state.files.map((source) => ({
        id: source.id,
        label: source.label,
        previewUrl: source.previewUrl,
        kind: 'upload' as const
      }))
    ].map((source) => ({
      ...source,
      isPrimary: source.id === primaryId,
      maskCoverage: mask && mask.sourceId === source.id ? mask.coverage : null
    }));
  });
  const primaryEditSource = $derived.by(() => {
    const state = $editSourceStore;
    if (state.selectedGalleryImageId) {
      return {
        id: state.selectedGalleryImageId,
        label: state.galleryLabel || state.galleryPreviewLabel,
        previewUrl: state.galleryPreviewUrl
      };
    }
    const first = state.files[0];
    return first ? { id: first.id, label: first.label, previewUrl: first.previewUrl } : null;
  });
  const activeEditMask = $derived(isMaskValid($editSourceStore) ? $editSourceStore.mask : null);
  const activeJobsCount = $derived($jobsStore.jobs.length);
  const optimizerSettings = $derived($settingsStore.settings?.prompt_optimizer || null);
  const promptOptimizerConfigAvailable = $derived(
    Boolean(
      optimizerSettings?.api_url.trim() &&
        optimizerSettings.model.trim() &&
        optimizerSettings.has_api_key
    )
  );
  const optimizerAvailable = $derived(
    Boolean(optimizerSettings?.enabled && promptOptimizerConfigAvailable)
  );
  const optimizerAssistantEnabled = $derived(
    optimizerAvailable &&
      !$uiStore.settingsOpen &&
      !$uiStore.promptSnippetsOpen &&
      !$uiStore.imagePromptOpen &&
      !$uiStore.jobsOpen &&
      !$uiStore.editPreviewOpen &&
      !$uiStore.sizeDialogOpen &&
      !$uiStore.maskEditorOpen &&
      !$confirmStore.request &&
      !Boolean($lightboxStore.image)
  );
  const aiAssistantSettings = $derived($settingsStore.settings?.ai_assistant || null);
  const aiAssistantAvailable = $derived(
    Boolean(
      aiAssistantSettings?.enabled &&
        promptOptimizerConfigAvailable &&
        (aiAssistantSettings.vision_model.trim() || optimizerSettings?.model.trim())
    )
  );
  const r2BackupSettings = $derived($settingsStore.settings?.r2_backup || null);
  const r2BackupAvailable = $derived(
    Boolean(
      r2BackupSettings?.enabled &&
        r2BackupSettings.endpoint_url.trim() &&
        r2BackupSettings.bucket_name.trim() &&
        r2BackupSettings.has_access_key_id &&
        r2BackupSettings.has_secret_access_key
    )
  );
  const nodeImageSettings = $derived($settingsStore.settings?.nodeimage || null);
  const nodeImageAvailable = $derived(
    Boolean(nodeImageSettings?.enabled && nodeImageSettings.api_key_resolvable)
  );

  const urlSync = createUrlSyncScheduler((mode) => {
    writePageUrl(
      {
        page: $galleryStore.page,
        filters: $galleryStore.filters,
        imageId: $lightboxStore.image?.id || null,
        jobsTab: $uiStore.jobsOpen ? jobsTab : null
      },
      mode
    );
  });
  const lightboxController = createLightboxController({
    getImage: () => $lightboxStore.image,
    getGallery: () => $galleryStore.gallery,
    isAiAvailable: () => aiAssistantAvailable,
    setImage: lightboxStore.setImage,
    loadGalleryPage: (page, direction) => galleryStore.loadGallery(page, false, direction),
    prefetchPage: (page) => galleryStore.prefetchGalleryPage(page, 'next'),
    loadAiMetadata: assistantStore.loadGalleryMetadata,
    describeImage: assistantStore.describeGalleryImage,
    analyzeImage: assistantStore.analyzeGalleryImage,
    isAbortError,
    onNavigate: () => urlSync.schedule('replace'),
    onImageNotFound: () => showToast($t.messages.galleryImageNotFound, 'error'),
    onAnalyzed: () => showToast($t.messages.aiAssistantGalleryAnalyzed),
    onError: showError
  });

  const {
    loadingPanel,
    ensurePanel,
    prefetchPanel,
    rememberPanelFocus,
    restorePanelFocus,
    openPanel: openUiPanel,
    closePanel: closeUiPanel,
    reset: resetPanels
  } = createPanelController(showToast);

  // Keep the lightbox navigation state and neighbor prefetch in step with the
  // stores it reads; only these three values may trigger a re-sync.
  $effect(() => {
    lightboxController.sync($lightboxStore.image, $galleryStore.gallery, aiAssistantAvailable);
  });
  // Track the active preset's defaults without re-running on form keystrokes;
  // the controller reads/writes form fields inside untrack.
  $effect(() => {
    promptForm.applyPresetDefaults($settingsStore.settings);
  });
  $effect(() => {
    if (optimizerAssistantEnabled) void ensurePanel('optimizer', false);
  });
  // A mask discarded by a primary-image change happens inside the store; the
  // store only bumps a counter, so the warning lives here.
  let maskDiscardCount = 0;
  $effect(() => {
    const count = $editMaskDiscards;
    if (count > maskDiscardCount) {
      maskDiscardCount = count;
      showToast($t.messages.editMaskDiscarded);
    }
  });

  async function loadInitialData() {
    await Promise.all([settingsStore.loadSettings(), jobsStore.loadJobs(), applyUrlStateToApp()]);
    const activeJob = $jobsStore.jobs[0];
    if (activeJob) trackJob(activeJob.job_id);
    urlSync.setReady();
    urlSync.flush();
    jobsStore.startJobsEvents();
  }

  async function loadAuthenticatedData() {
    await Promise.all([versionStore.loadVersion(), loadInitialData()]);
  }

  function showToast(message: string, variant?: 'status' | 'error', options?: ToastOptions) {
    uiStore.showToast(message, variant, options);
  }

  function errorMessage(error: unknown, fallback = $t.messages.requestFailed) {
    const message = error instanceof Error ? error.message : fallback;
    return message || fallback;
  }

  function showError(error: unknown, fallback = $t.messages.requestFailed) {
    showToast(errorMessage(error, fallback), 'error');
  }

  function syncAfterGalleryMutation(mode: 'replace' | 'push' = 'replace', debounceMs = 0) {
    urlSync.schedule(mode, debounceMs);
  }

  async function syncLightboxFromUrl(imageId: string | null | undefined) {
    const nextImageId = String(imageId || '').trim();
    if (!nextImageId) {
      lightboxController.close();
      return;
    }

    if (!(await ensurePanel('lightbox'))) return;
    await lightboxController.openFromId(nextImageId, $galleryStore.gallery?.images || []);
  }

  async function openLightbox(image: GalleryEntry) {
    rememberPanelFocus('lightbox');
    if (!(await ensurePanel('lightbox'))) return;
    lightboxController.open(image);
    urlSync.schedule('push');
  }

  function closeLightbox() {
    lightboxController.close();
    urlSync.schedule('replace');
    restorePanelFocus('lightbox');
  }
  function lightboxNavigationBlocked() {
    return Boolean(
      $confirmStore.request ||
        $uiStore.editPreviewOpen ||
        $uiStore.sizeDialogOpen ||
        $uiStore.maskEditorOpen ||
        galleryEditDialogOpen ||
        $uiStore.promptSnippetsOpen ||
        $uiStore.imagePromptOpen ||
        $uiStore.jobsOpen ||
        $uiStore.settingsOpen
    );
  }

  async function openJobsDrawer(tab: JobsTab = jobsTab) {
    rememberPanelFocus('jobs');
    if (!(await ensurePanel('jobs'))) return;
    jobsTab = tab;
    setUi('jobsOpen', true);
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
      void jobsStore.loadJobHistory();
    } else if (tab === 'history' && $jobsStore.historyNeedsRefresh && !$jobsStore.historyLoading) {
      void jobsStore.refreshHistoryIfLoaded();
    }
    urlSync.schedule();
  }

  function closeJobsDrawer() {
    closeUiPanel('jobs', 'jobsOpen');
    urlSync.schedule();
  }

  async function openPromptSnippetsDrawer() {
    rememberPanelFocus('snippets');
    if (!(await ensurePanel('snippets'))) return;
    setUi('promptSnippetsOpen', true);
    void loadPromptSnippets();
  }

  function closePromptSnippetsDrawer() {
    closeUiPanel('snippets', 'promptSnippetsOpen');
  }

  async function openImagePromptDialog() {
    await openUiPanel('imagePrompt', 'imagePromptOpen');
  }

  function closeImagePromptDialog() {
    closeUiPanel('imagePrompt', 'imagePromptOpen');
  }

  function setJobsTab(tab: JobsTab) {
    jobsTab = tab;
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
      void jobsStore.loadJobHistory();
    } else if (tab === 'history' && $jobsStore.historyNeedsRefresh && !$jobsStore.historyLoading) {
      void jobsStore.refreshHistoryIfLoaded();
    }
    urlSync.schedule();
  }

  async function applyUrlStateToApp() {
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    const { gallery: state, jobsTab: nextJobsTab, imageId } = readPageUrl(url);

    urlSync.setApplying(true);
    try {
      galleryStore.setPageAndFilters(state.page, state.filters);
      jobsTab = nextJobsTab || 'running';
      if (nextJobsTab && (await ensurePanel('jobs'))) setUi('jobsOpen', true);
      else setUi('jobsOpen', false);

      if (nextJobsTab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
        void jobsStore.loadJobHistory();
      }

      await galleryStore.loadGallery(state.page);
      await syncLightboxFromUrl(imageId);
    } finally {
      urlSync.setApplying(false);
    }
    urlSync.schedule();
  }

  function setUi<K extends keyof typeof $uiStore>(key: K, value: (typeof $uiStore)[K]) {
    uiStore.setKey(key, value);
  }

  function saveSettings(body: SettingsInput) {
    void settingsStore.saveSettings(body, showToast).then((saved) => {
      if (saved) setUi('settingsOpen', false);
    });
  }

  function createPreset() {
    void settingsStore.createPreset(showToast);
  }

  function activatePreset(presetId: string) {
    return settingsStore.activatePreset(presetId, showToast);
  }

  function deletePreset(presetId: string) {
    return settingsStore.deletePreset(presetId, showToast);
  }

  function checkPresetHealth(presetId: string) {
    void settingsStore.checkPresetHealth(presetId);
  }

  function checkR2Health(body: NonNullable<SettingsInput['r2_backup']>) {
    void settingsStore.checkR2Health(body);
  }

  function checkPromptOptimizerHealth() {
    void settingsStore.checkPromptOptimizerHealth();
  }

  function clearPromptOptimizerHealth() {
    settingsStore.clearPromptOptimizerHealth();
  }

  function checkAiAssistantHealth(body: AIAssistantSettingsInput) {
    void settingsStore.checkAiAssistantHealth(body);
  }

  function clearAiAssistantHealth() {
    settingsStore.clearAiAssistantHealth();
  }

  function clearPresetHealth() {
    settingsStore.clearPresetHealth();
  }

  function loadPromptOptimizerSystemPrompt() {
    return settingsStore.loadPromptOptimizerSystemPrompt();
  }

  function savePromptOptimizerSystemPrompt(systemPrompt: string) {
    return settingsStore.savePromptOptimizerSystemPrompt(systemPrompt, showToast);
  }

  function loadOverallConfig() {
    return settingsStore.loadOverallConfig();
  }

  function saveOverallConfig(body: OverallConfigUpdateRequest) {
    return settingsStore.saveOverallConfig(body, showToast);
  }

  function showNewGalleryImagesToast() {
    showToast($t.messages.newGalleryImagesAvailable, 'status', {
      actionLabel: $t.common.refresh,
      durationMs: 8000,
      onAction: () => {
        void galleryStore.loadGallery(1, false, 'jump', { lightweight: true });
        syncAfterGalleryMutation();
      }
    });
  }

  function scheduleGalleryRefreshAfterSuccess() {
    if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
    gallerySuccessRefreshTimer = setTimeout(() => {
      gallerySuccessRefreshTimer = null;
      if ($galleryStore.page !== 1) {
        showNewGalleryImagesToast();
        return;
      }
      void galleryStore.loadGallery(1, false, 'jump', { lightweight: true });
    }, 350);
  }

  function updatePreviewFromJob(job: GenerateJobStatus) {
    previewStore.setPreview(jobsStore.previewFromJob(job, $previewStore));
    if (job.status !== 'queued' && job.status !== 'running') {
      if (handledTerminalJobIds.has(job.job_id)) return;
      handledTerminalJobIds.add(job.job_id);
      if (handledTerminalJobIds.size > 200) {
        const oldestJobId = handledTerminalJobIds.values().next().value;
        if (oldestJobId) handledTerminalJobIds.delete(oldestJobId);
      }
      if (jobsStore.shouldRefreshJobsAfterSubmit()) void jobsStore.loadJobs();
      jobsStore.markHistoryStale();
      if (job.status === 'success' || job.status === 'partial_failure') scheduleGalleryRefreshAfterSuccess();
    }
  }

  function trackJob(jobId: string) {
    jobsStore.trackJob(jobId, async (job) => updatePreviewFromJob(job), previewStore.setError, previewStore.applyPreviewEvent);
  }

  function generateImage() {
    promptForm.normalizeQuantityForSubmit();
    void previewStore.generateImage(
      promptForm.snapshot(),
      jobsStore.makeQueuedPreview,
      trackJob,
      jobsStore.shouldRefreshJobsAfterSubmit() ? jobsStore.loadJobs : undefined
    );
  }

  function editImage() {
    promptForm.normalizeQuantityForSubmit();
    void previewStore.editImage(
      promptForm.snapshot(),
      $editSourceStore,
      jobsStore.makeQueuedPreview,
      trackJob,
      jobsStore.shouldRefreshJobsAfterSubmit() ? jobsStore.loadJobs : undefined
    );
  }

  function submitPrompt() {
    if (hasEditSource) editImage();
    else generateImage();
  }

  async function planEdit() {
    const goal = promptForm.prompt.trim();
    if (!goal) {
      previewStore.setError($t.messages.promptRequired);
      return;
    }
    try {
      const previousPrompt = promptForm.prompt;
      const previousSize = promptForm.size;
      const plan = await assistantStore.planEdit({
        goal,
        source_count: $editSourceStore.files.length + ($editSourceStore.selectedGalleryImageId ? 1 : 0),
        has_mask: Boolean(activeEditMask),
        current_prompt: promptForm.prompt,
        target_size: promptForm.size
      });
      if (plan.edit_prompt) {
        promptForm.prompt = plan.edit_prompt;
        if (plan.suggested_size) promptForm.size = plan.suggested_size;
      }
      showToast($t.messages.aiAssistantEditPlanReady, 'status', {
        actionLabel: $t.common.undo,
        onAction: () => {
          promptForm.prompt = previousPrompt;
          promptForm.size = previousSize;
        },
        durationMs: 8000
      });
    } catch (error) {
      showError(error);
    }
  }

  function setGalleryFilter(key: Parameters<typeof galleryStore.updateFilter>[0], value: Parameters<typeof galleryStore.updateFilter>[1]) {
    galleryStore.updateFilter(key, value);
    syncAfterGalleryMutation('replace', key === 'prompt' ? 300 : 0);
  }

  function resetGalleryFilters() {
    galleryStore.resetFilters();
    syncAfterGalleryMutation();
  }

  function loadGalleryPage(page: number, direction?: 'next' | 'prev' | 'jump') {
    void galleryStore.loadGallery(page, false, direction);
    syncAfterGalleryMutation();
  }

  function loadGalleryStats() {
    void galleryStore.loadGallery($galleryStore.page, true);
  }

  function promptContainsTag(prompt: string, value: string) {
    const normalized = value.trim().toLowerCase();
    return prompt
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .includes(normalized);
  }

  function appendPromptTag(value: string) {
    const tag = value.trim();
    if (!tag) return;
    if (promptContainsTag(promptForm.prompt, tag)) {
      showToast($t.messages.promptTagExists);
      return;
    }
    const prefix = promptForm.prompt.trim();
    promptForm.prompt = prefix ? `${prefix}, ${tag}` : tag;
  }

  async function loadPromptSnippets(query = '') {
    try {
      await promptSnippetsStore.loadSnippets(query);
    } catch (error) {
      showError(error);
    }
  }

  async function createPromptSnippet(input: PromptSnippetCreateInput) {
    try {
      await promptSnippetsStore.createSnippet(input);
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
    }
  }

  async function updatePromptSnippet(snippetId: string, input: PromptSnippetUpdateInput) {
    try {
      await promptSnippetsStore.updateSnippet(snippetId, input);
      showToast($t.messages.promptSnippetUpdated);
    } catch (error) {
      showError(error);
    }
  }

  async function deletePromptSnippet(snippet: PromptSnippet) {
    const confirmed = await confirmStore.confirm({
      title: $t.confirm.deleteSnippetTitle,
      message: $t.confirm.deleteSnippetMessage(snippet.title),
      confirmLabel: $t.common.delete,
      cancelLabel: $t.confirm.cancel,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
    try {
      await promptSnippetsStore.deleteSnippet(snippet.id);
      showToast($t.messages.promptSnippetDeleted);
    } catch (error) {
      showError(error);
    }
  }

  function usePromptSnippet(snippet: PromptSnippet) {
    promptForm.prompt = snippet.prompt;
    closePromptSnippetsDrawer();
    showToast($t.messages.promptSnippetLoaded);
  }

  async function copyPromptSnippet(snippet: PromptSnippet) {
    await copyText(snippet.prompt);
    showToast($t.messages.promptSnippetCopied);
  }

  function applyImagePrompt(prompt: string) {
    promptForm.prompt = prompt;
    showToast($t.messages.aiAssistantPromptApplied);
  }

  async function saveImagePrompt(prompt: string) {
    try {
      await promptSnippetsStore.createSnippet({
        title: $t.imagePrompt.snippetTitle,
        prompt,
        favorite: true
      });
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
      throw error;
    }
  }

  async function copyImagePrompt(prompt: string) {
    try {
      await copyText(prompt);
      showToast($t.messages.promptSnippetCopied);
    } catch (error) {
      showError(error);
    }
  }

  async function optimizePrompt() {
    const originalPrompt = promptForm.prompt;
    const prompt = originalPrompt.trim();
    if (!prompt || optimizingPrompt) return;
    if (!optimizerAvailable) {
      showToast($t.messages.promptOptimizerUnavailable, 'error');
      return;
    }

    optimizingPrompt = true;
    try {
      const response = await apiFetch<PromptOptimizeResponse>(
        '/api/prompt/optimize',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            buildPromptOptimizeRequest({
              prompt,
              targetLanguage: $language,
              apiPath: promptForm.apiPath,
              model: promptForm.model,
              size: promptForm.size,
              quality: promptForm.quality
            })
          )
        },
        'optimizing prompt'
      );
      promptForm.prompt = response.optimized_prompt;
      showToast($t.messages.promptOptimized, 'status', {
        actionLabel: $t.common.undo,
        onAction: () => {
          promptForm.prompt = originalPrompt;
        },
        durationMs: 6000
      });
    } catch (error) {
      showError(error, $t.messages.promptOptimizeFailed);
    } finally {
      optimizingPrompt = false;
    }
  }

  function applyOptimizedPrompt(prompt: string) {
    promptForm.prompt = prompt;
    showToast($t.messages.promptOptimized);
  }

  async function applyAssistantPrompt(prompt: string) {
    promptForm.prompt = prompt;
    showToast($t.messages.aiAssistantPromptApplied);
  }

  async function insertAssistantPrompt(prompt: string) {
    const currentPrompt = promptForm.prompt.trimEnd();
    promptForm.prompt = currentPrompt ? `${currentPrompt}\n${prompt}` : prompt;
    showToast($t.messages.aiAssistantPromptInserted);
  }

  async function saveAssistantSnippet(prompt: string) {
    try {
      await promptSnippetsStore.createSnippet({
        title: $t.aiAssistant.snippetTitle,
        prompt,
        favorite: true
      });
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
    }
  }

  function applyAssistantParams(recommendation: AssistantRecommendParamsResponse) {
    const updates: Partial<PromptFormState> = {};
    if (recommendation.model_name?.trim()) updates.model = recommendation.model_name.trim();
    if (promptForm.apiPath === '/v1/images/generations') {
      if (recommendation.size?.trim()) updates.size = recommendation.size.trim();
      if (recommendation.quality) updates.quality = recommendation.quality;
      if (recommendation.output_format) updates.outputFormat = recommendation.output_format;
      if (recommendation.n) updates.quantity = recommendation.n;
    }
    if (!Object.keys(updates).length) return;
    promptForm.patch(updates);
    showToast($t.messages.aiAssistantParamsApplied);
  }

  function regenerate() {
    previewStore.regenerate(
      (next) => promptForm.replace({ ...next, model: next.model.trim() || promptForm.presetDefaultModel || initialPromptFormState.model }),
      generateImage,
      editImage
    );
  }

  function clearPreview() {
    previewStore.clearPreview(jobsStore.closeActiveJobSource);
  }

  function openGalleryEditDialog(image: GalleryEntry) {
    void editGalleryDialogPanel.prefetch();
    galleryEditImage = image;
    galleryEditDialogOpen = true;
  }

  function closeGalleryEditDialog() {
    galleryEditDialogOpen = false;
    galleryEditImage = null;
  }

  function focusPromptAfterGalleryEdit() {
    if (typeof window === 'undefined') return;
    window.setTimeout(() => {
      const prompt = document.getElementById('prompt');
      if (!(prompt instanceof HTMLElement)) return;
      prompt.scrollIntoView({ behavior: 'smooth', block: 'center' });
      prompt.focus();
    }, 0);
  }

  function applyGalleryEditChoice(reusePrompt: boolean) {
    const image = galleryEditImage;
    if (!image) return;
    const nextLabel = $t.messages.galleryEditLabel(image.filename);
    if (!editSourceStore.setGallerySource(image.id, nextLabel, imageUrl(image.filename, image.image_url), nextLabel, previewStore.setError)) {
      showToast($t.messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES), 'error');
      closeGalleryEditDialog();
      return;
    }
    const nextForm = galleryEntryToEditForm(image, promptForm.presetDefaultModel, promptForm.apiPath);
    promptForm.replace(reusePrompt ? nextForm : { ...nextForm, prompt: '' });
    closeGalleryEditDialog();
    closeLightbox();
    focusPromptAfterGalleryEdit();
    showToast($t.messages.galleryImageReady);
  }

  function handleEditFile(event: Event) {
    editSourceStore.handleFile(event, previewStore.setError);
  }

  function handleEditFiles(files: File[]) {
    const addedCount = editSourceStore.addFiles(files, previewStore.setError);
    if (addedCount > 0) showToast($t.messages.editSourceFromDropAdded);
  }

  async function openEditPreview(sourceId: string) {
    rememberPanelFocus('editPreview');
    const upload = $editSourceStore.files.find((source) => source.id === sourceId);
    if (upload) {
      editPreviewUrl = upload.previewUrl;
      editPreviewLabel = upload.previewLabel;
      if (await ensurePanel('editPreview')) setUi('editPreviewOpen', true);
      return;
    }
    if ($editSourceStore.selectedGalleryImageId === sourceId && $editSourceStore.galleryPreviewUrl) {
      editPreviewUrl = $editSourceStore.galleryPreviewUrl;
      editPreviewLabel = $editSourceStore.galleryPreviewLabel || $editSourceStore.galleryLabel;
      if (await ensurePanel('editPreview')) setUi('editPreviewOpen', true);
    }
  }

  function closeEditPreview() {
    setUi('editPreviewOpen', false);
    editPreviewUrl = '';
    editPreviewLabel = '';
    restorePanelFocus('editPreview');
  }

  async function openMaskEditor(sourceId: string) {
    if (sourceId !== primaryEditSourceId($editSourceStore)) return;
    rememberPanelFocus('maskEditor');
    if (!(await ensurePanel('maskEditor'))) return;
    setUi('maskEditorOpen', true);
  }

  function closeMaskEditor() {
    closeUiPanel('maskEditor', 'maskEditorOpen');
  }

  function applyEditMask(mask: EditMask) {
    editSourceStore.setMask(mask);
    showToast($t.messages.editMaskApplied((mask.coverage * 100).toFixed(1)));
    closeMaskEditor();
  }

  function removeEditMask() {
    editSourceStore.removeMask();
  }

  function clearEditSource() {
    editSourceStore.clear();
    editPicker?.reset();
    closeEditPreview();
  }

  function removeEditSource(sourceId: string) {
    const upload = $editSourceStore.files.find((source) => source.id === sourceId);
    const isGallery = $editSourceStore.selectedGalleryImageId === sourceId;
    const label = upload?.label || (isGallery ? $editSourceStore.galleryLabel || $editSourceStore.galleryPreviewLabel : '');
    const previewUrl = upload?.previewUrl || (isGallery ? $editSourceStore.galleryPreviewUrl : '');
    if (!editSourceStore.remove(sourceId)) return;
    if (previewUrl && editPreviewUrl === previewUrl) closeEditPreview();
    showToast($t.messages.editSourceRemoved(label));
  }

  async function batchFavoriteGallery(favorite: boolean) {
    await galleryStore.batchFavorite(favorite, showToast, (ids, nextFavorite) => {
      ids.forEach((id) => lightboxStore.updateFavorite(id, nextFavorite));
    });
  }

  async function batchDeleteGallery() {
    await galleryStore.batchDelete(showToast, (ids) => {
      if ($lightboxStore.image && ids.includes($lightboxStore.image.id)) closeLightbox();
      if ($editSourceStore.selectedGalleryImageId && ids.includes($editSourceStore.selectedGalleryImageId)) {
        editSourceStore.clearGallerySource($editSourceStore.selectedGalleryImageId);
        closeEditPreview();
      }
    });
  }

  async function batchAnalyzeGallery() {
    const batchBody = galleryStore.selectedBatchRequestBody();
    const selectedCount = $galleryStore.selectionToken?.count || $galleryStore.selectedIds.size;
    if (!selectedCount) return;
    galleryActivityStore.setOperationStatus({
      kind: 'ai_analyze',
      label: $t.gallery.aiAnalyzing,
      detail: $t.gallery.aiAnalyzePreparing(selectedCount),
      progress: 0
    });
    try {
      const job = await assistantStore.batchAnalyzeGallery({ ...batchBody, target_language: $language });
      const currentJob = await waitForGalleryAnalysis(
        job,
        (nextJob) => {
          galleryActivityStore.setOperationStatus({
            kind: 'ai_analyze',
            label: $t.gallery.aiAnalyzing,
            detail: $t.gallery.aiAnalyzeProgress(
              nextJob.analyzed_count,
              nextJob.requested_count,
              nextJob.failed_count,
              nextJob.missing_count
            ),
            progress: nextJob.progress
          });
        }
      );
      galleryActivityStore.setOperationStatus({
        kind: 'ai_analyze',
        label: $t.gallery.aiAnalyzing,
        detail: $t.gallery.aiAnalyzeComplete(currentJob.analyzed_count, currentJob.failed_count, currentJob.missing_count),
        progress: 100
      });
      galleryStore.clearSelection();
      showToast(
        $t.gallery.aiAnalyzeComplete(currentJob.analyzed_count, currentJob.failed_count, currentJob.missing_count),
        currentJob.failed_count || currentJob.missing_count ? 'error' : 'status'
      );
    } catch (error) {
      showError(error);
    } finally {
      galleryActivityStore.setOperationStatus(null);
    }
  }

  function uploadGalleryImageToNodeImage(image: GalleryEntry) {
    void nodeImageResultPanel.prefetch();
    void galleryStore.uploadToNodeImage(image, showToast);
  }

  function batchUploadGalleryToNodeImage() {
    void nodeImageResultPanel.prefetch();
    void galleryStore.batchUploadToNodeImage(showToast);
  }

  async function toggleFavorite(image: GalleryEntry) {
    await galleryStore.toggleFavorite(image, (next) => {
      if ($lightboxStore.image?.id === image.id) lightboxStore.open(next);
    });
  }

  async function deleteImage(image: GalleryEntry) {
    await galleryStore.deleteImage(
      image,
      showToast,
      () => {
        if ($lightboxStore.image?.id === image.id) closeLightbox();
      },
      () => {
        if ($editSourceStore.selectedGalleryImageId === image.id) {
          editSourceStore.clearGallerySource(image.id);
          closeEditPreview();
        }
      }
    );
  }

  async function deleteAllImages() {
    await galleryStore.deleteAll(showToast, () => {
      closeLightbox();
      editSourceStore.clearGallerySource($editSourceStore.selectedGalleryImageId);
      closeEditPreview();
      clearPreview();
    });
  }

  async function importArchive(file: File) {
    await galleryStore.importArchive(file, showToast);
  }

  async function exportArchive() {
    await galleryStore.exportArchive(showToast);
  }

  async function syncGallery() {
    if (!r2BackupAvailable) {
      showToast($t.messages.r2BackupUnavailable, 'error');
      return;
    }
    try {
      await galleryStore.syncGallery(showToast);
    } catch (error) {
      showError(error);
    }
  }

  async function copyPrompt(image: GalleryEntry) {
    await copyText(image.prompt);
    showToast($t.messages.promptCopied);
  }

  async function copyImageUrl(image: GalleryEntry) {
    await copyText(new URL(imageUrl(image.filename, image.image_url), window.location.origin).href);
    showToast($t.messages.imageUrlCopied);
  }

  function copyPromptBestEffort(prompt: string) {
    if (!prompt) return;
    void copyText(prompt).catch(() => {});
  }

  function useGalleryPrompt(image: GalleryEntry) {
    promptForm.replace(galleryEntryToPromptOnly(image, promptForm.snapshot()));
    copyPromptBestEffort(image.prompt);
    closeLightbox();
    showToast($t.messages.galleryPromptLoaded);
  }

  function useGalleryParams(image: GalleryEntry) {
    const ignoredEditPath = image.api_path === '/v1/images/edits';
    promptForm.replace(galleryEntryToPromptForm(image, promptForm.presetDefaultModel, promptForm.apiPath));
    copyPromptBestEffort(image.prompt);
    closeLightbox();
    showToast(ignoredEditPath ? $t.messages.galleryEditApiPathIgnored : $t.messages.galleryParamsLoaded);
  }

  function useJobAsPrompt(job: GenerateJobStatus) {
    promptForm.replace(jobToPromptForm(job, promptForm.presetDefaultModel));
    closeJobsDrawer();
    showToast($t.messages.jobLoadedIntoPrompt);
  }

  async function clearJobHistory() {
    const confirmed = await confirmStore.confirm({
      title: $t.confirm.clearJobHistoryTitle,
      message: $t.confirm.clearJobHistoryMessage,
      details: [$t.confirm.clearJobHistoryDetail],
      confirmLabel: $t.common.clear,
      cancelLabel: $t.confirm.cancel,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;

    try {
      await jobsStore.clearJobHistory();
      showToast($t.messages.jobHistoryCleared);
    } catch (error) {
      showError(error);
    }
  }

  function retryJob(job: GenerateJobStatus) {
    promptForm.replace(jobToPromptForm(job, promptForm.presetDefaultModel));
    closeJobsDrawer();
    if (job.operation === 'edit') {
      if (!$editSourceStore.files.length && !$editSourceStore.selectedGalleryImageId) {
        previewStore.setError($t.messages.editRetryNeedsSource);
        showToast($t.messages.editRetryNeedsSource, 'error');
        return;
      }
      if (job.mask_applied && !isMaskValid($editSourceStore)) {
        showToast($t.messages.editRetryMaskMissing);
      }
      editImage();
      return;
    }
    generateImage();
  }

  async function diagnoseJob(job: GenerateJobStatus) {
    try {
      const diagnosis = await assistantStore.diagnoseJob(job.job_id);
      jobDiagnoses = { ...jobDiagnoses, [job.job_id]: diagnosis };
    } catch (error) {
      showError(error);
    }
  }

  onMount(() => {
    accessStore.installUnauthorizedHandler();
    const initialData = loadAuthenticatedData();
    void initialData.catch(() => undefined);
    void accessStore.checkAccess(() => initialData);
    // Loaded in its own chunk right after hydration so it stays out of the
    // homepage dependency graph without leaving a long-lived gap.
    void aiAssistantPanel.prefetch();
    void galleryGridPanel.prefetch();

    const popstate = () => {
      void applyUrlStateToApp();
    };

    const isEditableTarget = (target: EventTarget | null) => {
      const element = target instanceof HTMLElement ? target : null;
      return Boolean(element?.closest('input, textarea') || element?.isContentEditable);
    };

    const isBlockingDialogOpen = () =>
      $uiStore.settingsOpen ||
      $uiStore.promptSnippetsOpen ||
      $uiStore.imagePromptOpen ||
      $uiStore.sizeDialogOpen ||
      $uiStore.maskEditorOpen ||
      galleryEditDialogOpen ||
      Boolean($lightboxStore.image);

    const paste = (event: ClipboardEvent) => {
      if (isEditableTarget(event.target) || isBlockingDialogOpen()) return;

      const files = extractImageFilesFromClipboard(event.clipboardData);
      if (!files.length) return;

      event.preventDefault();
      const addedCount = editSourceStore.addFiles(files, previewStore.setError);
      if (addedCount > 0) showToast($t.messages.editSourceFromClipboardAdded);
    };

    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if ($uiStore.imagePromptOpen) closeImagePromptDialog();
        else if ($uiStore.editPreviewOpen) closeEditPreview();
        else if ($lightboxStore.image) closeLightbox();
        else if ($uiStore.sizeDialogOpen) setUi('sizeDialogOpen', false);
        return;
      }

      if (!$lightboxStore.image || lightboxNavigationBlocked()) return;
      if (event.key === 'ArrowLeft' && $lightboxController.canNavigatePrevious) {
        event.preventDefault();
        void lightboxController.navigate(-1);
      } else if (event.key === 'ArrowRight' && $lightboxController.canNavigateNext) {
        event.preventDefault();
        void lightboxController.navigate(1);
      }
    };
    return installWorkspaceLifecycle({
      onPopstate: popstate,
      onKeydown: keydown,
      onPaste: paste,
      shouldPrefetch: canPrefetchNonCritical,
      prefetchCommonPanels: () => {
        prefetchPanel('settings');
        prefetchPanel('jobs');
        prefetchPanel('snippets');
      },
      cleanup: () => {
        if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
        gallerySuccessRefreshTimer = null;
        urlSync.destroy();
        jobsStore.cleanup();
        galleryStore.cleanup();
        nodeImageResult.clear();
        previewStore.cleanup();
        uiStore.cleanup();
        lightboxController.destroy();
        resetPanels();
        optimizingPrompt = false;
      }
    });
  });
</script>

<svelte:head>
  <title>GPT Image Panel</title>
</svelte:head>

<a class="skip-link control-focus" href="#main-content">{$t.common.skipToMain}</a>

<AccessGate
  visible={$accessStore.gateVisible}
  error={$accessStore.error}
  loading={$accessStore.loading}
  turnstileEnabled={$accessStore.turnstileEnabled}
  turnstileSiteKey={$accessStore.turnstileSiteKey}
  onUnlock={(key, token) => accessStore.unlockAccess(key, token, loadAuthenticatedData)}
/>
<Header
  version={$versionStore.version}
  latestVersion={$versionStore.latestVersion}
  hasVersionUpdate={$versionStore.hasUpdate}
  releaseUrl={$versionStore.releaseUrl}
  {activeJobsCount}
  promptSnippetsOpen={$uiStore.promptSnippetsOpen}
  imagePromptOpen={$uiStore.imagePromptOpen}
  jobsOpen={$uiStore.jobsOpen}
  settingsOpen={$uiStore.settingsOpen}
  onOpenPromptSnippets={openPromptSnippetsDrawer}
  onOpenImagePrompt={openImagePromptDialog}
  onOpenJobs={openJobsDrawer}
  onOpenSettings={() => void openUiPanel('settings', 'settingsOpen')}
  onPrefetchPromptSnippets={() => prefetchPanel('snippets')}
  onPrefetchImagePrompt={() => prefetchPanel('imagePrompt')}
  onPrefetchJobs={() => prefetchPanel('jobs')}
  onPrefetchSettings={() => prefetchPanel('settings')}
/>

<ConfirmDialog request={$confirmStore.request} />
{#if $nodeImageResultPanel.component}
  {@const Panel = $nodeImageResultPanel.component}
  <Panel />
{/if}

{#if $imagePromptPanel.component}
{@const Panel = $imagePromptPanel.component}
<Panel
  open={$uiStore.imagePromptOpen}
  available={aiAssistantAvailable}
  onClose={closeImagePromptDialog}
  onApply={applyImagePrompt}
  onSave={saveImagePrompt}
  onCopy={copyImagePrompt}
/>
{/if}

{#if $settingsPanel.component}
{@const Panel = $settingsPanel.component}
<Panel
  open={$uiStore.settingsOpen}
  settings={$settingsStore.settings}
  saving={$settingsActivityStore.saving}
  health={$settingsActivityStore.health}
  healthChecking={$settingsActivityStore.healthChecking}
  r2Health={$settingsActivityStore.r2Health}
  r2HealthChecking={$settingsActivityStore.r2HealthChecking}
  promptOptimizerHealth={$settingsActivityStore.promptOptimizerHealth}
  promptOptimizerHealthChecking={$settingsActivityStore.promptOptimizerHealthChecking}
  aiAssistantHealth={$settingsActivityStore.aiAssistantHealth}
  aiAssistantHealthChecking={$settingsActivityStore.aiAssistantHealthChecking}
  onClose={() => closeUiPanel('settings', 'settingsOpen')}
  onSave={saveSettings}
  onCreate={createPreset}
  onActivate={activatePreset}
  onDelete={deletePreset}
  onHealthCheck={checkPresetHealth}
  onClearPresetHealth={clearPresetHealth}
  onR2HealthCheck={checkR2Health}
  onPromptOptimizerHealthCheck={checkPromptOptimizerHealth}
  onClearPromptOptimizerHealth={clearPromptOptimizerHealth}
  onAiAssistantHealthCheck={checkAiAssistantHealth}
  onClearAiAssistantHealth={clearAiAssistantHealth}
  onLoadPromptOptimizerSystemPrompt={loadPromptOptimizerSystemPrompt}
  onSavePromptOptimizerSystemPrompt={savePromptOptimizerSystemPrompt}
  onLoadOverallConfig={loadOverallConfig}
  onSaveOverallConfig={saveOverallConfig}
/>
{/if}

{#if $snippetsPanel.component}
{@const Panel = $snippetsPanel.component}
<Panel
  open={$uiStore.promptSnippetsOpen}
  snippets={$promptSnippetsStore.snippets}
  loading={$promptSnippetsStore.loading}
  saving={$promptSnippetsStore.saving}
  onClose={closePromptSnippetsDrawer}
  onSearch={loadPromptSnippets}
  onCreate={createPromptSnippet}
  onUpdate={updatePromptSnippet}
  onDelete={deletePromptSnippet}
  onUse={usePromptSnippet}
  onCopy={copyPromptSnippet}
/>
{/if}

{#if $jobsPanel.component}
{@const Panel = $jobsPanel.component}
<Panel
  open={$uiStore.jobsOpen}
  activeTab={jobsTab}
  jobs={$jobsStore.jobs}
  historyJobs={$jobsStore.historyJobs}
  historyLoading={$jobsStore.historyLoading}
  historyLoaded={$jobsStore.historyLoaded}
  historyHasMore={$jobsStore.historyHasMore}
  historyFailedOnly={$jobsStore.historyFailedOnly}
  selectedIds={$jobsStore.selectedIds}
  onClose={closeJobsDrawer}
  onTabChange={setJobsTab}
  onRefresh={jobsStore.loadJobs}
  onRefreshHistory={jobsStore.loadJobHistory}
  onLoadMoreHistory={jobsStore.loadMoreJobHistory}
  onHistoryFailedOnlyChange={jobsStore.setHistoryFailedOnly}
  onClearHistory={clearJobHistory}
  onToggle={jobsStore.toggleSelection}
  onToggleAll={jobsStore.toggleAll}
  onCancelSelected={jobsStore.cancelSelected}
  onUseJob={useJobAsPrompt}
  onRetryJob={retryJob}
  aiAssistantEnabled={aiAssistantAvailable}
  diagnosingJobId={$assistantStore.diagnoseLoadingJobId}
  diagnoses={jobDiagnoses}
  onDiagnoseJob={diagnoseJob}
/>
{/if}

<main id="main-content" tabindex="-1" class:optimizer-gutter={optimizerAssistantEnabled} class="mx-auto max-w-5xl space-y-6 px-4 py-6 pb-28 sm:px-6 sm:pb-32">
  <ToastHost toast={$toastStore} />

  <PromptForm
    loading={$previewStore.loading}
    optimizing={optimizingPrompt}
    optimizerEnabled={optimizerAvailable}
    editPlannerEnabled={aiAssistantAvailable}
    editPlanning={$assistantStore.editPlanLoading}
    onSubmit={submitPrompt}
    {hasEditSource}
    onPlanEdit={planEdit}
    onOptimize={optimizePrompt}
    onAppendPromptTag={appendPromptTag}
    onOpenSize={() => void openUiPanel('size', 'sizeDialogOpen')}
  >
    {#snippet editSource()}
      <EditSourcePicker
        bind:this={editPicker}
        sources={editSources}
        onChange={handleEditFile}
        onDropFiles={handleEditFiles}
        onPreview={openEditPreview}
        onRemove={removeEditSource}
        onClear={clearEditSource}
        onEditMask={openMaskEditor}
        onRemoveMask={removeEditMask}
      />
    {/snippet}
  </PromptForm>

  {#if $aiAssistantPanel.component}
    {@const Panel = $aiAssistantPanel.component}
    <Panel
      enabled={aiAssistantAvailable}
      optimizerEnabled={optimizerAvailable}
      loading={$assistantStore.promptLoading || $assistantStore.paramsLoading}
      onApplyPrompt={applyAssistantPrompt}
      onInsertPrompt={insertAssistantPrompt}
      onSaveSnippet={saveAssistantSnippet}
      onApplyParams={applyAssistantParams}
    />
  {/if}

  <PreviewPanel onRegenerate={regenerate} onClear={clearPreview} />

  {#if $galleryGridPanel.component}
    {@const Panel = $galleryGridPanel.component}
    <Panel
      canSyncR2={r2BackupAvailable}
      canNodeImageUpload={nodeImageAvailable}
      onFilter={setGalleryFilter}
      onResetFilters={resetGalleryFilters}
      onPage={loadGalleryPage}
      onLoadStats={loadGalleryStats}
      onFavorite={toggleFavorite}
      onDelete={deleteImage}
      onDeleteAll={deleteAllImages}
      onImport={importArchive}
      onExport={exportArchive}
      onSync={syncGallery}
      onOpen={openLightbox}
      onEdit={openGalleryEditDialog}
      onUsePrompt={useGalleryPrompt}
      onUseAll={useGalleryParams}
      onNodeImageUpload={uploadGalleryImageToNodeImage}
      onBatchDelete={batchDeleteGallery}
      onBatchFavorite={batchFavoriteGallery}
      onBatchNodeImageUpload={batchUploadGalleryToNodeImage}
      canAiAnalyze={aiAssistantAvailable}
      onBatchAiAnalyze={batchAnalyzeGallery}
    />
  {/if}
</main>

{#if $optimizerPanel.component}
{@const Panel = $optimizerPanel.component}
<Panel
  enabled={optimizerAssistantEnabled}
  onApplyPrompt={applyOptimizedPrompt}
/>
{/if}

{#if $lightboxPanel.component}
{@const Panel = $lightboxPanel.component}
<Panel
  open={Boolean($lightboxStore.image)}
  image={$lightboxStore.image}
  onClose={closeLightbox}
  onEdit={openGalleryEditDialog}
  onFavorite={toggleFavorite}
  onDelete={deleteImage}
  onCopyPrompt={copyPrompt}
  onCopyUrl={copyImageUrl}
  onUsePrompt={useGalleryPrompt}
  onUseAll={useGalleryParams}
  canNavigatePrevious={$lightboxController.canNavigatePrevious}
  canNavigateNext={$lightboxController.canNavigateNext}
  navigating={$lightboxController.navigating}
  aiAssistantEnabled={aiAssistantAvailable}
  aiMetadata={$lightboxController.aiMetadata}
  aiLoadingImageId={$assistantStore.galleryLoadingImageId}
  onAiDescribe={lightboxController.describe}
  onAiAnalyze={lightboxController.analyze}
  onNavigatePrevious={() => lightboxController.navigate(-1)}
  onNavigateNext={() => lightboxController.navigate(1)}
/>
{/if}

{#if $editPreviewPanel.component}
{@const Panel = $editPreviewPanel.component}
<Panel
  open={$uiStore.editPreviewOpen}
  url={editPreviewUrl}
  label={editPreviewLabel}
  onClose={closeEditPreview}
/>
{/if}

{#if $maskEditorPanel.component && primaryEditSource}
  {@const Panel = $maskEditorPanel.component}
  <Panel
    open={$uiStore.maskEditorOpen}
    sourceId={primaryEditSource.id}
    imageUrl={primaryEditSource.previewUrl}
    label={primaryEditSource.label}
    size={promptForm.size}
    existingMask={activeEditMask}
    onApply={applyEditMask}
    onClose={closeMaskEditor}
    onError={(message: string) => showToast(message, 'error')}
  />
{/if}

{#if $editGalleryDialogPanel.component}
  {@const Panel = $editGalleryDialogPanel.component}
  <Panel
    open={galleryEditDialogOpen}
    image={galleryEditImage}
    onChoose={applyGalleryEditChoice}
    onClose={closeGalleryEditDialog}
  />
{/if}

{#if $sizePanel.component}
  {@const Panel = $sizePanel.component}
  <Panel open={$uiStore.sizeDialogOpen} onClose={() => closeUiPanel('size', 'sizeDialogOpen')} />
{/if}

{#if $loadingPanel}
  <div class="lazy-panel-status" role="status" aria-live="polite">
    <span class="spinner" aria-hidden="true"></span>
    <span>{$t.common.loadingFeature}</span>
  </div>
{/if}
