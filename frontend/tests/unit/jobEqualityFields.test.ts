import { describe, expect, it } from 'vitest';
import { sameJob } from '$lib/stores/jobsState';
import { sameJobSnapshot } from '$lib/stores/previewGroup';
import type { GenerateJobStatus } from '$lib/api/types/jobs';

// Every GenerateJobStatus field must be classified. Adding a field to the type
// fails type-checking here until its comparison policy is decided.
//   core     compared by both sameJob and sameJobSnapshot (sameJobCore)
//   jobs     compared only by sameJob (jobs-list diffing)
//   snapshot compared only by sameJobSnapshot (preview group)
//   ignored  deliberately not compared by either
type Policy = 'core' | 'jobs' | 'snapshot' | 'ignored';

const FIELD_POLICY: Record<keyof GenerateJobStatus, Policy> = {
  job_id: 'core',
  status: 'core',
  stage: 'core',
  message: 'core',
  error: 'core',
  completed_count: 'core',
  success_count: 'core',
  failure_count: 'core',
  image_id: 'core',
  image_url: 'core',
  duration: 'core',
  completed_at: 'core',
  updated_at: 'core',
  operation: 'jobs',
  prompt: 'jobs',
  size: 'jobs',
  model: 'jobs',
  images: 'jobs', // jobs: image dimensions; snapshot: unit/result index (see below)
  stage_timings: 'jobs',
  streaming: 'snapshot',
  mask_applied: 'snapshot',
  markup_applied: 'snapshot',
  unit_statuses: 'snapshot',
  id: 'ignored',
  agent_turn_id: 'ignored',
  agent_conversation_id: 'ignored',
  created_at: 'ignored',
  started_at: 'ignored',
  image_width: 'ignored',
  image_height: 'ignored',
  quality: 'ignored',
  complexity: 'ignored',
  frame_count: 'ignored',
  frame_duration_ms: 'ignored',
  output_format: 'ignored',
  output_compression: 'ignored',
  background: 'ignored',
  response_format: 'ignored',
  safety_identifier: 'ignored',
  n: 'ignored',
  api_path: 'ignored',
  api_preset_id: 'ignored',
  api_preset_name: 'ignored',
  usage: 'ignored',
  cost: 'ignored',
  partial_images: 'ignored',
  paste_back: 'ignored'
};

const base: GenerateJobStatus = { job_id: 'job-1', status: 'running' };

const image = (extra: Record<string, number>) => ({
  image_id: 'i',
  image_url: '/i',
  filename: 'i.png',
  ...extra
});

// A differing value for fields whose type is not a plain string.
const VARIANTS: Partial<Record<keyof GenerateJobStatus, [unknown, unknown]>> = {
  completed_count: [1, 2],
  success_count: [1, 2],
  failure_count: [1, 2],
  stage_timings: [{ a: 1 }, { a: 2 }],
  streaming: [true, false],
  mask_applied: [true, false],
  markup_applied: [true, false],
  unit_statuses: [{ '0': 'running' }, { '0': 'completed' }]
};

function pair(field: keyof GenerateJobStatus): [GenerateJobStatus, GenerateJobStatus] {
  const [a, b] = VARIANTS[field] ?? ['a', 'b'];
  return [
    { ...base, [field]: a } as GenerateJobStatus,
    { ...base, [field]: b } as GenerateJobStatus
  ];
}

describe('job equality field policy', () => {
  const scalars = (Object.keys(FIELD_POLICY) as (keyof GenerateJobStatus)[]).filter((f) => f !== 'images');

  it.each(scalars)('%s follows its declared policy', (field) => {
    const policy = FIELD_POLICY[field];
    const [left, right] = pair(field);
    const jobsDiffers = !sameJob(left, right);
    const snapshotDiffers = !sameJobSnapshot(left, right);
    expect(jobsDiffers).toBe(policy === 'core' || policy === 'jobs');
    expect(snapshotDiffers).toBe(policy === 'core' || policy === 'snapshot');
  });

  it('images: sameJob compares dimensions, sameJobSnapshot compares unit/result index', () => {
    const dims = [
      { ...base, images: [image({ image_width: 1 })] },
      { ...base, images: [image({ image_width: 2 })] }
    ] as GenerateJobStatus[];
    expect(sameJob(dims[0], dims[1])).toBe(false);
    expect(sameJobSnapshot(dims[0], dims[1])).toBe(true);

    const idx = [
      { ...base, images: [image({ unit_index: 0 })] },
      { ...base, images: [image({ unit_index: 1 })] }
    ] as GenerateJobStatus[];
    expect(sameJob(idx[0], idx[1])).toBe(true);
    expect(sameJobSnapshot(idx[0], idx[1])).toBe(false);
  });
});
