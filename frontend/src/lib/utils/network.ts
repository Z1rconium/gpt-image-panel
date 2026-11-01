type NetworkInformationLike = {
  effectiveType?: string;
  saveData?: boolean;
};

type NavigatorWithConnection = Navigator & {
  connection?: NetworkInformationLike;
};

function connectionInfo() {
  if (typeof navigator === 'undefined') return null;
  return (navigator as NavigatorWithConnection).connection || null;
}

/** Background work must stop while the tab is hidden so it never competes with the visible page. */
export function isPageVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden';
}

export function canPrefetchNonCritical() {
  if (!isPageVisible()) return false;
  const connection = connectionInfo();
  if (!connection) return true;
  if (connection.saveData) return false;
  return connection.effectiveType !== 'slow-2g' && connection.effectiveType !== '2g';
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking synchronously can cancel the download in WebKit before the
  // navigation consumes the blob URL; defer it to a later task.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function canPrefetchLargeMedia() {
  if (!isPageVisible()) return false;
  const connection = connectionInfo();
  if (!connection) return true;
  if (connection.saveData) return false;
  return !['slow-2g', '2g', '3g'].includes(connection.effectiveType || '');
}
