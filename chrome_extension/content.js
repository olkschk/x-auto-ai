/* X AUTO — content script.
 * Injects an "AI Reply" button into the action bar of every tweet in the feed.
 * On click, sends the tweet text to the local FastAPI server and pre-fills the
 * reply textarea with the generated answer. The user reviews and sends manually.
 */

const DEFAULT_SERVER = "http://127.0.0.1:8765";
const ENDPOINT = "/generate-reply";
const BUTTON_CLASS = "ai-reply-btn";
const INJECTED_FLAG = "data-ai-reply-injected";

let serverUrl = DEFAULT_SERVER;
chrome.storage?.sync?.get?.({ serverUrl: DEFAULT_SERVER }, (data) => {
  if (data?.serverUrl) serverUrl = data.serverUrl.replace(/\/+$/, "");
});

function showToast(message, kind = "error") {
  const node = document.createElement("div");
  node.className = "ai-reply-toast" + (kind === "success" ? " success" : "");
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

function findActionBar(tweetEl) {
  return tweetEl.querySelector('[role="group"]');
}

function extractTweetText(tweetEl) {
  const node = tweetEl.querySelector('[data-testid="tweetText"]');
  return node ? node.innerText.trim() : "";
}

// Polls the DOM until the selector matches or timeout is reached.
// Resolves with the element on success, or null on timeout.
// Uses a MutationObserver instead of a per-frame loop so we wake up only when
// the DOM actually changes — gives Draft.js a more consistent mount state by
// the time we resolve.
function waitForElement(selector, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const found = document.querySelector(selector);
    if (found) return resolve(found);

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) {
        observer.disconnect();
        resolve(el);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeoutMs);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Open the reply composer for a tweet article and fill the textarea with the
// generated text. Uses execCommand selectAll + insertText: this triggers the
// browser's native input handling, which fires beforeinput/input events that
// Draft.js (X's editor) listens to. Plain DOM mutation is ignored by Draft.js.
let __insertReqCounter = 0;

// Delegates the actual selectAll + insertText to injected.js, which runs in
// the page's MAIN world (same JS context as Draft.js). Calling execCommand
// directly from this isolated content script results in the inserted text
// being rendered twice in the editor — Draft.js doesn't preventDefault the
// beforeinput event when it comes from an isolated-world execCommand, so the
// browser's native insertion fires alongside Draft.js's React render.
function delegateInsertion(text, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const id = ++__insertReqCounter;
    const onMessage = (e) => {
      if (e.source !== window) return;
      const data = e.data;
      if (!data || data.type !== "X_AUTO_INSERT_RES" || data.id !== id) return;
      window.removeEventListener("message", onMessage);
      resolve(data);
    };
    window.addEventListener("message", onMessage);
    window.postMessage({ type: "X_AUTO_INSERT_REQ", id, text }, "*");
    setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve({ success: false, reason: "timeout" });
    }, timeoutMs);
  });
}

async function injectReplyText(article, replyText) {
  const replyButton = article.querySelector('[data-testid="reply"]');
  if (!replyButton) {
    throw new Error("Reply button not found");
  }
  replyButton.click();

  const EDITOR_SELECTOR = [
    '[data-testid="tweetTextarea_0"][contenteditable="true"]',
    '[data-testid="tweetTextarea_0"] div[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"][data-offset-key]',
  ].join(", ");

  const textbox = await waitForElement(EDITOR_SELECTOR, 6000);
  if (!textbox) {
    throw new Error("Reply textarea did not appear");
  }

  // Give Draft.js time to fully mount and register its event listeners.
  await sleep(300);

  const result = await delegateInsertion(replyText);
  if (!result.success) {
    throw new Error(result.reason || "Insertion failed in page world");
  }
}

async function callServer(tweetText) {
  const resp = await fetch(serverUrl + ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tweet_text: tweetText }),
  });
  if (!resp.ok) {
    throw new Error(`Server returned ${resp.status}`);
  }
  return resp.json();
}

async function handleReply(button, tweetEl) {
  const tweetText = extractTweetText(tweetEl);
  if (!tweetText) {
    showToast("Could not extract tweet text");
    return;
  }

  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "🤖 …";

  try {
    const data = await callServer(tweetText);
    if (data.error) {
      showToast(`AI Reply: ${data.error}`);
      return;
    }
    if (!data.reply) {
      showToast("AI Reply: empty response");
      return;
    }

    await injectReplyText(tweetEl, data.reply);
    showToast("AI Reply inserted — review and send", "success");
  } catch (e) {
    console.error("[X AUTO] handleReply failed", e);
    showToast(`AI Reply failed: ${e.message}. Is the local server running?`);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

function isTweetArticle(node) {
  return (
    node.nodeType === Node.ELEMENT_NODE &&
    node.tagName === "ARTICLE" &&
    node.getAttribute("data-testid") === "tweet"
  );
}

function injectButton(tweetEl) {
  if (tweetEl.getAttribute(INJECTED_FLAG)) return;
  tweetEl.setAttribute(INJECTED_FLAG, "1");

  const actionBar = findActionBar(tweetEl);
  if (!actionBar) return;

  const container = document.createElement("div");
  container.className = "ai-reply-row";

  const btn = document.createElement("button");
  btn.className = BUTTON_CLASS;
  btn.type = "button";
  btn.textContent = "🤖 AI Reply";
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    handleReply(btn, tweetEl);
  });

  container.appendChild(btn);

  // Insert as a sibling AFTER the action bar (not inside it). Inside the
  // [role="group"] X has its own click delegation that interferes with
  // ours and may cause stray events during the reply modal mount.
  actionBar.parentNode.insertBefore(container, actionBar.nextSibling);
}

// Process only newly-added nodes per mutation, not the entire document.
// Full-document scans during DraftJS mount cause extra DOM churn that the
// editor reacts to, leading to duplicated text on insertion.
const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (node.nodeType !== Node.ELEMENT_NODE) continue;
      if (isTweetArticle(node)) {
        injectButton(node);
      }
      node.querySelectorAll?.('article[data-testid="tweet"]').forEach(injectButton);
    }
  }
});

observer.observe(document.body, { childList: true, subtree: true });

// Initial pass for tweets already in the DOM at content-script load.
document.querySelectorAll('article[data-testid="tweet"]').forEach(injectButton);
