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

async function waitForElement(selector, root = document, timeoutMs = 5000) {
  const start = performance.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const el = root.querySelector(selector);
      if (el) return resolve(el);
      if (performance.now() - start > timeoutMs) {
        return reject(new Error(`Timed out waiting for ${selector}`));
      }
      requestAnimationFrame(tick);
    };
    tick();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Open the reply composer for a tweet article and fill the textarea with the
// generated text. Uses execCommand selectAll + insertText: this triggers the
// browser's native input handling, which fires beforeinput/input events that
// Draft.js (X's editor) listens to. Plain DOM mutation is ignored by Draft.js.
async function injectReplyText(article, replyText) {
  const replyButton = article.querySelector('[data-testid="reply"]');
  if (!replyButton) {
    throw new Error("Reply button not found");
  }
  replyButton.click();

  const EDITOR_SELECTOR = [
    '[data-testid="tweetTextarea_0"] div[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"][data-offset-key]',
  ].join(", ");

  const textbox = await waitForElement(EDITOR_SELECTOR, document, 6000);
  if (!textbox) {
    throw new Error("Reply textarea did not appear");
  }

  // Let Draft.js fully mount, register its event listeners, and finish any
  // pre-fill (e.g. @mention insertion) before we touch the editor.
  await sleep(500);

  textbox.focus();

  // Draft.js needs its own selectAll path to establish editor-owned selection
  // (the Range API alone doesn't make Draft.js treat the editor as the input
  // target, so insertText becomes a no-op). After that, delete the selection
  // explicitly and let a fresh microtask settle before inserting — this avoids
  // a known Draft.js render quirk where selectAll + insertText fired back-to-
  // back can duplicate the text in the DOM.
  document.execCommand("selectAll", false, null);
  document.execCommand("delete", false, null);
  await sleep(0);
  document.execCommand("insertText", false, replyText);
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

function injectButton(tweetEl) {
  if (tweetEl.getAttribute(INJECTED_FLAG)) return;
  const bar = findActionBar(tweetEl);
  if (!bar) return;
  if (bar.querySelector(`.${BUTTON_CLASS}`)) {
    tweetEl.setAttribute(INJECTED_FLAG, "1");
    return;
  }

  const btn = document.createElement("button");
  btn.className = BUTTON_CLASS;
  btn.type = "button";
  btn.textContent = "🤖 AI Reply";
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    handleReply(btn, tweetEl);
  });

  bar.appendChild(btn);
  tweetEl.setAttribute(INJECTED_FLAG, "1");
}

function scanAndInject() {
  document
    .querySelectorAll('article[data-testid="tweet"]')
    .forEach(injectButton);
}

const observer = new MutationObserver(() => scanAndInject());
observer.observe(document.body, { childList: true, subtree: true });
scanAndInject();
