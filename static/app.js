/* QA console frontend — no build step, no dependencies.
   Element ids match index.html; form field names match app.py's File params. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const form = $("upload-form"), runBtn = $("run-btn"), formError = $("form-error");
  const resultsEmpty = $("results-empty"), resultsContent = $("results-content");
  const stamp = $("overall-stamp"), stageList = $("stage-list");
  const logText = $("log-text"), downloadBtn = $("download-log");
  const inputs = [...form.querySelectorAll("input[type=file]")];

  const SKIPPED_TITLES = {
    ingestion: "STAGE 1: INGESTION (weather_records)",
    lagged_long: "STAGE 2a: PROCESSING (lagged features, long)",
    lagged_wide: "STAGE 2b: PROCESSING (lagged features, wide)",
    merge: "STAGE 3: VALIDATION (outbreak_dataset_v1)",
  };
  let lastLog = "";

  /* live data-store status in the masthead */
  fetch("/api/health").then((r) => r.json()).then((h) => {
    const n = Object.values(h.datasets).filter((d) => d.loaded).length;
    $("store-status").textContent = `data store: ${n}/${Object.keys(h.datasets).length} datasets loaded`;
  }).catch(() => { $("store-status").textContent = "data store: unreachable"; });

  /* file slots */
  inputs.forEach((input) => input.addEventListener("change", () => {
    const slot = input.closest(".slot"), chosen = slot.querySelector(".slot__chosen");
    const f = input.files[0];
    slot.classList.toggle("has-file", !!f);
    chosen.textContent = f ? `${f.name} · ${(f.size / 1024).toFixed(0)} kB` : "No file selected";
  }));

  /* run */
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formError.hidden = true;
    if (!inputs.some((i) => i.files[0])) return showError("Load at least one CSV before running checks.");

    const label = runBtn.textContent;
    runBtn.disabled = true; runBtn.textContent = "Running checks…";
    try {
      const res = await fetch("/api/run", { method: "POST", body: new FormData(form) });
      const body = await res.json();
      if (!res.ok) return showError(typeof body.detail === "string" ? body.detail : "Run failed.");
      render(body);
    } catch (err) {
      showError(`Request failed — is uvicorn running? (${err.message})`);
    } finally {
      runBtn.disabled = false; runBtn.textContent = label;
    }
  });

  function showError(msg) { formError.textContent = msg; formError.hidden = false; }

  /* render */
  function render(result) {
    resultsEmpty.hidden = true;
    resultsContent.hidden = false;
    const passed = result.overall.passed;
    stamp.className = `stamp ${passed ? "pass" : "fail"}`;
    stamp.textContent = passed ? "PASS" : "FAIL";
    stamp.title = result.overall.summary;

    stageList.innerHTML = "";
    orderStages(result).forEach((s) => stageList.appendChild(stageEl(s)));

    lastLog = result.log_text || "";
    renderLog(lastLog);
  }

  function orderStages(result) {
    const byKey = Object.fromEntries((result.stages || []).map((s) => [s.key, s]));
    return ["ingestion", "lagged_long", "lagged_wide", "merge"].map((key) =>
      byKey[key] || ((result.skipped || []).includes(key)
        ? { key, title: SKIPPED_TITLES[key], checks: [], warnings: [], skipped: true }
        : null)
    ).filter(Boolean);
  }

  function stageEl(stage) {
    const el = document.createElement("div");
    el.className = `stage ${stage.skipped ? "skipped" : stage.passed ? "pass" : "fail"}`;

    const header = document.createElement("div");
    header.className = "stage__header";
    header.setAttribute("role", "button"); header.tabIndex = 0;
    header.setAttribute("aria-expanded", "true");

    const title = document.createElement("span");
    title.className = "stage__title"; title.textContent = stage.title;
    const badge = document.createElement("span");
    badge.className = "stage__badge";
    badge.textContent = stage.skipped ? "skipped" : stage.passed ? "pass" : "fail";
    header.append(title, badge);

    const body = document.createElement("div");
    body.className = "stage__body";
    let i = 0;

    if (stage.skipped) {
      const note = document.createElement("div");
      note.className = "check-row__detail";
      note.style.padding = ".5rem 0";
      note.textContent = "Inputs not supplied — stage skipped, not failed.";
      body.appendChild(note);
    }
    for (const c of stage.checks) {
      body.appendChild(row(`check-row ${c.status}`, c.status === "pass" ? "✓" : "✕", c.label, c.detail, i++));
    }
    for (const w of stage.warnings) {
      body.appendChild(row("warn-row", "⚠", w.label, w.detail, i++));
    }

    const toggle = () => {
      body.hidden = !body.hidden;
      header.setAttribute("aria-expanded", String(!body.hidden));
    };
    header.addEventListener("click", toggle);
    header.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });

    el.append(header, body);
    return el;
  }

  function row(cls, glyph, label, detail, i) {
    const el = document.createElement("div");
    el.className = cls;
    el.style.animationDelay = `${i * 40}ms`;
    const g = document.createElement("span");
    g.className = cls.startsWith("warn") ? "warn-row__glyph" : "check-row__glyph";
    g.textContent = glyph;
    const l = document.createElement("span");
    l.className = "check-row__label"; l.textContent = label;
    if (detail) {
      const d = document.createElement("span");
      d.className = "check-row__detail"; d.textContent = detail;
      l.appendChild(d);
    }
    el.append(g, l);
    return el;
  }

  function renderLog(text) {
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    logText.innerHTML = esc(text).split("\n").map((line) => {
      if (/^PASS/.test(line)) return `<span class="lg-pass">${line}</span>`;
      if (/^FAIL/.test(line)) return `<span class="lg-fail">${line}</span>`;
      if (/^WARN/.test(line)) return `<span class="lg-warn">${line}</span>`;
      if (/^SKIP/.test(line)) return `<span class="lg-skip">${line}</span>`;
      return line;
    }).join("\n");
  }

  downloadBtn.addEventListener("click", () => {
    const blob = new Blob([lastLog || "(no run yet)"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dq_checks_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.log`;
    a.click();
    URL.revokeObjectURL(url);
  });
})();