<script lang="ts">
  import { dialogIn, dialogOut, drawerIn, drawerOut, overlayIn, overlayOut } from '$lib/motion';
  import { dialog } from '$lib/actions/dialog';
  import { swipeClose } from '$lib/actions/swipeClose';

  /**
   * Shared chrome for every modal dialog/drawer: the fixed backdrop, the
   * click-outside-to-close button, the panel shell, and the transition +
   * focus-trap/ESC/scroll-lock wiring (`$lib/actions/dialog`). Header, body,
   * and footer markup stay the caller's own slot content - they vary too
   * much (a danger-red confirm button, a filename chip, a settings form) to
   * standardize here.
   */
  export let open = false;
  export let variant: 'dialog' | 'drawer' = 'dialog';
  export let onClose: () => void = () => {};
  export let closeLabel: string;
  export let labelledBy: string;
  export let describedBy: string | undefined = undefined;
  export let z: number;
  export let panelId: string | undefined = undefined;
  export let maxWidthClass = 'max-w-lg';
  export let panelClass = '';
  export let backdropClass = 'bg-stone-950/60 dark:bg-zinc-950/75';
  export let backdropBlur = false;
  export let mobileDvh = false;
  export let swipeToClose = variant === 'drawer';

  $: enterTransition = variant === 'drawer' ? drawerIn : dialogIn;
  $: exitTransition = variant === 'drawer' ? drawerOut : dialogOut;
</script>

{#if open}
  <div
    class={variant === 'drawer'
      ? 'mobile-drawer-root fixed inset-0'
      : `fixed inset-0 flex items-center justify-center p-4 ${backdropClass} ${backdropBlur ? 'backdrop-blur' : ''} ${mobileDvh ? 'mobile-dialog-root' : ''}`}
    style:z-index={z}
    in:overlayIn
    out:overlayOut
  >
    <button
      type="button"
      class={variant === 'drawer' ? 'drawer-backdrop absolute inset-0' : 'absolute inset-0'}
      tabindex="-1"
      aria-label={closeLabel}
      on:click={onClose}
    ></button>
    <div
      id={panelId}
      class={variant === 'drawer'
        ? `mobile-drawer-panel overlay-panel absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${panelClass}`
        : `overlay-panel relative w-full ${maxWidthClass} rounded-2xl border border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${mobileDvh ? 'mobile-dvh-dialog' : ''} ${panelClass}`}
      in:enterTransition
      out:exitTransition
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      use:dialog={{ open, onClose }}
      use:swipeClose={{ enabled: swipeToClose, onClose }}
    >
      <slot />
    </div>
  </div>
{/if}
