import type { GalleryEntry, GalleryResponse, GalleryThumbnailState } from '$lib/api/types/gallery';

export type GalleryFilters = {
  prompt: string;
  model: string;
  preset: string;
  size: string;
  dateFrom: string;
  dateTo: string;
  favorite: boolean;
};

export const defaultGalleryFilters: GalleryFilters = {
  prompt: '',
  model: '',
  preset: '',
  size: '',
  dateFrom: '',
  dateTo: '',
  favorite: false
};

export function buildGalleryParams(
  page: number,
  filters: GalleryFilters,
  includeTotalBytes = false,
  cursor?: string | null,
  direction?: 'next' | 'prev',
  includeCounts = true,
  includeFilterOptions = true
) {
  const params = new URLSearchParams({ page: String(page), page_size: '9' });
  if (filters.model) params.set('model', filters.model);
  if (filters.preset) params.set('preset', filters.preset);
  if (filters.size) params.set('size', filters.size);
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.favorite) params.set('favorite', 'true');
  if (includeTotalBytes) params.set('include_total_bytes', 'true');
  if (!includeCounts) params.set('include_counts', 'false');
  if (!includeFilterOptions) params.set('include_filter_options', 'false');
  if (cursor && direction) {
    params.set('cursor', cursor);
    params.set('direction', direction);
  }
  return params;
}

export function gallerySearchBody(params: URLSearchParams, filters: GalleryFilters) {
  return {
    page: Number(params.get('page') || 1),
    page_size: Number(params.get('page_size') || 9),
    prompt: filters.prompt.trim(),
    model: filters.model,
    preset: filters.preset,
    size: filters.size,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    favorite: filters.favorite ? true : null,
    include_total_bytes: params.get('include_total_bytes') === 'true',
    include_counts: params.get('include_counts') !== 'false',
    include_filter_options: params.get('include_filter_options') !== 'false',
    cursor: params.get('cursor'),
    direction: params.get('direction') || 'next'
  };
}

export function galleryFiltersToSelectionPayload(filters: GalleryFilters) {
  return {
    prompt: filters.prompt.trim(),
    model: filters.model,
    preset: filters.preset,
    size: filters.size,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    favorite: filters.favorite ? true : null
  };
}

export function sameGalleryFilters(left: GalleryFilters, right: GalleryFilters) {
  return (
    left.prompt === right.prompt &&
    left.model === right.model &&
    left.preset === right.preset &&
    left.size === right.size &&
    left.dateFrom === right.dateFrom &&
    left.dateTo === right.dateTo &&
    left.favorite === right.favorite
  );
}

export function sameGalleryEntryThumbnail(left: GalleryThumbnailState, right: GalleryThumbnailState) {
  return (
    left.id === right.id &&
    left.thumbnail_status === right.thumbnail_status &&
    left.thumbnail_url === right.thumbnail_url &&
    left.thumbnail_filename === right.thumbnail_filename
  );
}

export function sameGalleryImageList(left: GalleryEntry[], right: GalleryEntry[]) {
  if (left.length !== right.length) return false;
  return left.every((image, index) => {
    const next = right[index];
    return Boolean(next) && sameGalleryEntryThumbnail(image, next);
  });
}

export function pendingImageMatchesFilters(image: GalleryEntry, filters: GalleryFilters) {
  if (filters.prompt.trim() && !image.prompt.toLowerCase().includes(filters.prompt.trim().toLowerCase())) return false;
  if (filters.model && image.model !== filters.model) return false;
  if (filters.preset && image.api_preset_name !== filters.preset) return false;
  if (filters.size && image.size !== filters.size) return false;
  if (filters.favorite && !image.favorite) return false;
  if (filters.dateFrom || filters.dateTo) {
    const timestamp = image.completed_at || image.created_at;
    if (filters.dateFrom && timestamp < `${filters.dateFrom}T00:00:00`) return false;
    if (filters.dateTo && timestamp > `${filters.dateTo}T23:59:59.999999`) return false;
  }
  return true;
}

export function galleryThumbnailPendingState(gallery: GalleryResponse, filters: GalleryFilters) {
  const pendingIds = gallery.images
    .filter((image) => image.thumbnail_status && image.thumbnail_status !== 'ready')
    .map((image) => image.id);
  if (!pendingIds.length) return null;
  return {
    key: `${gallery.page}:${JSON.stringify(filters)}:${pendingIds.join(',')}`,
    pendingIds
  };
}
