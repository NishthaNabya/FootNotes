const test = require("node:test");
const assert = require("node:assert/strict");
const {
  MAX_CONTEXT_TEXT,
  buildContext,
  fingerprint,
  isMeaningfulContext,
  isSupportedUrl,
} = require("../extension/page-context-state.js");

test("ordinary public article URLs are supported", () => {
  assert.equal(isSupportedUrl("https://example.com/articles/calm-technology"), true);
});

test("internal, local, private, social, and authentication pages are excluded", () => {
  const excluded = [
    "chrome://settings",
    "chrome-extension://abc/recall.html",
    "http://localhost:8000/entries",
    "https://x.com/messages",
    "https://mail.google.com/mail/u/0/",
    "https://example.com/login",
    "https://example.com/account/password",
    "https://example.com/messages/thread/123",
    "https://github.com/settings/profile",
    "https://example.com/search?q=private+typed+query",
    "not a URL",
  ];
  excluded.forEach((url) => assert.equal(isSupportedUrl(url), false, url));
});

test("page context is normalized, bounded, and stable when unchanged", () => {
  const input = {
    url: "https://example.com/article?session=private#comments",
    title: "  Calm   technology  ",
    description: " A useful public article. ",
    text: "word ".repeat(3000),
  };
  const context = buildContext(input);
  assert.equal(context.url, "https://example.com/article");
  assert.equal(context.title, "Calm technology");
  assert.equal(context.text.length, MAX_CONTEXT_TEXT);
  assert.equal(fingerprint(context), fingerprint(buildContext(input)));
});

test("pages without meaningful readable content are ignored", () => {
  const thin = buildContext({
    url: "https://example.com/empty",
    title: "Empty",
    description: "",
    text: "Short page",
  });
  assert.equal(isMeaningfulContext(thin), false);
});
