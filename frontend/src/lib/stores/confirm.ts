import { writable } from 'svelte/store';

export type ConfirmVariant = 'default' | 'danger';

export type ConfirmRequest = {
  id: number;
  title: string;
  message: string;
  details?: string[];
  confirmLabel: string;
  cancelLabel: string;
  closeLabel: string;
  variant: ConfirmVariant;
  requiredText?: string;
  requiredTextLabel?: string;
};

type ConfirmState = {
  request: ConfirmRequest | null;
};

type ConfirmOptions = Omit<ConfirmRequest, 'id' | 'variant'> & {
  variant?: ConfirmVariant;
};

const initialState: ConfirmState = {
  request: null
};

function createConfirmStore() {
  const { subscribe, set } = writable<ConfirmState>(initialState);
  let nextId = 1;
  let resolver: ((confirmed: boolean) => void) | null = null;

  function finish(confirmed: boolean) {
    const currentResolver = resolver;
    resolver = null;
    set(initialState);
    currentResolver?.(confirmed);
  }

  function confirm(options: ConfirmOptions) {
    if (resolver) finish(false);
    const request: ConfirmRequest = {
      ...options,
      id: nextId,
      variant: options.variant || 'default'
    };
    nextId += 1;
    set({ request });
    return new Promise<boolean>((resolve) => {
      resolver = resolve;
    });
  }

  return {
    subscribe,
    confirm,
    accept: () => finish(true),
    cancel: () => finish(false)
  };
}

export const confirmStore = createConfirmStore();
