import { writable } from 'svelte/store';

// A preset package received through a `?preset=` share link, waiting for the
// user to open the import preview. Never applied automatically.
export const presetShareStore = writable<unknown | null>(null);
