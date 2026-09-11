import type {
  AIAssistantSettingsInput,
  ApiPreset,
  NodeImageSettingsInput,
  R2BackupSettingsInput,
  SettingsInput,
  SettingsResponse
} from '$lib/api/types/settings';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import { normalizeResponseFormat } from '$lib/utils/promptForm';

export const MASKED_API_KEY_VALUE = '********';

export type SettingsDraft = {
  activePresetId: string;
  presetName: string;
  apiUrl: string;
  defaultModel: string;
  defaultResponseFormat: ResponseFormatDefault;
  apiKey: string;
  apiPath: ApiPath;
  upstreamSocks5Proxy: string;
  webhookUrl: string;
  promptOptimizerEnabled: boolean;
  promptOptimizerApiUrl: string;
  promptOptimizerModel: string;
  promptOptimizerTimeoutSeconds: number;
  promptOptimizerApiKey: string;
  aiAssistantEnabled: boolean;
  aiAssistantVisionModel: string;
  r2BackupEnabled: boolean;
  r2EndpointUrl: string;
  r2BucketName: string;
  r2Region: string;
  r2KeyPrefix: string;
  r2SyncIntervalHours: number;
  r2AccessKeyId: string;
  r2SecretAccessKey: string;
  nodeImageEnabled: boolean;
  nodeImageApiKey: string;
};

export function promptOptimizerTimeoutValue(value: number | string | null | undefined): number {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
}

export function r2SyncIntervalHoursValue(value: number | string | null | undefined): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

export function secretDraftValue(
  source: 'empty' | 'stored' | 'env' | 'registry' | undefined,
  hasSecret: boolean | undefined,
  envVar: string | null | undefined,
  secretId: string | null | undefined
) {
  if (source === 'env' && envVar) return `\${${envVar}}`;
  if (source === 'registry' && secretId) return secretId;
  return hasSecret ? MASKED_API_KEY_VALUE : '';
}

function expectedSecretValue(
  source: 'empty' | 'stored' | 'env' | 'registry' | undefined,
  hasSecret: boolean | undefined,
  envVar: string | null | undefined
) {
  return source === 'env' && envVar ? `\${${envVar}}` : hasSecret ? MASKED_API_KEY_VALUE : '';
}

export function hasSettingsChanges(
  draft: SettingsDraft,
  settings: SettingsResponse | null,
  activePreset: ApiPreset | null
) {
  const proxyValue = draft.upstreamSocks5Proxy.trim();
  const currentProxyMask = settings?.upstream_socks5_proxy_masked || '';
  const webhookValue = draft.webhookUrl.trim();
  const currentWebhookMask = settings?.webhook_url_masked || '';

  return (
    draft.activePresetId !== (settings?.active_preset_id || '') ||
    draft.presetName !== (activePreset?.name || '') ||
    draft.apiUrl !== (activePreset?.api_url || settings?.api_url || '') ||
    draft.defaultModel !== (activePreset?.default_model || settings?.default_model || 'gpt-image-2') ||
    draft.defaultResponseFormat !== normalizeResponseFormat(activePreset?.default_response_format ?? settings?.default_response_format, 'url') ||
    draft.apiKey !== expectedSecretValue(activePreset?.api_key_source, activePreset?.has_api_key || settings?.has_api_key, activePreset?.api_key_env_var) ||
    draft.apiPath !== (activePreset?.api_path || settings?.api_path || '/v1/images/generations') ||
    proxyValue !== currentProxyMask ||
    webhookValue !== currentWebhookMask ||
    draft.promptOptimizerEnabled !== Boolean(settings?.prompt_optimizer?.enabled) ||
    draft.promptOptimizerApiUrl !== (settings?.prompt_optimizer?.api_url || '') ||
    draft.promptOptimizerModel !== (settings?.prompt_optimizer?.model || 'gpt-4o-mini') ||
    draft.promptOptimizerTimeoutSeconds !== (settings?.prompt_optimizer?.timeout_seconds || 60) ||
    draft.promptOptimizerApiKey !==
      expectedSecretValue(
        settings?.prompt_optimizer?.api_key_source,
        settings?.prompt_optimizer?.has_api_key,
        settings?.prompt_optimizer?.api_key_env_var
      ) ||
    draft.aiAssistantEnabled !== Boolean(settings?.ai_assistant?.enabled) ||
    draft.aiAssistantVisionModel !==
      (settings?.ai_assistant?.vision_model || settings?.prompt_optimizer?.model || 'gpt-4o-mini') ||
    draft.r2BackupEnabled !== Boolean(settings?.r2_backup?.enabled) ||
    draft.r2EndpointUrl !== (settings?.r2_backup?.endpoint_url || '') ||
    draft.r2BucketName !== (settings?.r2_backup?.bucket_name || '') ||
    draft.r2Region !== (settings?.r2_backup?.region || 'auto') ||
    draft.r2KeyPrefix !== (settings?.r2_backup?.key_prefix || 'gallery/') ||
    draft.r2SyncIntervalHours !== (settings?.r2_backup?.sync_interval_hours ?? 0) ||
    draft.r2AccessKeyId !==
      expectedSecretValue(
        settings?.r2_backup?.access_key_id_source,
        settings?.r2_backup?.has_access_key_id,
        settings?.r2_backup?.access_key_id_env_var
      ) ||
    draft.r2SecretAccessKey !==
      expectedSecretValue(
        settings?.r2_backup?.secret_access_key_source,
        settings?.r2_backup?.has_secret_access_key,
        settings?.r2_backup?.secret_access_key_env_var
      ) ||
    draft.nodeImageEnabled !== Boolean(settings?.nodeimage?.enabled) ||
    draft.nodeImageApiKey !==
      secretDraftValue(
        settings?.nodeimage?.api_key_source,
        settings?.nodeimage?.has_api_key,
        settings?.nodeimage?.api_key_env_var,
        settings?.nodeimage?.api_key_secret_id
      )
  );
}

export function aiAssistantPayload(draft: SettingsDraft): AIAssistantSettingsInput {
  return {
    enabled: draft.aiAssistantEnabled,
    vision_model: draft.aiAssistantVisionModel.trim()
  };
}

export function r2BackupPayload(draft: SettingsDraft): R2BackupSettingsInput {
  return {
    enabled: draft.r2BackupEnabled,
    endpoint_url: draft.r2EndpointUrl.trim(),
    bucket_name: draft.r2BucketName.trim(),
    region: draft.r2Region.trim() || 'auto',
    key_prefix: draft.r2KeyPrefix.trim(),
    sync_interval_hours: r2SyncIntervalHoursValue(draft.r2SyncIntervalHours),
    access_key_id: draft.r2AccessKeyId.trim() === MASKED_API_KEY_VALUE ? null : draft.r2AccessKeyId.trim(),
    secret_access_key: draft.r2SecretAccessKey.trim() === MASKED_API_KEY_VALUE ? null : draft.r2SecretAccessKey.trim()
  };
}

export function nodeImagePayload(draft: SettingsDraft): NodeImageSettingsInput {
  return {
    enabled: draft.nodeImageEnabled,
    api_key: draft.nodeImageApiKey.trim() === MASKED_API_KEY_VALUE ? null : draft.nodeImageApiKey.trim()
  };
}

export function buildSettingsPayload(draft: SettingsDraft, settings: SettingsResponse | null): SettingsInput {
  const proxyValue = draft.upstreamSocks5Proxy.trim();
  const currentProxyMask = settings?.upstream_socks5_proxy_masked || '';
  const webhookValue = draft.webhookUrl.trim();
  const currentWebhookMask = settings?.webhook_url_masked || '';
  return {
    active_preset_id: draft.activePresetId,
    preset_name: draft.presetName.trim(),
    api_url: draft.apiUrl.trim(),
    default_model: draft.defaultModel.trim(),
    default_response_format: draft.defaultResponseFormat,
    api_key: draft.apiKey.trim() === MASKED_API_KEY_VALUE ? null : draft.apiKey.trim(),
    api_path: draft.apiPath,
    upstream_socks5_proxy: proxyValue === currentProxyMask ? null : proxyValue,
    webhook_url: webhookValue === currentWebhookMask ? null : webhookValue,
    prompt_optimizer: {
      enabled: draft.promptOptimizerEnabled,
      api_url: draft.promptOptimizerApiUrl.trim(),
      model: draft.promptOptimizerModel.trim(),
      timeout_seconds: promptOptimizerTimeoutValue(draft.promptOptimizerTimeoutSeconds),
      api_key: draft.promptOptimizerApiKey.trim() === MASKED_API_KEY_VALUE ? null : draft.promptOptimizerApiKey.trim()
    },
    ai_assistant: aiAssistantPayload(draft),
    r2_backup: r2BackupPayload(draft),
    nodeimage: nodeImagePayload(draft)
  };
}
