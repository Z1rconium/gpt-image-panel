import type { Action } from 'svelte/action';

/**
 * Reports an element's content-box height on mount and on every resize.
 * Shared by any list that windows its rows against a scroll viewport.
 */
export const observeViewport: Action<HTMLElement, (height: number) => void> = (node, onResize) => {
  const update = () => onResize(node.clientHeight);
  update();
  const observer = new ResizeObserver(update);
  observer.observe(node);
  return {
    update(nextOnResize) {
      onResize = nextOnResize;
    },
    destroy() {
      observer.disconnect();
    }
  };
};

export type MeasureItemParams = {
  id: string;
  onMeasure: (id: string, height: number) => void;
};

/**
 * Reports one row's rendered height (id-keyed) on mount and on every resize,
 * so a virtualized list can lay out variable-height rows without measuring
 * everything up front.
 */
export const measureItem: Action<HTMLElement, MeasureItemParams> = (node, params) => {
  let { id, onMeasure } = params;
  const update = () => {
    const height = Math.ceil(node.getBoundingClientRect().height);
    if (height > 0) onMeasure(id, height);
  };
  update();
  const observer = new ResizeObserver(update);
  observer.observe(node);
  return {
    update(next) {
      id = next.id;
      onMeasure = next.onMeasure;
    },
    destroy() {
      observer.disconnect();
    }
  };
};
