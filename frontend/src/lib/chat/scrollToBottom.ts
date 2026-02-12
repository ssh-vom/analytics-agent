import { tick } from "svelte";

type FeedElementGetter = () => HTMLDivElement | null;

export function createScrollToBottom(getFeedElement: FeedElementGetter) {
  let shouldAutoScroll = true;
  let pendingScrollRaf = 0;
  let hasPendingScroll = false;
  let pendingScrollForce = false;
  let scrollAttemptsQueue: (() => void)[] = [];

  function handleScroll(): void {
    const feedElement = getFeedElement();
    if (!feedElement) {
      return;
    }
    const bottomDistance =
      feedElement.scrollHeight - feedElement.scrollTop - feedElement.clientHeight;
    shouldAutoScroll = bottomDistance < 120;
  }

  function scrollToBottom(force = false): void {
    if (force) {
      pendingScrollForce = true;
    }
    if (!force && !shouldAutoScroll) {
      return;
    }

    scrollAttemptsQueue.push(() => {
      const feedElement = getFeedElement();
      const shouldScrollNow = pendingScrollForce || shouldAutoScroll;
      pendingScrollForce = false;
      if (!feedElement || !shouldScrollNow) {
        return;
      }
      feedElement.scrollTo({
        top: feedElement.scrollHeight,
        behavior: "auto",
      });
    });

    if (hasPendingScroll) {
      return;
    }
    hasPendingScroll = true;

    void tick().then(() => {
      pendingScrollRaf = requestAnimationFrame(() => {
        pendingScrollRaf = 0;
        hasPendingScroll = false;
        const lastAttempt = scrollAttemptsQueue.pop();
        scrollAttemptsQueue = [];
        if (lastAttempt) {
          lastAttempt();
        }
      });
    });
  }

  function setAutoScroll(value: boolean): void {
    shouldAutoScroll = value;
  }

  function dispose(): void {
    if (pendingScrollRaf) {
      cancelAnimationFrame(pendingScrollRaf);
      pendingScrollRaf = 0;
    }
    hasPendingScroll = false;
    pendingScrollForce = false;
    scrollAttemptsQueue = [];
  }

  return {
    handleScroll,
    scrollToBottom,
    setAutoScroll,
    dispose,
  };
}
