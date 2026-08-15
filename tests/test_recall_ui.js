const test = require("node:test");
const assert = require("node:assert/strict");
const {
  createSearchRunner,
  matchLabel,
  moveSelection,
  relatedPreview,
  resultUrl,
  sourceLabel,
} = require("../extension/recall-state.js");

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

test("exact and semantic results retain useful human-facing context", () => {
  assert.equal(sourceLabel({ source_platform: "x" }), "X");
  assert.equal(
    matchLabel({ relevance: { matched_terms: ["product", "interfaces"] } }),
    "Matched: product · interfaces"
  );
  assert.equal(
    matchLabel({ relevance: { reasons: ["meaning is similar to the query"] } }),
    "Matched by meaning"
  );
});

test("result payloads may carry a distinct user note without affecting URL behavior", () => {
  const result = {
    source_url: "https://example.com/memory",
    user_note: "use this for Footnote onboarding",
  };
  assert.equal(result.user_note, "use this for Footnote onboarding");
  assert.equal(resultUrl(result), "https://example.com/memory");
});

test("related previews prefer a personal thought over generated excerpts", () => {
  assert.equal(
    relatedPreview({ user_note: "take mom here", excerpt: "Restaurant review" }),
    "take mom here"
  );
  assert.equal(relatedPreview({ excerpt: "Restaurant review" }), "Restaurant review");
});

test("rapid typing ignores an older response even when it resolves last", async () => {
  const resolvers = new Map();
  const rendered = [];
  const runner = createSearchRunner({
    delay: 0,
    search(query) {
      return new Promise((resolve) => resolvers.set(query, resolve));
    },
    onSearching() {},
    onResults(results) { rendered.push(results); },
    onError(error) { throw error; },
  });

  runner.schedule("invis");
  await wait(5);
  runner.schedule("invisible products");
  await wait(5);
  resolvers.get("invisible products")([{ id: "new" }]);
  await wait(0);
  resolvers.get("invis")([{ id: "old" }]);
  await wait(0);

  assert.deepEqual(rendered, [[{ id: "new" }]]);
});

test("empty queries produce the intentional initial state without a request", () => {
  let searches = 0;
  let state;
  const runner = createSearchRunner({
    search() { searches += 1; },
    onSearching() {},
    onResults(results, metadata) { state = { results, metadata }; },
    onError() {},
  });
  runner.schedule("   ");
  assert.equal(searches, 0);
  assert.deepEqual(state, { results: [], metadata: { initial: true } });
});

test("offline errors are surfaced while aborted work stays quiet", async () => {
  const errors = [];
  const runner = createSearchRunner({
    delay: 0,
    async search() { throw new TypeError("Failed to fetch"); },
    onSearching() {},
    onResults() {},
    onError(error) { errors.push(error.message); },
  });
  runner.schedule("memory");
  await wait(5);
  assert.deepEqual(errors, ["Failed to fetch"]);
});

test("keyboard selection wraps and safe original URLs are preserved", () => {
  assert.equal(moveSelection(-1, 1, 3), 0);
  assert.equal(moveSelection(0, -1, 3), 2);
  assert.equal(moveSelection(2, 1, 3), 0);
  assert.equal(resultUrl({ source_url: "https://example.com/a" }), "https://example.com/a");
  assert.equal(resultUrl({ source_url: "javascript:alert(1)" }), null);
});
