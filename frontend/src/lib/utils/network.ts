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

export function canPrefetchLargeMedia() {
  if (!isPageVisible()) return false;
  const connection = connectionInfo();
  if (!connection) return true;
  if (connection.saveData) return false;
  return !['slow-2g', '2g', '3g'].includes(connection.effectiveType || '');
}
