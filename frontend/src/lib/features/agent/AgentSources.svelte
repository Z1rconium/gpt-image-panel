<script lang="ts">
  import type { AgentSource } from '$lib/api/types/agent';
  import { t } from '$lib/i18n';
  import { safeAgentUrl } from '$lib/utils/agentMarkdown';
  let { sources }: { sources: AgentSource[] } = $props();
</script>

<details class="border-t border-stone-200 pt-2 text-xs dark:border-zinc-800" data-testid="agent-sources">
  <summary class="control-focus cursor-pointer font-medium">{$t.agent.sources} ({sources.length})</summary>
  <ol class="mt-2 list-decimal space-y-2 pl-5">
    {#each sources as source (source.id)}
      <li>
        {#if safeAgentUrl(source.url)}
          <a class="control-focus text-cyan-700 underline dark:text-cyan-300" href={safeAgentUrl(source.url)} target="_blank" rel="noopener noreferrer">{source.title}</a>
        {:else}
          <span>{source.title}</span>
        {/if}
        {#if source.excerpt}<p class="mt-1 break-words text-stone-500 dark:text-zinc-400">{source.excerpt}</p>{/if}
      </li>
    {/each}
  </ol>
</details>
