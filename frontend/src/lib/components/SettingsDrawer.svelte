<script lang="ts">
  import { t } from '$lib/i18n';
  import type { ResponseFormatDefault } from '$lib/api/types/common';
  import type { AIAssistantSettingsInput, ApiPreset, AssistantHealthResponse, OverallConfigItem, OverallConfigResponse, OverallConfigUpdateRequest, PromptOptimizerHealthResponse, PresetHealthResponse, PromptOptimizerSystemPromptResponse, R2BackupSettingsInput, R2HealthResponse, SettingsInput, SettingsResponse } from '$lib/api/types/settings';
  import { confirmStore } from '$lib/stores/confirm';
  import { normalizeResponseFormat } from '$lib/utils/promptForm';
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

  interface Props {
    open?: boolean;
    settings?: SettingsResponse | null;
    saving?: boolean;
    health?: PresetHealthResponse | null;
    healthChecking?: boolean;
    r2Health?: R2HealthResponse | null;
    r2HealthChecking?: boolean;
    promptOptimizerHealth?: PromptOptimizerHealthResponse | null;
    promptOptimizerHealthChecking?: boolean;
    aiAssistantHealth?: AssistantHealthResponse | null;
    aiAssistantHealthChecking?: boolean;
    onClose?: () => void;
    onSave?: (body: SettingsInput) => Promise<void> | void;
    onCreate?: () => Promise<void> | void;
    onActivate?: (presetId: string) => Promise<void> | void;
    onDelete?: (presetId: string) => Promise<void> | void;
    onHealthCheck?: (presetId: string) => Promise<void> | void;
    onClearPresetHealth?: () => void;
    onR2HealthCheck?: (body: R2BackupSettingsInput) => Promise<void> | void;
    onPromptOptimizerHealthCheck?: () => Promise<void> | void;
    onClearPromptOptimizerHealth?: () => void;
    onAiAssistantHealthCheck?: (body: AIAssistantSettingsInput) => Promise<void> | void;
    onClearAiAssistantHealth?: () => void;
    onLoadPromptOptimizerSystemPrompt?: () => Promise<PromptOptimizerSystemPromptResponse>;
    onSavePromptOptimizerSystemPrompt?: (systemPrompt: string) => Promise<PromptOptimizerSystemPromptResponse>;
    onLoadOverallConfig?: () => Promise<OverallConfigResponse>;
    onSaveOverallConfig?: (body: OverallConfigUpdateRequest) => Promise<OverallConfigResponse>;
  }

  let {
    open = false,
    settings = null,
    saving = false,
    health = null,
    healthChecking = false,
    r2Health = null,
    r2HealthChecking = false,
    promptOptimizerHealth = null,
    promptOptimizerHealthChecking = false,
    aiAssistantHealth = null,
    aiAssistantHealthChecking = false,
    onClose = () => {},
    onSave = () => {},
    onCreate = () => {},
    onActivate = () => {},
    onDelete = () => {},
    onHealthCheck = () => {},
    onClearPresetHealth = () => {},
    onR2HealthCheck = () => {},
    onPromptOptimizerHealthCheck = () => {},
    onClearPromptOptimizerHealth = () => {},
    onAiAssistantHealthCheck = () => {},
    onClearAiAssistantHealth = () => {},
    onLoadPromptOptimizerSystemPrompt = async () => ({
      system_prompt: '',
      default_system_prompt: '',
      customized: false
    }),
    onSavePromptOptimizerSystemPrompt = async (systemPrompt) => ({
      system_prompt: systemPrompt,
      default_system_prompt: '',
      customized: true
    }),
    onLoadOverallConfig = async () => ({
      items: [],
      restart_required_names: []
    }),
    onSaveOverallConfig = async () => ({
      items: [],
      restart_required_names: []
    })
  }: Props = $props();

  // Editable timeout/interval inputs hold raw text until their normalizers run.
  type EditableCount = number | string;
  type SettingsDrawerDraft = Omit<SettingsDraft, 'promptOptimizerTimeoutSeconds' | 'r2SyncIntervalHours'> & {
    promptOptimizerTimeoutSeconds: EditableCount;
    r2SyncIntervalHours: EditableCount;
  };

  const initialDraft: SettingsDrawerDraft = {
    activePresetId: '',
    presetName: '',
    apiUrl: '',
    defaultModel: '',
    defaultResponseFormat: 'url',
    apiKey: '',
    apiPath: '/v1/images/generations',
    upstreamSocks5Proxy: '',
    webhookUrl: '',
    promptOptimizerEnabled: false,
    promptOptimizerApiUrl: '',
    promptOptimizerModel: 'gpt-4o-mini',
    promptOptimizerTimeoutSeconds: 60,
    promptOptimizerApiKey: '',
    aiAssistantEnabled: false,
    aiAssistantVisionModel: 'gpt-4o-mini',
    r2BackupEnabled: false,
    r2EndpointUrl: '',
    r2BucketName: '',
    r2Region: 'auto',
    r2KeyPrefix: 'gallery/',
    r2SyncIntervalHours: 0,
    r2AccessKeyId: '',
    r2SecretAccessKey: '',
    nodeImageEnabled: false,
    nodeImageApiKey: ''
  };

  // One editable draft object instead of a mirror field per input; the sync
  // effect below is the only writer that reflects server state into it.
  let draft = $state<SettingsDrawerDraft>({ ...initialDraft });
  let activatingPresetId = $state('');
  let systemPromptOpen = $state(false);
  let systemPromptLoading = $state(false);
  let systemPromptSaving = $state(false);
  let systemPromptText = $state('');
  let systemPromptError = $state('');
  let overallConfigOpen = $state(false);
  let overallConfigLoading = $state(false);
  let overallConfigSaving = $state(false);
  let overallConfigError = $state('');
  let overallConfigItems = $state<OverallConfigItem[]>([]);
  let overallConfigDraft = $state<Record<string, string | boolean | number>>({});
  let overallConfigClears = $state<Record<string, boolean>>({});
  let systemPromptInitialText = $state('');

  const activePreset = $derived(
    settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null
  );

  // Mirror the incoming server state into the editable draft. Only the
  // settings object may trigger this; the draft fields are not dependencies.
  $effect(() => {
    if (!settings || !activePreset) return;
    draft.activePresetId = settings.active_preset_id;
    draft.presetName = activePreset.name || '';
    draft.apiUrl = activePreset.api_url || settings.api_url || '';
    draft.defaultModel = activePreset.default_model || settings.default_model || 'gpt-image-2';
    draft.apiKey =
      activePreset.api_key_source === 'registry' && activePreset.api_key_secret_id
        ? activePreset.api_key_secret_id
        : activePreset.has_api_key || settings.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    draft.apiPath = activePreset.api_path || settings.api_path || '/v1/images/generations';
    draft.defaultResponseFormat = normalizeResponseFormat(activePreset.default_response_format ?? settings.default_response_format, 'url');
    draft.upstreamSocks5Proxy = settings.has_upstream_socks5_proxy ? settings.upstream_socks5_proxy_masked : '';
    draft.webhookUrl = settings.has_webhook_url ? settings.webhook_url_masked : '';
    draft.promptOptimizerEnabled = Boolean(settings.prompt_optimizer?.enabled);
    draft.promptOptimizerApiUrl = settings.prompt_optimizer?.api_url || '';
    draft.promptOptimizerModel = settings.prompt_optimizer?.model || 'gpt-4o-mini';
    draft.promptOptimizerTimeoutSeconds = settings.prompt_optimizer?.timeout_seconds || 60;
    draft.promptOptimizerApiKey =
      settings.prompt_optimizer?.api_key_source === 'registry' && settings.prompt_optimizer.api_key_secret_id
        ? settings.prompt_optimizer.api_key_secret_id
        : settings.prompt_optimizer?.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    draft.aiAssistantEnabled = Boolean(settings.ai_assistant?.enabled);
    draft.aiAssistantVisionModel = settings.ai_assistant?.vision_model || settings.prompt_optimizer?.model || 'gpt-4o-mini';
    draft.r2BackupEnabled = Boolean(settings.r2_backup?.enabled);
    draft.r2EndpointUrl = settings.r2_backup?.endpoint_url || '';
    draft.r2BucketName = settings.r2_backup?.bucket_name || '';
    draft.r2Region = settings.r2_backup?.region || 'auto';
    draft.r2KeyPrefix = settings.r2_backup?.key_prefix || 'gallery/';
    draft.r2SyncIntervalHours = settings.r2_backup?.sync_interval_hours ?? 0;
    draft.r2AccessKeyId =
      settings.r2_backup?.access_key_id_source === 'registry' && settings.r2_backup.access_key_id_secret_id
        ? settings.r2_backup.access_key_id_secret_id
        : settings.r2_backup?.has_access_key_id
          ? MASKED_API_KEY_VALUE
          : '';
    draft.r2SecretAccessKey =
      settings.r2_backup?.secret_access_key_source === 'registry' && settings.r2_backup.secret_access_key_secret_id
        ? settings.r2_backup.secret_access_key_secret_id
        : settings.r2_backup?.has_secret_access_key
          ? MASKED_API_KEY_VALUE
          : '';
    draft.nodeImageEnabled = Boolean(settings.nodeimage?.enabled);
    draft.nodeImageApiKey = secretDraftValue(
      settings.nodeimage?.api_key_source,
      settings.nodeimage?.has_api_key,
      settings.nodeimage?.api_key_env_var,
      settings.nodeimage?.api_key_secret_id
    );
  });

  $effect(() => {
    if (!open && systemPromptOpen) {
      systemPromptOpen = false;
      systemPromptError = '';
    }
  });
  $effect(() => {
    if (!open && overallConfigOpen) {
      overallConfigOpen = false;
      overallConfigError = '';
    }
  });

  const overallConfigGroups = $derived(
    overallConfigItems.reduce(
      (groups, item) => {
        if (!groups[item.group]) groups[item.group] = [];
        groups[item.group].push(item);
        return groups;
      },
      {} as Record<string, OverallConfigItem[]>
    )
  );
  const overallConfigGroupNames = $derived(Object.keys(overallConfigGroups));
  const settingsDraft = $derived(
    {
      ...draft,
      promptOptimizerTimeoutSeconds: promptOptimizerTimeoutValue(draft.promptOptimizerTimeoutSeconds),
      r2SyncIntervalHours: r2SyncIntervalHoursValue(draft.r2SyncIntervalHours)
    } satisfies SettingsDraft
  );
  const settingsDirty = $derived(Boolean(settings && activePreset) && hasSettingsChanges(settingsDraft, settings, activePreset));
  const systemPromptDirty = $derived(systemPromptOpen && !systemPromptLoading && systemPromptText !== systemPromptInitialText);
  const overallConfigDirty = $derived(
    overallConfigItems.some((item) => {
      if (overallConfigClears[item.name]) return item.has_override;
      if (!hasOverallDraft(item.name)) return false;
      return overallConfigDraft[item.name] !== item.value;
    })
  );

  function normalizePromptOptimizerTimeout() {
    draft.promptOptimizerTimeoutSeconds = promptOptimizerTimeoutValue(draft.promptOptimizerTimeoutSeconds);
  }

  function normalizeR2SyncIntervalHours() {
    draft.r2SyncIntervalHours = r2SyncIntervalHoursValue(draft.r2SyncIntervalHours);
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
    if (!draft.activePresetId) return;
    await onHealthCheck(draft.activePresetId);
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
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.settings.closeLabel} onclick={requestCloseDrawer}>x</button>
      </div>

      <PresetSettingsEditor
        {settings}
        activePresetId={draft.activePresetId}
        {activatingPresetId}
        bind:presetName={draft.presetName}
        bind:apiUrl={draft.apiUrl}
        bind:apiPath={draft.apiPath}
        bind:defaultModel={draft.defaultModel}
        bind:defaultResponseFormat={draft.defaultResponseFormat}
        bind:apiKey={draft.apiKey}
        apiKeyInputType="text"
        bind:upstreamSocks5Proxy={draft.upstreamSocks5Proxy}
        bind:webhookUrl={draft.webhookUrl}
        bind:r2BackupEnabled={draft.r2BackupEnabled}
        bind:r2EndpointUrl={draft.r2EndpointUrl}
        bind:r2BucketName={draft.r2BucketName}
        bind:r2Region={draft.r2Region}
        bind:r2KeyPrefix={draft.r2KeyPrefix}
        bind:r2SyncIntervalHours={draft.r2SyncIntervalHours}
        bind:r2AccessKeyId={draft.r2AccessKeyId}
        bind:r2SecretAccessKey={draft.r2SecretAccessKey}
        r2AccessKeyIdInputType="text"
        r2SecretAccessKeyInputType="text"
        {r2Health}
        {r2HealthChecking}
        bind:nodeImageEnabled={draft.nodeImageEnabled}
        bind:nodeImageApiKey={draft.nodeImageApiKey}
        bind:promptOptimizerEnabled={draft.promptOptimizerEnabled}
        bind:promptOptimizerApiUrl={draft.promptOptimizerApiUrl}
        bind:promptOptimizerModel={draft.promptOptimizerModel}
        bind:promptOptimizerTimeoutSeconds={draft.promptOptimizerTimeoutSeconds}
        bind:promptOptimizerApiKey={draft.promptOptimizerApiKey}
        promptOptimizerApiKeyInputType="text"
        {promptOptimizerHealthChecking}
        bind:aiAssistantEnabled={draft.aiAssistantEnabled}
        bind:aiAssistantVisionModel={draft.aiAssistantVisionModel}
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
            disabled={healthChecking || !draft.activePresetId}
            class="control-focus rounded-xl border border-stone-300 px-4 py-3 text-sm font-semibold text-stone-700 transition-colors hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            onclick={checkHealth}
          >
            {healthChecking ? $t.settings.healthChecking : $t.settings.healthCheck}
          </button>
          <button
            type="button"
            disabled={saving}
            class="control-focus rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            onclick={save}
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
