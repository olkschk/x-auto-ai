/* X AUTO — page-world script (runs with world: "MAIN").
 *
 * The content.js content script lives in an isolated JS world, and Draft.js
 * (X's editor) lives in the page world. From isolated world, calling
 * execCommand('insertText') causes Draft.js to render the inserted text
 * twice in the DOM — apparently the synthesized beforeinput event isn't
 * trusted enough for Draft.js's preventDefault to suppress the native
 * browser insertion, so both paths fire.
 *
 * Running the insertion from MAIN world is indistinguishable from the page
 * doing it itself. We dispatch a synthetic paste event with text/plain in a
 * DataTransfer; Draft.js's onPaste replaces the current selection in a
 * single setState and calls preventDefault — single render, no duplication.
 */

(function () {
  const REQ = "X_AUTO_INSERT_REQ";
  const RES = "X_AUTO_INSERT_RES";

  function findEditor() {
    const candidates = [
      '[data-testid="tweetTextarea_0"][contenteditable="true"]',
      '[data-testid="tweetTextarea_0"] div[contenteditable="true"]',
      'div[role="textbox"][contenteditable="true"]',
      'div[contenteditable="true"][data-offset-key]',
    ];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function performInsertion(text) {
    const editor = findEditor();
    if (!editor) {
      return { success: false, reason: "Editor not found in MAIN world" };
    }

    editor.focus();
    document.execCommand("selectAll", false, null);

    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    const pasteEvent = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData: dt,
    });
    editor.dispatchEvent(pasteEvent);

    return { success: true };
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.type !== REQ || typeof data.id !== "number") return;

    let result;
    try {
      result = performInsertion(String(data.text || ""));
    } catch (err) {
      result = { success: false, reason: String(err?.message || err) };
    }
    window.postMessage({ type: RES, id: data.id, ...result }, "*");
  });
})();
