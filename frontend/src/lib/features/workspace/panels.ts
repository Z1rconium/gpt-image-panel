import { createLazyComponent } from '$lib/utils/lazyComponent';

export type LazyPanel =
  | 'settings'
  | 'jobs'
  | 'snippets'
  | 'imagePrompt'
  | 'lightbox'
  | 'size'
  | 'editPreview'
  | 'optimizer';

export const lazyPanels = {
  settings: createLazyComponent(() => import('$lib/components/SettingsDrawer.svelte')),
  jobs: createLazyComponent(() => import('$lib/components/JobHistoryDrawer.svelte')),
  snippets: createLazyComponent(() => import('$lib/components/PromptSnippetsDrawer.svelte')),
  imagePrompt: createLazyComponent(() => import('$lib/components/ImagePromptDialog.svelte')),
  lightbox: createLazyComponent(() => import('$lib/components/Lightbox.svelte')),
  size: createLazyComponent(() => import('$lib/components/SizeDialog.svelte')),
  editPreview: createLazyComponent(() => import('$lib/components/EditPreviewModal.svelte')),
  optimizer: createLazyComponent(() => import('$lib/components/PromptOptimizerAssistant.svelte'))
} satisfies Record<LazyPanel, ReturnType<typeof createLazyComponent>>;

export const settingsPanel = lazyPanels.settings;
export const jobsPanel = lazyPanels.jobs;
export const snippetsPanel = lazyPanels.snippets;
export const imagePromptPanel = lazyPanels.imagePrompt;
export const lightboxPanel = lazyPanels.lightbox;
export const sizePanel = lazyPanels.size;
export const editPreviewPanel = lazyPanels.editPreview;
export const optimizerPanel = lazyPanels.optimizer;

// Not a modal panel, but the assistant is below the fold and heavy enough that
// it should not sit in the homepage dependency graph.
export const aiAssistantPanel = createLazyComponent(() => import('$lib/components/AiAssistantPanel.svelte'));

// Demand-loaded dialogs: they only render after the user opens them (or after
// a background prefetch), so they stay out of the homepage graph.
export const nodeImageResultPanel = createLazyComponent(() => import('$lib/components/NodeImageResultDialog.svelte'));
export const editGalleryDialogPanel = createLazyComponent(() => import('$lib/components/EditGalleryDialog.svelte'));

// The gallery grid sits below the fold; defer its (icon- and branch-heavy)
// module until after the first paint.
export const galleryGridPanel = createLazyComponent(() => import('$lib/components/GalleryGrid.svelte'));
