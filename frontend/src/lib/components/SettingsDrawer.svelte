<script lang="ts">
  import { t } from '$lib/i18n';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { AIAssistantSettingsInput, ApiPreset, AssistantHealthResponse, OverallConfigItem, OverallConfigResponse, OverallConfigUpdateRequest, PromptOptimizerHealthResponse, PresetHealthResponse, PromptOptimizerSystemPromptResponse, R2BackupSettingsInput, R2HealthResponse, SettingsInput, SettingsResponse } from '$lib/api/types/settings';
  import { confirmStore } from '$lib/stores/confirm';
  import { RESPONSE_FORMAT_OPTIONS, normalizeResponseFormat } from '$lib/utils/promptForm';
  import {
    MASKED_API_KEY_VALUE,
    aiAssistantPayload,
    buildSettingsPayload,
    hasSettingsChanges,
    promptOptimizerTimeoutValue,
    r2BackupPayload,
    r2SyncIntervalHoursValue,
    secretDraftValue,
    type SettingsDraft
  } from '$lib/features/settings/draft';
  import Overlay from '$lib/components/Overlay.svelte';
  import HealthResults from '$lib/components/settings/HealthResults.svelte';
  import PresetSettingsEditor from '$lib/components/settings/PresetSettingsEditor.svelte';
  import OverallConfigDialog from '$lib/components/settings/OverallConfigDialog.svelte';
  import SystemPromptDialog from '$lib/components/settings/SystemPromptDialog.svelte';

  export let open = false;
  export let settings: SettingsResponse | null = null;
  export let saving = false;
  export let health: PresetHealthResponse | null = null;
  export let healthChecking = false;
  export let r2Health: R2HealthResponse | null = null;
  export let r2HealthChecking = false;
  export let promptOptimizerHealth: PromptOptimizerHealthResponse | null = null;
  export let promptOptimizerHealthChecking = false;
  export let aiAssistantHealth: AssistantHealthResponse | null = null;
  export let aiAssistantHealthChecking = false;
  export let onClose: () => void = () => {};
  export let onSave: (body: SettingsInput) => Promise<void> | void = () => {};
  export let onCreate: () => Promise<void> | void = () => {};
  export let onActivate: (presetId: string) => Promise<void> | void = () => {};
  export let onDelete: (presetId: string) => Promise<void> | void = () => {};
  export let onHealthCheck: (presetId: string) => Promise<void> | void = () => {};
  export let onClearPresetHealth: () => void = () => {};
  export let onR2HealthCheck: (body: R2BackupSettingsInput) => Promise<void> | void = () => {};
  export let onPromptOptimizerHealthCheck: () => Promise<void> | void = () => {};
  export let onClearPromptOptimizerHealth: () => void = () => {};
  export let onAiAssistantHealthCheck: (body: AIAssistantSettingsInput) => Promise<void> | void = () => {};
  export let onClearAiAssistantHealth: () => void = () => {};
  export let onLoadPromptOptimizerSystemPrompt: () => Promise<PromptOptimizerSystemPromptResponse> = async () => ({
    system_prompt: '',
    default_system_prompt: '',
    customized: false
  });
  export let onSavePromptOptimizerSystemPrompt: (systemPrompt: string) => Promise<PromptOptimizerSystemPromptResponse> = async (
    systemPrompt
  ) => ({
    system_prompt: systemPrompt,
    default_system_prompt: '',
    customized: true
  });
  export let onLoadOverallConfig: () => Promise<OverallConfigResponse> = async () => ({
    items: [],
    restart_required_names: []
  });
  export let onSaveOverallConfig: (body: OverallConfigUpdateRequest) => Promise<OverallConfigResponse> = async () => ({
    items: [],
    restart_required_names: []
  });

  let activePresetId = '';
  let presetName = '';
  let apiUrl = '';
  let defaultModel = '';
  let defaultResponseFormat: ResponseFormatDefault = 'url';
  let apiKey = '';
  let apiPath: ApiPath = '/v1/images/generations';
  let upstreamSocks5Proxy = '';
  let webhookUrl = '';
  let apiKeyInputType = 'password';
  let activatingPresetId = '';
  let promptOptimizerEnabled = false;
  let promptOptimizerApiUrl = '';
  let promptOptimizerModel = 'gpt-4o-mini';
  let promptOptimizerTimeoutSeconds: number | string = 60;
  let promptOptimizerApiKey = '';
  let promptOptimizerApiKeyInputType = 'password';
  let aiAssistantEnabled = false;
  let aiAssistantVisionModel = 'gpt-4o-mini';
  let r2BackupEnabled = false;
  let r2EndpointUrl = '';
  let r2BucketName = '';
  let r2Region = 'auto';
  let r2KeyPrefix = 'gallery/';
  let r2SyncIntervalHours: number | string = 0;
  let r2AccessKeyId = '';
  let r2SecretAccessKey = '';
  let r2AccessKeyIdInputType = 'password';
  let r2SecretAccessKeyInputType = 'password';
  let nodeImageEnabled = false;
  let nodeImageApiKey = '';
  let systemPromptOpen = false;
  let systemPromptLoading = false;
  let systemPromptSaving = false;
  let systemPromptText = '';
  let systemPromptError = '';
  let overallConfigOpen = false;
  let overallConfigLoading = false;
  let overallConfigSaving = false;
  let overallConfigError = '';
  let overallConfigItems: OverallConfigItem[] = [];
  let overallConfigDraft: Record<string, string | boolean | number> = {};
  let overallConfigClears: Record<string, boolean> = {};
  let systemPromptInitialText = '';

  $: activePreset = settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null;
  $: if (settings && activePreset) {
    activePresetId = settings.active_preset_id;
    presetName = activePreset.name || '';
    apiUrl = activePreset.api_url || settings.api_url || '';
    defaultModel = activePreset.default_model || settings.default_model || 'gpt-image-2';
    apiKey =
      activePreset.api_key_source === 'registry' && activePreset.api_key_secret_id
        ? activePreset.api_key_secret_id
        : activePreset.has_api_key || settings.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    apiPath = activePreset.api_path || settings.api_path || '/v1/images/generations';
    defaultResponseFormat = normalizeResponseFormat(activePreset.default_response_format ?? settings.default_response_format, 'url');
    upstreamSocks5Proxy = settings.has_upstream_socks5_proxy ? settings.upstream_socks5_proxy_masked : '';
    webhookUrl = settings.has_webhook_url ? settings.webhook_url_masked : '';
    promptOptimizerEnabled = Boolean(settings.prompt_optimizer?.enabled);
    promptOptimizerApiUrl = settings.prompt_optimizer?.api_url || '';
    promptOptimizerModel = settings.prompt_optimizer?.model || 'gpt-4o-mini';
    promptOptimizerTimeoutSeconds = settings.prompt_optimizer?.timeout_seconds || 60;
    promptOptimizerApiKey =
      settings.prompt_optimizer?.api_key_source === 'registry' && settings.prompt_optimizer.api_key_secret_id
        ? settings.prompt_optimizer.api_key_secret_id
        : settings.prompt_optimizer?.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    aiAssistantEnabled = Boolean(settings.ai_assistant?.enabled);
    aiAssistantVisionModel = settings.ai_assistant?.vision_model || settings.prompt_optimizer?.model || 'gpt-4o-mini';
    r2BackupEnabled = Boolean(settings.r2_backup?.enabled);
    r2EndpointUrl = settings.r2_backup?.endpoint_url || '';
    r2BucketName = settings.r2_backup?.bucket_name || '';
    r2Region = settings.r2_backup?.region || 'auto';
    r2KeyPrefix = settings.r2_backup?.key_prefix || 'gallery/';
    r2SyncIntervalHours = settings.r2_backup?.sync_interval_hours ?? 0;
    r2AccessKeyId =
      settings.r2_backup?.access_key_id_source === 'registry' && settings.r2_backup.access_key_id_secret_id
        ? settings.r2_backup.access_key_id_secret_id
        : settings.r2_backup?.has_access_key_id
          ? MASKED_API_KEY_VALUE
          : '';
    r2SecretAccessKey =
      settings.r2_backup?.secret_access_key_source === 'registry' && settings.r2_backup.secret_access_key_secret_id
        ? settings.r2_backup.secret_access_key_secret_id
        : settings.r2_backup?.has_secret_access_key
          ? MASKED_API_KEY_VALUE
          : '';
    nodeImageEnabled = Boolean(settings.nodeimage?.enabled);
    nodeImageApiKey = secretDraftValue(
      settings.nodeimage?.api_key_source,
      settings.nodeimage?.has_api_key,
      settings.nodeimage?.api_key_env_var,
      settings.nodeimage?.api_key_secret_id
    );
  }
  $: apiKeyInputType = 'text';
  $: promptOptimizerApiKeyInputType = 'text';
  $: r2AccessKeyIdInputType = 'text';
  $: r2SecretAccessKeyInputType = 'text';
  $: if (!open && systemPromptOpen) {
    systemPromptOpen = false;
    systemPromptError = '';
  }
  $: if (!open && overallConfigOpen) {
    overallConfigOpen = false;
    overallConfigError = '';
  }
  $: overallConfigGroups = overallConfigItems.reduce(
    (groups, item) => {
      if (!groups[item.group]) groups[item.group] = [];
      groups[item.group].push(item);
      return groups;
    },
    {} as Record<string, OverallConfigItem[]>
  );
  $: overallConfigGroupNames = Object.keys(overallConfigGroups);
  $: settingsDraft = {
    activePresetId,
    presetName,
    apiUrl,
    defaultModel,
    defaultResponseFormat,
    apiKey,
    apiPath,
    upstreamSocks5Proxy,
    webhookUrl,
    promptOptimizerEnabled,
    promptOptimizerApiUrl,
    promptOptimizerModel,
    promptOptimizerTimeoutSeconds: promptOptimizerTimeoutValue(promptOptimizerTimeoutSeconds),
    promptOptimizerApiKey,
    aiAssistantEnabled,
    aiAssistantVisionModel,
    r2BackupEnabled,
    r2EndpointUrl,
    r2BucketName,
    r2Region,
    r2KeyPrefix,
    r2SyncIntervalHours: r2SyncIntervalHoursValue(r2SyncIntervalHours),
    r2AccessKeyId,
    r2SecretAccessKey,
    nodeImageEnabled,
    nodeImageApiKey
  } satisfies SettingsDraft;
  $: settingsDirty = Boolean(settings && activePreset) && hasSettingsChanges(settingsDraft, settings, activePreset);
  $: systemPromptDirty = systemPromptOpen && !systemPromptLoading && systemPromptText !== systemPromptInitialText;
  $: overallConfigDirty = overallConfigItems.some((item) => {
    if (overallConfigClears[item.name]) return item.has_override;
    if (!hasOverallDraft(item.name)) return false;
    return overallConfigDraft[item.name] !== item.value;
  });

  function normalizePromptOptimizerTimeout() {
    promptOptimizerTimeoutSeconds = promptOptimizerTimeoutValue(promptOptimizerTimeoutSeconds);
  }

  function normalizeR2SyncIntervalHours() {
    r2SyncIntervalHours = r2SyncIntervalHoursValue(r2SyncIntervalHours);
  }

  function closePromptOptimizerHealth() {
    onClearPromptOptimizerHealth();
  }

  function closeAiAssistantHealth() {
    onClearAiAssistantHealth();
  }

  function closePresetHealth() {
    onClearPresetHealth();
  }

  async function confirmDiscardChanges() {
    return confirmStore.confirm({
      title: $t.confirm.unsavedChangesTitle,
      message: $t.confirm.unsavedChangesMessage,
      confirmLabel: $t.common.discard,
      cancelLabel: $t.common.keepEditing,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
  }

  async function save() {
    await onSave(buildSettingsPayload(settingsDraft, settings));
  }

  function keyLabel(preset: ApiPreset) {
    if (preset.api_key_source === 'env') {
      return `${$t.settings.envRef}: ${preset.api_key_env_var || preset.api_key_masked}`;
    }
    return preset.has_api_key ? preset.api_key_masked : $t.common.noKey;
  }

  async function activateSelectedPreset(presetId: string) {
    if (!presetId || presetId === settings?.active_preset_id || activatingPresetId) return;
    activatingPresetId = presetId;
    try {
      await onActivate(presetId);
    } finally {
      activatingPresetId = '';
    }
  }

  async function checkHealth() {
    if (!activePresetId) return;
    await onHealthCheck(activePresetId);
  }

  async function checkR2Health() {
    await onR2HealthCheck(r2BackupPayload(settingsDraft));
  }

  async function checkPromptOptimizerHealth() {
    await onPromptOptimizerHealthCheck();
  }

  async function checkAiAssistantHealth() {
    await onAiAssistantHealthCheck(aiAssistantPayload(settingsDraft));
  }

  async function openSystemPromptEditor() {
    systemPromptOpen = true;
    systemPromptLoading = true;
    systemPromptError = '';
    try {
      const response = await onLoadPromptOptimizerSystemPrompt();
      systemPromptText = response.system_prompt;
      systemPromptInitialText = response.system_prompt;
    } catch (error) {
      systemPromptError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      systemPromptLoading = false;
    }
  }

  async function closeSystemPromptEditor() {
    if (systemPromptSaving) return;
    if (systemPromptDirty && !(await confirmDiscardChanges())) return;
    systemPromptOpen = false;
    systemPromptError = '';
  }

  async function saveSystemPrompt() {
    const nextSystemPrompt = systemPromptText.trim();
    if (!nextSystemPrompt) {
      systemPromptError = $t.settings.systemPromptRequired;
      return;
    }
    systemPromptSaving = true;
    systemPromptError = '';
    try {
      const response = await onSavePromptOptimizerSystemPrompt(nextSystemPrompt);
      systemPromptText = response.system_prompt;
      systemPromptInitialText = response.system_prompt;
      systemPromptOpen = false;
    } catch (error) {
      systemPromptError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      systemPromptSaving = false;
    }
  }

  function overallDraftValue(item: OverallConfigItem) {
    if (hasOverallDraft(item.name)) {
      return overallConfigDraft[item.name];
    }
    if (item.secret) return item.value_masked || '';
    return item.value;
  }

  function hasOverallDraft(name: string) {
    return Object.prototype.hasOwnProperty.call(overallConfigDraft, name);
  }

  function setOverallDraft(item: OverallConfigItem, value: string | boolean | number) {
    overallConfigDraft = { ...overallConfigDraft, [item.name]: value };
    overallConfigClears = { ...overallConfigClears, [item.name]: false };
  }

  async function openOverallConfigModal() {
    overallConfigOpen = true;
    overallConfigLoading = true;
    overallConfigError = '';
    try {
      const response = await onLoadOverallConfig();
      overallConfigItems = response.items;
      overallConfigDraft = {};
      overallConfigClears = {};
    } catch (error) {
      overallConfigError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      overallConfigLoading = false;
    }
  }

  async function closeOverallConfigModal() {
    if (overallConfigSaving) return;
    if (overallConfigDirty && !(await confirmDiscardChanges())) return;
    overallConfigOpen = false;
    overallConfigError = '';
    overallConfigDraft = {};
    overallConfigClears = {};
  }

  function resetOverallConfigItem(item: OverallConfigItem) {
    overallConfigClears = { ...overallConfigClears, [item.name]: true };
    const { [item.name]: _discard, ...nextDraft } = overallConfigDraft;
    overallConfigDraft = nextDraft;
  }

  async function saveOverallConfigModal() {
    const updates = overallConfigItems
      .map((item) => {
        if (overallConfigClears[item.name]) return { name: item.name, clear_override: true };
        if (hasOverallDraft(item.name)) {
          return { name: item.name, value: overallConfigDraft[item.name] };
        }
        return null;
      })
      .filter(Boolean) as OverallConfigUpdateRequest['updates'];
    if (!updates.length) {
      overallConfigOpen = false;
      return;
    }
    overallConfigSaving = true;
    overallConfigError = '';
    try {
      const response = await onSaveOverallConfig({ updates });
      overallConfigItems = response.items;
      overallConfigDraft = {};
      overallConfigClears = {};
      overallConfigOpen = false;
    } catch (error) {
      overallConfigError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      overallConfigSaving = false;
    }
  }

  async function requestCloseDrawer() {
    if (saving) return;
    if (systemPromptOpen) {
      await closeSystemPromptEditor();
      return;
    }
    if (overallConfigOpen) {
      await closeOverallConfigModal();
      return;
    }
    if (settingsDirty && !(await confirmDiscardChanges())) return;
    onClose();
  }

  function sourceLabel(source: OverallConfigItem['source']) {
    if (source === 'override') return $t.settings.overallConfigSourceOverride;
    if (source === 'env') return $t.settings.overallConfigSourceEnv;
    return $t.settings.overallConfigSourceDefault;
  }
</script>

<Overlay {open} variant="drawer" onClose={requestCloseDrawer} closeLabel={$t.settings.closeLabel} labelledBy="settings-drawer-title" z={50} panelId="settings-drawer">
      <div class="flex items-center justify-between border-b border-stone-200 p-5 dark:border-zinc-800">
        <div>
          <h2 id="settings-drawer-title" class="text-lg font-semibold text-stone-900 dark:text-zinc-100">{$t.settings.title}</h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.settings.closeLabel} on:click={requestCloseDrawer}>x</button>
      </div>

      <PresetSettingsEditor
        {settings}
        {activePresetId}
        {activatingPresetId}
        bind:presetName
        bind:apiUrl
        bind:apiPath
        bind:defaultModel
        bind:defaultResponseFormat
        bind:apiKey
        {apiKeyInputType}
        bind:upstreamSocks5Proxy
        bind:webhookUrl
        bind:r2BackupEnabled
        bind:r2EndpointUrl
        bind:r2BucketName
        bind:r2Region
        bind:r2KeyPrefix
        bind:r2SyncIntervalHours
        bind:r2AccessKeyId
        bind:r2SecretAccessKey
        {r2AccessKeyIdInputType}
        {r2SecretAccessKeyInputType}
        {r2Health}
        {r2HealthChecking}
        bind:nodeImageEnabled
        bind:nodeImageApiKey
        bind:promptOptimizerEnabled
        bind:promptOptimizerApiUrl
        bind:promptOptimizerModel
        bind:promptOptimizerTimeoutSeconds
        bind:promptOptimizerApiKey
        {promptOptimizerApiKeyInputType}
        {promptOptimizerHealthChecking}
        bind:aiAssistantEnabled
        bind:aiAssistantVisionModel
        {aiAssistantHealthChecking}
        {onCreate}
        {onDelete}
        {activateSelectedPreset}
        {keyLabel}
        {openOverallConfigModal}
        {normalizeR2SyncIntervalHours}
        {checkR2Health}
        {normalizePromptOptimizerTimeout}
        {openSystemPromptEditor}
        {checkPromptOptimizerHealth}
        {checkAiAssistantHealth}
      />

      <div class="space-y-3 border-t border-stone-200 p-5 dark:border-zinc-800">
        <HealthResults
          {health}
          {promptOptimizerHealth}
          {aiAssistantHealth}
          {closePromptOptimizerHealth}
          {closeAiAssistantHealth}
          {closePresetHealth}
        />
        <div class="grid grid-cols-2 gap-3">
          <button
            type="button"
            disabled={healthChecking || !activePresetId}
            class="control-focus rounded-xl border border-stone-300 px-4 py-3 text-sm font-semibold text-stone-700 transition-colors hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            on:click={checkHealth}
          >
            {healthChecking ? $t.settings.healthChecking : $t.settings.healthCheck}
          </button>
          <button
            type="button"
            disabled={saving}
            class="control-focus rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            on:click={save}
          >
            {saving ? $t.settings.saving : $t.settings.savePreset}
          </button>
        </div>
      </div>
</Overlay>

<OverallConfigDialog
  {overallConfigOpen}
  {overallConfigLoading}
  {overallConfigSaving}
  {overallConfigError}
  {overallConfigGroups}
  {overallConfigGroupNames}
  {closeOverallConfigModal}
  {saveOverallConfigModal}
  {overallDraftValue}
  {hasOverallDraft}
  {setOverallDraft}
  {resetOverallConfigItem}
  {sourceLabel}
/>

<SystemPromptDialog
  {systemPromptOpen}
  {systemPromptLoading}
  {systemPromptSaving}
  bind:systemPromptText
  {systemPromptError}
  {closeSystemPromptEditor}
  {saveSystemPrompt}
/>
