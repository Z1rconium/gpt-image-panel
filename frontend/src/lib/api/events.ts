export type JsonEvent<T> = {
  event: string;
  data: T;
  /** SSE frame id; 0 when the server did not send one. */
  lastEventId: number;
};

export type EventHandlers<T> = {
  onEvent: (event: JsonEvent<T>) => void;
  onError?: (error?: unknown) => void;
  onNetworkError?: (event: Event) => void;
};

export class JsonEventSourceParseError extends Error {
  readonly eventName: string;
  readonly originalError: unknown;

  constructor(eventName: string, originalError: unknown) {
    super(`Invalid JSON in ${eventName} event`);
    this.name = 'JsonEventSourceParseError';
    this.eventName = eventName;
    this.originalError = originalError;
  }
}

export class JsonEventSourceClosedError extends Error {
  readonly event: Event;

  constructor(event: Event) {
    super('EventSource connection closed');
    this.name = 'JsonEventSourceClosedError';
    this.event = event;
  }
}

export function openJsonEventSource<T>(url: string, handlers: EventHandlers<T>, eventNames = ['job', 'jobs']): EventSource {
  const source = new EventSource(url);

  function handleJsonEvent(eventName: string, event: Event) {
    let data: T;
    try {
      data = JSON.parse((event as MessageEvent).data) as T;
    } catch (error) {
      source.close();
      handlers.onError?.(new JsonEventSourceParseError(eventName, error));
      return;
    }
    const frameId = Number((event as MessageEvent).lastEventId);
    handlers.onEvent({ event: eventName, data, lastEventId: Number.isFinite(frameId) ? frameId : 0 });
  }

  for (const eventName of eventNames) {
    source.addEventListener(eventName, (event) => handleJsonEvent(eventName, event));
  }

  source.onerror = (event) => {
    if (source.readyState === EventSource.CONNECTING) {
      handlers.onNetworkError?.(event);
      return;
    }
    handlers.onError?.(new JsonEventSourceClosedError(event));
  };

  return source;
}
