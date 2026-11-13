/**
 * Merge a reordered list of supported member IDs back into the full collection
 * member list, keeping unsupported (e.g. SVG/GIF) items in their original slots.
 */
export function mergeReorderedSupportedIds(allIds: string[], reorderedSupportedIds: string[]): string[] {
  if (!allIds.length) return [...reorderedSupportedIds];
  const supportedSet = new Set(reorderedSupportedIds);
  let cursor = 0;
  const merged = allIds.map((id) => {
    if (!supportedSet.has(id)) return id;
    const replacement = reorderedSupportedIds[cursor];
    cursor += 1;
    return replacement ?? id;
  });
  return cursor < reorderedSupportedIds.length
    ? [...merged, ...reorderedSupportedIds.slice(cursor)]
    : merged;
}
