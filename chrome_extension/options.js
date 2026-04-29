const DEFAULT_URL = "http://127.0.0.1:8765";

const input = document.getElementById("server");
const status = document.getElementById("status");
const saveBtn = document.getElementById("save");

chrome.storage.sync.get({ serverUrl: DEFAULT_URL }, (data) => {
  input.value = data.serverUrl || DEFAULT_URL;
});

saveBtn.addEventListener("click", () => {
  const url = (input.value || DEFAULT_URL).trim().replace(/\/+$/, "");
  chrome.storage.sync.set({ serverUrl: url }, () => {
    status.textContent = "Saved";
    setTimeout(() => (status.textContent = ""), 1500);
  });
});
