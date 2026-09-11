type WorkspaceLifecycleOptions = {
  onPopstate: () => void;
  onKeydown: (event: KeyboardEvent) => void;
  onPaste: (event: ClipboardEvent) => void;
  prefetchCommonPanels: () => void;
  shouldPrefetch: () => boolean;
  cleanup: () => void;
};

/**
 * Installs the workspace's global listeners and schedules the low-priority
 * panel prefetch. Prefetch waits until the first critical content has loaded
 * (window `load`), then requests an idle slot, re-checking network/visibility
 * both at schedule time and just before it runs so hidden or constrained
 * sessions never spend bandwidth on optional chunks.
 */
export function installWorkspaceLifecycle(options: WorkspaceLifecycleOptions) {
  window.addEventListener('popstate', options.onPopstate);
  window.addEventListener('keydown', options.onKeydown);
  window.addEventListener('paste', options.onPaste);
  let cancelScheduled: (() => void) | null = null;
  let loadListener: (() => void) | null = null;

  function scheduleIdlePrefetch() {
    if (!options.shouldPrefetch()) return;
    const run = () => {
      cancelScheduled = null;
      if (options.shouldPrefetch()) options.prefetchCommonPanels();
    };
    if (typeof window.requestIdleCallback === 'function') {
      const handle = window.requestIdleCallback(run, { timeout: 3000 });
      cancelScheduled = () => window.cancelIdleCallback(handle);
    } else {
      const handle = window.setTimeout(run, 1800);
      cancelScheduled = () => window.clearTimeout(handle);
    }
  }

  if (options.shouldPrefetch()) {
    if (typeof document !== 'undefined' && document.readyState === 'complete') {
      scheduleIdlePrefetch();
    } else {
      loadListener = scheduleIdlePrefetch;
      window.addEventListener('load', loadListener, { once: true });
    }
  }

  return () => {
    window.removeEventListener('popstate', options.onPopstate);
    window.removeEventListener('keydown', options.onKeydown);
    window.removeEventListener('paste', options.onPaste);
    if (loadListener) window.removeEventListener('load', loadListener);
    cancelScheduled?.();
    options.cleanup();
  };
}
