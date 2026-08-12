(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OrbitRecall = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function createSearchRunner({ search, onSearching, onResults, onError, delay = 220 }) {
    let timer = null;
    let controller = null;
    let sequence = 0;

    function cancel() {
      sequence += 1;
      if (timer) clearTimeout(timer);
      timer = null;
      if (controller) controller.abort();
      controller = null;
    }

    function schedule(query) {
      cancel();
      const current = sequence;
      const value = query.trim();
      if (!value) {
        onResults([], { initial: true });
        return;
      }

      timer = setTimeout(async () => {
        controller = new AbortController();
        onSearching();
        try {
          const results = await search(value, controller.signal);
          if (current === sequence) onResults(results, { initial: false });
        } catch (error) {
          if (error?.name !== "AbortError" && current === sequence) onError(error);
        }
      }, delay);
    }

    return { schedule, cancel };
  }

  function moveSelection(current, direction, count) {
    if (!count) return -1;
    if (current < 0) return direction > 0 ? 0 : count - 1;
    return (current + direction + count) % count;
  }

  function sourceLabel(entry) {
    const platform = (entry.source_platform || "").toLowerCase();
    if (platform === "x") return "X";
    if (platform === "youtube") return "YouTube";
    if (platform === "medium") return "Medium";
    if (platform === "substack") return "Substack";
    if (platform && platform !== "other") return platform[0].toUpperCase() + platform.slice(1);
    try {
      return new URL(entry.source_url).hostname.replace(/^www\./, "");
    } catch {
      return "Web";
    }
  }

  function savedDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    }).format(date);
  }

  function matchLabel(entry) {
    const relevance = entry.relevance || {};
    const terms = (relevance.matched_terms || []).filter(Boolean).slice(0, 4);
    if (terms.length) return `Matched: ${terms.join(" · ")}`;
    const reasons = relevance.reasons || [];
    if (reasons.some((reason) => reason.includes("meaning") || reason.includes("semantic"))) {
      return "Matched by meaning";
    }
    if (reasons.some((reason) => reason.includes("title"))) return "Matched in title";
    return "";
  }

  function resultUrl(entry) {
    try {
      const url = new URL(entry?.source_url || "");
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch {
      return null;
    }
  }

  function relatedPreview(entry) {
    return String(entry?.user_note || entry?.excerpt || "").trim();
  }

  return {
    createSearchRunner,
    moveSelection,
    sourceLabel,
    savedDate,
    matchLabel,
    resultUrl,
    relatedPreview,
  };
});
