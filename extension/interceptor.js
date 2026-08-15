(function () {
  const GRAPHQL_URL = "graphql";
  const TIMELINE_URL = "HomeTimeline";
  const DETAIL_URL = "TweetDetail";
  const BOOKMARK_OPERATION = "CreateBookmark";

  const tweetCache = new Map();
  console.log(`[Footnote] Memory cache initialized. Size: ${tweetCache.size}`);

  const originalFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input?.url || "";
    if (!url.includes(GRAPHQL_URL) && !url.includes(TIMELINE_URL) && !url.includes(DETAIL_URL) && !url.includes(BOOKMARK_OPERATION)) {
      return originalFetch.apply(this, arguments);
    }
    if (url.includes(BOOKMARK_OPERATION) || isBookmarkRequest(init)) {
      const tweetId = extractTweetIdFromRequest(init);
      if (tweetId) {
        const cachedTweet = tweetCache.get(tweetId);
        if (cachedTweet) {
          const payload = normalizeTweetPayload(cachedTweet);
          window.postMessage({ source: "footnote-interceptor", type: "bookmark-captured", payload }, "*");
        }
      }
    }
    const response = await originalFetch.apply(this, arguments);
    try {
      const clonedResponse = response.clone();
      const json = await clonedResponse.json();
      const tweetsFound = extractAndCacheTweets(json);
      if (tweetsFound > 0) console.log(`[Footnote] Cached ${tweetsFound} tweet(s)`);
    } catch (err) {}
    return response;
  };

  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._footnoteUrl = url;
    return originalXHROpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const xhr = this;
    const url = xhr._footnoteUrl || "";
    if (url.includes(GRAPHQL_URL) || url.includes(TIMELINE_URL) || url.includes(DETAIL_URL) || url.includes(BOOKMARK_OPERATION)) {
      if (url.includes(BOOKMARK_OPERATION)) {
        const tweetId = extractTweetIdFromXhrBody(body);
        if (tweetId) {
          const cachedTweet = tweetCache.get(tweetId);
          if (cachedTweet) {
            const payload = normalizeTweetPayload(cachedTweet);
            window.postMessage({ source: "footnote-interceptor", type: "bookmark-captured", payload }, "*");
          }
        }
      }
      xhr.addEventListener("load", function () {
        try {
          const json = JSON.parse(xhr.responseText);
          const tweetsFound = extractAndCacheTweets(json);
          if (tweetsFound > 0) console.log(`[Footnote] Cached ${tweetsFound} tweet(s) from XHR`);
        } catch (err) {}
      });
    }
    return originalXHRSend.apply(this, arguments);
  };

  function isBookmarkRequest(init) {
    if (!init?.body) return false;
    try {
      const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      const opName = body?.operationName || body?.query || "";
      return opName.includes("Bookmark") || opName.includes("bookmark");
    } catch { return false; }
  }
  
  function extractTweetIdFromRequest(init) {
    if (!init?.body) return null;
    try {
      const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      return body?.variables?.tweet_id || null;
    } catch { return null; }
  }
  
  function extractTweetIdFromXhrBody(body) {
    if (!body) return null;
    try {
      const parsed = typeof body === "string" ? JSON.parse(body) : body;
      return parsed?.variables?.tweet_id || null;
    } catch { return null; }
  }

  function extractAndCacheTweets(json) {
    if (!json?.data) return 0;
    let count = 0;
    const found = findAllTweetObjects(json.data);
    for (const tweet of found) {
      const id = tweet.rest_id || tweet.id_str || tweet.id;
      if (id && tweet.legacy?.full_text) {
        // Cache legacy PLUS the author and any long-form text, both of which
        // live outside legacy. Storing bare `tweet.legacy` was why every
        // captured tweet came out authored by "unknown": the user object sits
        // at tweet.core.user_results.result, one level up, and was discarded.
        tweetCache.set(id, {
          ...tweet.legacy,
          rest_id: id,
          __user: extractUser(tweet),
          __noteText: extractNoteText(tweet),
        });
        count++;
      } else if (id && tweet.full_text) {
        tweetCache.set(id, { ...tweet, rest_id: id, __user: extractUser(tweet) });
        count++;
      }
    }
    return count;
  }

  // X has moved author fields around over time: older payloads put them in
  // user.legacy, newer ones in user.core. Check every shape we've seen.
  function extractUser(tweet) {
    const result =
      tweet.core?.user_results?.result ||
      tweet.author_community_relationship?.user_results?.result ||
      null;
    if (!result) return null;

    const legacy = result.legacy || {};
    const core = result.core || {};
    const screenName = core.screen_name || legacy.screen_name || "";
    const name = core.name || legacy.name || "";
    if (!screenName && !name) return null;

    return { screen_name: screenName, name: name || screenName };
  }

  // Tweets over 280 characters keep the full body in note_tweet; legacy
  // .full_text is truncated with an ellipsis and a t.co link.
  function extractNoteText(tweet) {
    return (
      tweet.note_tweet?.note_tweet_results?.result?.text ||
      tweet.legacy?.note_tweet?.note_tweet_results?.result?.text ||
      null
    );
  }

  function findAllTweetObjects(obj, depth = 0) {
    if (!obj || typeof obj !== "object" || depth > 50) return [];
    let results = [];
    if (obj.legacy && obj.legacy.full_text) {
      results.push(obj);
    } else if (obj.full_text && (obj.id_str || obj.rest_id || obj.id)) {
      results.push(obj);
    }
    for (const key of Object.keys(obj)) {
      const val = obj[key];
      if (val && typeof val === "object") {
        results = results.concat(findAllTweetObjects(val, depth + 1));
      }
    }
    return results;
  }

  function normalizeTweetPayload(tweet) {
    const user =
      tweet.__user ||
      tweet.user ||
      tweet.core?.user_results?.result?.legacy ||
      {};
    const handle = user.screen_name || "i";
    const author = user.name || user.screen_name || "";
    const id = tweet.rest_id || tweet.id_str || tweet.id;
    const text = tweet.__noteText || tweet.full_text || "";

    return {
      type: "tweet",
      // x.com/i/status/<id> is X's own handle-agnostic permalink, so an
      // unresolved author still yields a URL that actually opens the tweet.
      source_url: `https://x.com/${handle}/status/${id}`,
      source_platform: "x",
      author: author,
      author_handle: user.screen_name ? `@${user.screen_name}` : "",
      title: text.split("\n")[0]?.slice(0, 100) || "",
      captured_at: new Date().toISOString(),
      published_at: tweet.created_at ? new Date(tweet.created_at).toISOString() : null,
      content: text,
    };
  }
  
  console.log("[Footnote] Stable Fetch interceptor injected.");
})();