(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OrbitPageContext = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const MAX_CONTEXT_TEXT = 6000;
  const PRIVATE_HOSTS = [
    "x.com", "twitter.com", "facebook.com", "instagram.com", "linkedin.com",
    "mail.google.com", "outlook.live.com", "outlook.office.com", "slack.com",
    "discord.com", "web.whatsapp.com", "messenger.com", "bankofamerica.com",
    "chase.com", "paypal.com",
  ];
  const AUTH_PATH = /\/(?:login|log-in|signin|sign-in|signup|sign-up|auth|oauth|password|checkout|payment)(?:\/|$)/i;
  const PRIVATE_PATH = /\/(?:messages?|inbox|direct|dm|notifications|settings|search)(?:\/|$)/i;

  function isPrivateHost(hostname) {
    const host = String(hostname || "").toLowerCase().replace(/^www\./, "");
    return PRIVATE_HOSTS.some((blocked) => host === blocked || host.endsWith(`.${blocked}`));
  }

  function isSupportedUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "http:" && url.protocol !== "https:") return false;
      const host = url.hostname.toLowerCase();
      if (!host || host === "localhost" || host === "127.0.0.1" || host === "::1") return false;
      if (isPrivateHost(host) || AUTH_PATH.test(url.pathname) || PRIVATE_PATH.test(url.pathname)) return false;
      return true;
    } catch {
      return false;
    }
  }

  function compact(value, maximum) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  }

  function buildContext({ url, title, description, text }) {
    let cleanUrl = String(url || "");
    try {
      const parsed = new URL(cleanUrl);
      parsed.search = "";
      parsed.hash = "";
      cleanUrl = parsed.href;
    } catch {
      cleanUrl = "";
    }
    return {
      url: cleanUrl.slice(0, 2048),
      title: compact(title, 500),
      description: compact(description, 1000),
      text: compact(text, MAX_CONTEXT_TEXT),
    };
  }

  function isMeaningfulContext(context) {
    return Boolean(
      context?.url
      && context?.title
      && (context.text.length >= 250 || context.description.length >= 100)
    );
  }

  function fingerprint(context) {
    const value = `${context.url}\n${context.title}\n${context.description}\n${context.text}`;
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16);
  }

  return { MAX_CONTEXT_TEXT, buildContext, fingerprint, isMeaningfulContext, isSupportedUrl };
});
