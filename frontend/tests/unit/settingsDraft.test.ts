import { describe, expect, it } from 'vitest';
import {
  MASKED_API_KEY_VALUE,
  agentMaxToolRoundsValue,
  buildSettingsPayload,
  hasSettingsChanges,
  r2SyncIntervalHoursValue,
  secretDraftValue,
  type SettingsDraft
} from '$lib/features/settings/draft';
import type { SettingsResponse } from '$lib/api/types/settings';

const settings = {
  active_preset_id: 'default',
  api_url: 'https://api.example.com',
  api_path: '/v1/images/generations',
  default_model: 'gpt-image-2',
  default_response_format: 'url',
  upstream_socks5_proxy_masked: '',
  webhook_url_masked: '',
  has_api_key: true,
  presets: [
    {
      id: 'default',
      name: 'Default',
      api_url: 'https://api.example.com',
      api_path: '/v1/images/generations',
      default_model: 'gpt-image-2',
      default_response_format: 'url',
      has_api_key: true,
      api_key_source: 'stored'
    }
  ],
  prompt_optimizer: {
    enabled: true,
    api_url: 'https://optimizer.example.com',
    model: 'gpt-4o-mini',
    timeout_seconds: 60,
    has_api_key: true,
    api_key_source: 'stored'
  },
  ai_assistant: {
    enabled: true,
    vision_model: 'gpt-4o-mini',
    model: 'gpt-4o-mini'
  },
  r2_backup: {
    enabled: false,
    endpoint_url: '',
    bucket_name: '',
    region: 'auto',
    key_prefix: 'gallery/',
    sync_interval_hours: 0,
    has_access_key_id: false,
    access_key_id_source: 'empty',
    has_secret_access_key: false,
    secret_access_key_source: 'empty'
  },
  nodeimage: {
    enabled: true,
    has_api_key: true,
    api_key_source: 'stored'
  }
} as unknown as SettingsResponse;

const activePreset = settings.presets[0];

function pristineDraft(): SettingsDraft {
  return {
    activePresetId: 'default',
    presetName: 'Default',
    apiUrl: 'https://api.example.com',
    defaultModel: 'gpt-image-2',
    defaultResponseFormat: 'url',
    supportsMask: true,
    promptGuard: false,
    apiKey: MASKED_API_KEY_VALUE,
    apiPath: '/v1/images/generations',
    upstreamSocks5Proxy: '',
    webhookUrl: '',
    promptOptimizerEnabled: true,
    promptOptimizerApiUrl: 'https://optimizer.example.com',
    promptOptimizerModel: 'gpt-4o-mini',
    promptOptimizerTimeoutSeconds: 60,
    promptOptimizerApiKey: MASKED_API_KEY_VALUE,
    aiAssistantEnabled: true,
    aiAssistantVisionModel: 'gpt-4o-mini',
    agentEnabled: false,
    agentModel: '',
    agentMaxToolRounds: 4,
    agentSystemPrompt: '',
    r2BackupEnabled: false,
    r2EndpointUrl: '',
    r2BucketName: '',
    r2Region: 'auto',
    r2KeyPrefix: 'gallery/',
    r2SyncIntervalHours: 0,
    r2AccessKeyId: '',
    r2SecretAccessKey: '',
    nodeImageEnabled: true,
    nodeImageApiKey: MASKED_API_KEY_VALUE
  };
}

describe('hasSettingsChanges', () => {
  it('is false for a pristine draft and true after an edit', () => {
    expect(hasSettingsChanges(pristineDraft(), settings, activePreset)).toBe(false);
    expect(hasSettingsChanges({ ...pristineDraft(), defaultModel: 'gpt-image-3' }, settings, activePreset)).toBe(true);
  });
});

describe('buildSettingsPayload', () => {
  it('omits unchanged masked secrets and trims text fields', () => {
    const payload = buildSettingsPayload({ ...pristineDraft(), apiUrl: '  https://api.example.com  ' }, settings);
    expect(payload.api_key).toBeNull();
    expect(payload.prompt_optimizer?.api_key).toBeNull();
    expect(payload.api_url).toBe('https://api.example.com');
    expect(payload.upstream_socks5_proxy).toBeNull();
  });

  it('forwards a newly entered secret', () => {
    const payload = buildSettingsPayload({ ...pristineDraft(), apiKey: 'sk-new' }, settings);
    expect(payload.api_key).toBe('sk-new');
  });

  it('detects and forwards a mask capability change', () => {
    const draft = { ...pristineDraft(), supportsMask: false };
    expect(hasSettingsChanges(draft, settings, activePreset)).toBe(true);
    expect(buildSettingsPayload(draft, settings).supports_mask).toBe(false);
  });

  it('detects and forwards a prompt guard change', () => {
    expect(buildSettingsPayload(pristineDraft(), settings).prompt_guard).toBe(false);
    const draft = { ...pristineDraft(), promptGuard: true };
    expect(hasSettingsChanges(draft, settings, activePreset)).toBe(true);
    expect(buildSettingsPayload(draft, settings).prompt_guard).toBe(true);
  });
});

describe('agent settings', () => {
  it('is pristine with defaults and forwards trimmed agent fields', () => {
    expect(hasSettingsChanges(pristineDraft(), settings, activePreset)).toBe(false);
    const draft = {
      ...pristineDraft(),
      agentEnabled: true,
      agentModel: ' agent-model ',
      agentMaxToolRounds: 6,
      agentSystemPrompt: '  Be brief.  '
    };
    expect(hasSettingsChanges(draft, settings, activePreset)).toBe(true);
    expect(buildSettingsPayload(draft, settings).ai_assistant).toMatchObject({
      agent_enabled: true,
      agent_model: 'agent-model',
      agent_max_tool_rounds: 6,
      agent_system_prompt: 'Be brief.'
    });
  });

  it('normalizes the tool round count into 1..64 and reads saved values', () => {
    expect(agentMaxToolRoundsValue('abc')).toBe(4);
    expect(agentMaxToolRoundsValue(0)).toBe(4);
    expect(agentMaxToolRoundsValue('7')).toBe(7);
    expect(agentMaxToolRoundsValue(500)).toBe(64);
    const saved = {
      ...settings,
      ai_assistant: { ...settings.ai_assistant, agent_enabled: true, agent_model: 'm', agent_max_tool_rounds: 2, agent_system_prompt: 'x' }
    } as unknown as SettingsResponse;
    const draft = { ...pristineDraft(), agentEnabled: true, agentModel: 'm', agentMaxToolRounds: 2, agentSystemPrompt: 'x' };
    expect(hasSettingsChanges(draft, saved, activePreset)).toBe(false);
    expect(hasSettingsChanges({ ...draft, agentMaxToolRounds: 3 }, saved, activePreset)).toBe(true);
  });
});

describe('secretDraftValue', () => {
  it('resolves env, registry, stored, and empty sources', () => {
    expect(secretDraftValue('env', false, 'MY_KEY', null)).toBe('${MY_KEY}');
    expect(secretDraftValue('registry', false, null, 'secret-id')).toBe('secret-id');
    expect(secretDraftValue('stored', true, null, null)).toBe(MASKED_API_KEY_VALUE);
    expect(secretDraftValue('empty', false, null, null)).toBe('');
  });
});

describe('r2SyncIntervalHoursValue', () => {
  it('clamps to a non-negative integer', () => {
    expect(r2SyncIntervalHoursValue('12')).toBe(12);
    expect(r2SyncIntervalHoursValue(-3)).toBe(0);
    expect(r2SyncIntervalHoursValue('abc')).toBe(0);
  });
});
