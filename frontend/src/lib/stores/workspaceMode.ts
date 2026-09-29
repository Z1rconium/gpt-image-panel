import { writable } from 'svelte/store';

export type WorkspaceMode = 'studio' | 'agent';

export type WorkspaceModeState = {
  mode: WorkspaceMode;
  conversationId: string | null;
};

const initialState: WorkspaceModeState = { mode: 'studio', conversationId: null };

function createWorkspaceModeStore() {
  const { subscribe, set, update } = writable<WorkspaceModeState>(initialState);

  return {
    subscribe,
    setMode(mode: WorkspaceMode) {
      update((state) => ({ ...state, mode }));
    },
    setConversation(conversationId: string | null) {
      update((state) => ({ ...state, conversationId }));
    },
    apply(state: WorkspaceModeState) {
      set(state);
    }
  };
}

export const workspaceModeStore = createWorkspaceModeStore();
