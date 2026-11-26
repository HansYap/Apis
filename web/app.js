"use strict";

const $ = (id) => document.getElementById(id);
let busy = false;
let ready = false;
let readerInstalled = false;
let topicModelInstalled = false;
let topicTipShown = false;
let currentStep = "transcript";
let highestStep = 0;
let preparedSignature = null;
let vaultPath = "";
let noteSaved = false;  // The current note is in the vault; saving again would only add a numbered copy.
const steps = ["transcript", "prompt", "save"];
const launcher = navigator.userAgent.includes("Windows") ? "Start Apis.cmd" : "start-apis.sh";
const formFields = ["video-url", "video-title", "transcript", "question", "note"];

function status(message, kind = "") {
  $("status").className = kind;
  $("status-text").textContent = message;
}

async function api(method, ...args) {
  if (!window.pywebview?.api) throw new Error(`Open ${launcher} to use captions and local files. This is a design preview.`);
  const response = await window.pywebview.api[method](...args);
  if (!response.ok) throw new Error(response.error);
  return response.data;
}

function setBusy(value) {
  busy = value;
  document.body.classList.toggle("busy", value);
  document.querySelectorAll("button, input, textarea").forEach((node) => {
    node.disabled = value;
  });
  $("continue-save").disabled = value || !preparedSignature;
  $("save").disabled = value || noteSaved;
  $("tools-menu").hidden = true;
  $("tools-toggle").setAttribute("aria-expanded", "false");
}

async function run(message, work) {
  if (busy) return;
  setBusy(true);
  status(message, "busy");
  try {
    await work();
  } catch (error) {
    status(error.message || "Something went wrong. Please try again.", "error");
  } finally {
    setBusy(false);
    $("status").classList.remove("busy");
  }
}

function payload() {
  return {url: $("video-url").value, title: $("video-title").value.trim(),
    transcript: $("transcript").value,
    mode: document.querySelector('input[name="mode"]:checked').value,
    question: $("question").value, vault: vaultPath, note: $("note").value};
}

function showStep(step) {
  if (!steps.includes(step) || steps.indexOf(step) > highestStep) return;
  if (step === "save" && preparedSignature !== signature()) return;
  currentStep = step;
  for (const name of steps) $("page-" + name).hidden = name !== step;
  document.querySelectorAll(".step-nav li").forEach((indicator) => {
    if (indicator.dataset.step === step) indicator.setAttribute("aria-current", "step");
    else indicator.removeAttribute("aria-current");
  });
  $("new-video").hidden = step === "transcript";
  updateContext();
  window.scrollTo({top: 0, behavior: "instant"});
}

function updateContext() {
  const text = $("transcript").value.trim();
  const words = text ? text.split(/\s+/).length : 0;
  $("word-count").textContent = words ? `${words.toLocaleString()} words` : "Ready when you are";
  $("source-context").textContent = $("video-url").value.trim()
    ? `${$("video-title").value.trim() || "Your video"} · ${$("video-url").value.trim()}`
    : "Add a video link in Transcript to keep your source connected.";
  $("vault").textContent = vaultPath || "No folder selected";
  $("vault").title = vaultPath;
}

function signature() {
  const data = payload();
  return JSON.stringify([data.url, data.title, data.transcript, data.mode, data.question]);
}

function markSaved(saved) {
  noteSaved = saved;
  const button = $("save");
  button.classList.toggle("done", saved);
  button.disabled = saved || busy;
  button.querySelector("span").textContent = saved ? "Saved to Obsidian" : "Save & connect";
  button.querySelector("use").setAttribute("href", saved ? "#check" : "#arrow");
  if (!saved) $("saved-result").hidden = true;
}

function invalidatePreparation() {
  preparedSignature = null;
  highestStep = Math.min(highestStep, 1);
  markSaved(false);
  for (const name of ["prepared-result", "prompt-fallback"]) $(name).hidden = true;
  $("generated-prompt").value = "";
  $("prepared-file").textContent = "";
  $("continue-save").disabled = true;
}

function confirmAction(title, message, action) {
  $("dialog-title").textContent = title;
  $("dialog-message").textContent = message;
  $("dialog-confirm").textContent = action;
  const dialog = $("confirm-dialog");
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true});
    dialog.showModal();
  });
}

function requireVideo() {
  if (!$("video-url").value.trim()) {
    showStep("transcript");
    $("video-url").focus();
    status("Paste a YouTube video link to get started.", "error");
    return false;
  }
  return true;
}

async function fetchTranscript(event) {
  event.preventDefault();
  if (busy || !requireVideo()) return;
  if (!ready) { status(`Open ${launcher} to retrieve captions. This is a design preview.`, "error"); return; }
  if (!readerInstalled && !(await confirmAction("One quick setup.", "Install the free yt-dlp caption reader from PyPI? It stays inside Apis’ folder. You can also import or paste captions without installing it.", "Install & continue"))) return;
  let languageMissing = false;
  await run(readerInstalled ? "Finding the words behind the video…" : "Installing the caption reader…", async () => {
    if (!readerInstalled) {
      await api("install_reader");
      readerInstalled = true;
      status("Finding the words behind the video…", "busy");
    }
    let result;
    try {
      result = await api("fetch", $("video-url").value, $("caption-language").value.trim());
    } catch (error) {
      languageMissing = error.message.includes("No caption track matches");
      if (languageMissing) $("video-details").open = true;
      throw error;
    }
    $("video-url").value = result.url;
    if (!$("video-title").value.trim()) $("video-title").value = result.title;  // A title the user typed wins.
    $("transcript").value = result.text;
    invalidatePreparation();
    updateContext();
    status(`Transcript ready. ${result.track}.`);
  });
  if (languageMissing) { $("caption-language").focus(); $("caption-language").select(); }
}

async function chooseVault() {
  await run("Choose the folder you open in Obsidian…", async () => {
    const result = await api("choose_vault");
    if (result) {
      if (result.vault !== vaultPath) markSaved(false);  // A different vault does not have this note yet.
      vaultPath = result.vault;
      updateContext();
      status("Vault connected. Your ideas have a place to land.");
    } else status("Vault selection cancelled.");
  });
}

async function importFile(kind) {
  await run(kind === "transcript" ? "Choose a transcript file…" : "Choose a Markdown file…", async () => {
    const result = await api("import_file", kind);
    if (result) {
      $(kind === "transcript" ? "transcript" : "note").value = result.text;
      if (kind === "transcript") invalidatePreparation();
      else markSaved(false);
      updateContext();
      status(`Imported ${result.name}.`);
    } else status("Import cancelled.");
  });
}

async function prepare() {
  if (!requireVideo()) return;
  if (!$("transcript").value.trim()) {
    showStep("transcript"); $("transcript").focus();
    status("Fetch, import, or paste a transcript first.", "error"); return;
  }
  if (payload().mode === "Answer a question" && !$("question").value.trim()) {
    $("question").focus(); status("Add the question you want your AI model to answer.", "error"); return;
  }
  await run("Preparing the prompt…", async () => {
    const result = await api("prepare", payload());
    let copied = result.copied;
    if (!copied) {
      try { await navigator.clipboard.writeText(result.prompt); copied = true; } catch (_) { /* Manual copy stays available. */ }
    }
    $("generated-prompt").value = result.prompt;
    $("prompt-fallback").hidden = copied;
    $("prompt-fallback").open = !copied;
    $("prepared-title").textContent = copied ? "Prompt copied." : "Copy the prompt below.";
    $("prepared-file").textContent = `Includes the transcript (${result.words.toLocaleString()} words).` + linkingSummary(result.linking);
    preparedSignature = signature();
    highestStep = 2;
    $("prepared-result").hidden = false;
    let message = copied ? "Ready. Paste it into your AI chat and send, then continue to Obsidian." : "Clipboard access failed; select and copy the prompt below.";
    if (result.linking.problem) message += `\n${result.linking.problem} Word matching was used instead.`;
    else if (result.linking.method === "words" && !topicModelInstalled && !topicTipShown) {
      topicTipShown = true;
      message += "\nTip: Install topic matcher (⋯ menu beside Import file) to suggest links by meaning, not just shared words.";
    }
    status(message);
  });
}

function linkingSummary(linking) {
  const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  if (linking.method === "model") {
    return linking.count
      ? ` ${plural(linking.count, "suggested link")} from your vault (${linking.strong} strong) for your AI model to check.`
      : " No close matches in your vault, so your AI model will create new links.";
  }
  if (linking.method === "words") return linking.count ? ` ${plural(linking.count, "vault topic")} matched by shared words.` : "";
  return "";
}

async function saveNote() {
  if (currentStep !== "save" || preparedSignature !== signature() || noteSaved) return;
  if (!requireVideo()) return;
  if (!vaultPath) { $("choose-vault").focus(); status("Choose your Obsidian vault folder first.", "error"); return; }
  if (!$("note").value.trim()) { $("note").focus(); status("Paste the Markdown from your AI model before saving.", "error"); return; }
  await run("Giving your ideas a home…", async () => {
    const result = await api("save", payload());
    $("saved-title").textContent = result.filename;
    const topics = (count) => `${count} topic${count === 1 ? "" : "s"}`;
    $("saved-detail").textContent = result.linked
      ? `${topics(result.linked)} linked` + (result.existing ? ` · ${result.existing} already in your vault` : "")
      : "Saved. Add [[Topic links]] in your note to create graph connections.";
    $("saved-result").hidden = false;
    markSaved(true);
    $("saved-result").scrollIntoView({block: "nearest", behavior: "smooth"});
    status("Saved to your vault. Another idea, connected.");
    updateContext();
  });
}

async function reset() {
  if (busy) return;
  const hasWork = $("transcript").value.trim() || $("note").value.trim();
  if (hasWork && !(await confirmAction(
    "Start a new video?",
    "This clears the transcript and note from the workspace. Files you already saved stay where they are.",
    "Clear & start fresh"))) return;
  await run("Starting fresh…", async () => {
    await api("reset");
    for (const name of formFields) $(name).value = "";
    $("caption-language").value = "";
    $("video-details").open = false;
    document.querySelector('input[name="mode"]').checked = true;
    $("question").required = false;
    $("question-field").hidden = true;
    invalidatePreparation();
    highestStep = 0;
    $("prompt-fallback").open = false;
    $("saved-title").textContent = "A new connection.";
    $("saved-detail").textContent = "";
    updateContext(); showStep("transcript");
    status("A little curiosity goes a long way.");
  });
  $("video-url").focus();
}

formFields.forEach((name) => $(name).addEventListener("input", () => {
  if (name === "video-url") $("video-title").value = "";  // A new video needs a new title.
  if (name !== "note") invalidatePreparation();
  else markSaved(false);
  updateContext();
}));
$("link-form").addEventListener("submit", fetchTranscript);
// Enter in the title must not start a fetch, which would replace a pasted transcript.
$("video-title").addEventListener("keydown", (event) => { if (event.key === "Enter") event.preventDefault(); });
$("continue-prompt").addEventListener("click", async () => {
  if (!requireVideo()) return;
  if (!$("transcript").value.trim()) { $("transcript").focus(); status("Fetch, import, or paste a transcript first.", "error"); return; }
  await run("Checking your transcript…", async () => {
    const result = await api("validate_transcript", $("video-url").value, $("transcript").value);
    $("video-url").value = result.url;
    $("transcript").value = result.text;
    if (preparedSignature && preparedSignature !== signature()) invalidatePreparation();
    highestStep = Math.max(highestStep, 1);
    showStep("prompt"); status("Choose a reading style. Follow your curiosity.");
  });
});
$("continue-save").addEventListener("click", () => showStep("save"));
$("back-transcript").addEventListener("click", () => showStep("transcript"));
$("back-prompt").addEventListener("click", () => showStep("prompt"));
$("new-video").addEventListener("click", reset);
$("choose-vault").addEventListener("click", chooseVault);
$("import-transcript").addEventListener("click", () => importFile("transcript"));
$("import-note").addEventListener("click", () => importFile("note"));
$("prepare").addEventListener("click", prepare);
$("save").addEventListener("click", saveNote);
$("open-video").addEventListener("click", () => run("Opening your video…", async () => { await api("open_external", "youtube", $("video-url").value); status("In YouTube, choose Show transcript if you need to copy captions."); }));
$("open-obsidian").addEventListener("click", () => run("Opening your note in Obsidian…", async () => { await api("open_external", "obsidian"); status("Your note should now be open in Obsidian."); }));
$("open-note").addEventListener("click", () => run("Opening your saved notes…", async () => { await api("open_folder", "note"); status("Your saved note folder is open."); }));
$("install-reader").addEventListener("click", async () => {
  $("tools-menu").hidden = true;
  $("tools-toggle").setAttribute("aria-expanded", "false");
  if (await confirmAction("Caption reader setup", "Install or update yt-dlp from PyPI inside Apis’ folder?", "Install / update")) {
    await run("Setting up the caption reader…", async () => { await api("install_reader"); readerInstalled = true; status("Caption reader ready. Paste a link and get started."); });
  }
});
$("install-topic-model").addEventListener("click", async () => {
  $("tools-menu").hidden = true;
  $("tools-toggle").setAttribute("aria-expanded", "false");
  if (await confirmAction("Topic matcher setup", "Download a small multilingual model (about 450 MB with its runtime) into Apis’ folder? It runs on this computer and suggests which of your existing topics fit each video. Transcripts never leave your computer for this step.", "Install / update")) {
    await run("Setting up the topic matcher… This can take a few minutes.", async () => {
      topicModelInstalled = (await api("install_topic_model")).topic_model_installed;
      status("Topic matcher ready. Copy a prompt to get link suggestions from your vault.");
    });
  }
});
$("confirm-dialog").addEventListener("click", (event) => {
  // A click on the backdrop targets the dialog itself but lands outside its box: treat it as Cancel.
  const box = event.currentTarget.getBoundingClientRect();
  const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  if (event.target === event.currentTarget && outside) event.currentTarget.close("cancel");
});
$("tools-toggle").addEventListener("click", () => { const menu = $("tools-menu"); menu.hidden = !menu.hidden; $("tools-toggle").setAttribute("aria-expanded", String(!menu.hidden)); });
document.addEventListener("click", (event) => { if (!event.target.closest(".menu-wrap")) { $("tools-menu").hidden = true; $("tools-toggle").setAttribute("aria-expanded", "false"); } });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { $("tools-menu").hidden = true; $("tools-toggle").setAttribute("aria-expanded", "false"); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "l") { event.preventDefault(); if (!busy) { showStep("transcript"); $("video-url").focus(); $("video-url").select(); } }
});
document.querySelectorAll('input[name="mode"]').forEach((radio) => radio.addEventListener("change", () => {
  const required = radio.value === "Answer a question" && radio.checked;
  $("question-field").hidden = !required;
  $("question").required = required;
  if (!required) $("question").value = "";
  invalidatePreparation();
  if (required) $("question").focus();
}));

async function initialize() {
  if (ready) return;
  ready = true;
  try {
    const data = await api("initialize");
    vaultPath = data.vault;
    readerInstalled = data.reader_installed;
    topicModelInstalled = data.topic_model_installed;
    updateContext();
    status("A little curiosity goes a long way.");
  } catch (error) { status(error.message, "error"); }
}
// The pinned header gets its backing only once content scrolls under it.
window.addEventListener("scroll", () => document.body.classList.toggle("scrolled", window.scrollY > 4), {passive: true});
window.addEventListener("pywebviewready", initialize);
if (window.pywebview?.api) initialize();
setTimeout(() => { if (!ready) status(`Design preview · Open ${launcher} to work with captions and local files.`); }, 1200);
