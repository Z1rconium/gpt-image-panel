<script lang="ts">
  import { renderAgentMarkdown } from '$lib/utils/agentMarkdown';
  let { text }: { text: string } = $props();
  const html = $derived(renderAgentMarkdown(text));
</script>

<div class="agent-markdown break-words text-sm leading-6 text-stone-900 dark:text-zinc-100" data-testid="agent-markdown">{@html html}</div>

<style>
  .agent-markdown :global(p + p), .agent-markdown :global(ul), .agent-markdown :global(ol), .agent-markdown :global(pre), .agent-markdown :global(blockquote), .agent-markdown :global(table) { margin-block: 0.75rem; }
  .agent-markdown :global(h1), .agent-markdown :global(h2), .agent-markdown :global(h3), .agent-markdown :global(h4) { margin-block: 0.75rem 0.25rem; font-weight: 600; font-size: 1.05em; }
  .agent-markdown :global(ul) { padding-left: 1.5rem; list-style: disc; }
  .agent-markdown :global(ol) { padding-left: 1.5rem; list-style: decimal; }
  .agent-markdown :global(a) { color: var(--agent-link, #0891b2); text-decoration: underline; text-underline-offset: 3px; }
  .agent-markdown :global(a:focus-visible) { outline: 2px solid currentColor; outline-offset: 3px; }
  .agent-markdown :global(pre) { max-width: 100%; overflow-x: auto; border: 1px solid #64748b55; border-radius: 0.375rem; padding: 0.75rem; }
  .agent-markdown :global(code) { font-family: ui-monospace, monospace; font-size: 0.9em; white-space: pre-wrap; }
  .agent-markdown :global(pre code) { white-space: pre; }
  .agent-markdown :global(blockquote) { border-left: 2px solid #64748b88; padding-left: 0.75rem; }
  .agent-markdown :global(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }
  .agent-markdown :global(th), .agent-markdown :global(td) { border: 1px solid #64748b55; padding: 0.35rem 0.65rem; text-align: left; }
</style>
