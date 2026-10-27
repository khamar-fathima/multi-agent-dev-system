(() => {
  const stages = {
    plan: {
      number: "01", agent: "ARCHITECT", status: "SPEC WRITTEN", title: "Make the brief testable.",
      copy: "The Architect turns the three asks into a traceable plan and a sequenced todo list. Duplicate handling, cursor behavior, and audit events each get an explicit acceptance check.",
      output: "plan.md · todo.md"
    },
    build: {
      number: "02", agent: "CODER", status: "IMPLEMENTED", title: "Build against the contract.",
      copy: "The Coder implements the API from the scoped plan: idempotent expense creation, stable cursor pagination, and an append-only audit trail. Each requirement maps to a concrete change.",
      output: "API implementation · coder_report.md"
    },
    test: {
      number: "03", agent: "TESTER", status: "2 FINDINGS", title: "Try to break the happy path.",
      copy: "The Tester reviews independently and probes replay behavior and cursor boundaries. It finds duplicate creation on a replayed key and a cursor that can skip a record under equal timestamps.",
      output: "bug_report.md · 2 actionable issues"
    },
    fix: {
      number: "04", agent: "BUG FIXER", status: "PATCHED · RETESTED", title: "Repair causes, not symptoms.",
      copy: "The fixer makes idempotency key handling atomic and adds a stable tie-breaker to cursor ordering. The Tester then reruns the targeted cases; both findings are closed.",
      output: "Patch · fix log · targeted retest"
    },
    security: {
      number: "05", agent: "SECURITY REVIEWER", status: "CLEAN", title: "Give risk its own reviewer.",
      copy: "A separate security pass checks the expense-write path, request validation, secret handling, and duplicate protection. No blocking issue remains in this illustrative review.",
      output: "Security verdict · CLEAN"
    },
    qa: {
      number: "06", agent: "QA REVIEWER", status: "APPROVE", title: "Check the original promise.",
      copy: "Senior QA compares the delivered behavior to the source requirements, not the implementation summary. The three required capabilities are covered and the change is approved.",
      output: "Shippability sign-off · APPROVE"
    },
    verify: {
      number: "07", agent: "DETERMINISTIC VERIFIER", status: "PASSING EVIDENCE", title: "Require actual passing checks.",
      copy: "The verifier runs project checks and records their results. The sample shows 18 tests passing, lint clean, and compilation clean. Missing or failed evidence would block a clean status.",
      output: "pytest · ruff · compileall"
    },
    deliver: {
      number: "08", agent: "SAFE DELIVERY", status: "READY", title: "Prepare a safe handoff.",
      copy: "A secret scan precedes publishing, with private-by-default delivery. The receiving team gets the change alongside the artifacts and verification evidence that support it.",
      output: "Secret scan · private push · handoff"
    }
  };

  const buttons = Array.from(document.querySelectorAll(".stage-item"));
  const kicker = document.querySelector("#detail-kicker");
  const status = document.querySelector("#detail-status");
  const content = document.querySelector("#detail-content");
  const counter = document.querySelector("#detail-counter");

  function selectStage(key) {
    const stage = stages[key];
    if (!stage) return;
    buttons.forEach((button) => {
      const selected = button.dataset.stage === key;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    kicker.textContent = `STAGE ${stage.number} / ${stage.agent}`;
    status.textContent = stage.status;
    counter.textContent = `${stage.number} / 08`;
    content.innerHTML = `<p class="detail-label">THE HANDOFF</p><h3>${stage.title}</h3><p class="detail-copy">${stage.copy}</p><div class="detail-output"><span>OUTPUT</span><strong>${stage.output}</strong></div>`;
  }

  buttons.forEach((button) => button.addEventListener("click", () => selectStage(button.dataset.stage)));

  // This is a fixed, front-end-only story. It does not execute commands or contact a service.
  const sampleEvents = [
    { stage: "plan", time: "00:00", tag: "INPUT", text: "Fixed brief accepted: expense API, duplicate protection, cursor history, audit trail." },
    { stage: "plan", time: "00:04", tag: "ARCHITECT", text: "Acceptance checks mapped to all three requirements." },
    { stage: "plan", time: "00:08", tag: "OUTPUT", text: "Illustrative artifacts: plan.md · todo.md" },
    { stage: "build", time: "00:14", tag: "CODER", text: "Implementation described: expense write path, stable history cursor, append-only audit events." },
    { stage: "build", time: "00:22", tag: "OUTPUT", text: "Illustrative artifacts: expense_api.py · coder_report.md" },
    { stage: "test", time: "00:30", tag: "TESTER", text: "Independent review probes replayed idempotency keys and equal-timestamp cursor boundaries." },
    { stage: "test", time: "00:37", tag: "FINDING 01", text: "A replayed key could create a duplicate expense. Routed to Bug Fixer." },
    { stage: "test", time: "00:43", tag: "FINDING 02", text: "Equal timestamps could make history pagination skip a record. Routed to Bug Fixer." },
    { stage: "fix", time: "00:51", tag: "BUG FIXER", text: "Patch described: make idempotency handling atomic; add a stable cursor tie-breaker." },
    { stage: "fix", time: "00:59", tag: "RETEST", text: "Tester rechecks both targeted cases. Sample findings closed." },
    { stage: "security", time: "01:08", tag: "SECURITY REVIEW", text: "Separate review checks secret exposure, input validation, write-path risk, and duplicate protection." },
    { stage: "security", time: "01:16", tag: "GATE", text: "Illustrative verdict: CLEAN. No blocking issue in this sample." },
    { stage: "qa", time: "01:23", tag: "SENIOR QA", text: "Original requirements checked against the described behavior. Sample verdict: APPROVE." },
    { stage: "verify", time: "01:31", tag: "VERIFIER", text: "Illustrative evidence: 18 tests passed; lint and compilation clean. No commands run by this page." },
    { stage: "deliver", time: "01:41", tag: "DELIVERY", text: "Illustrative sequence: secret scan → private-by-default push → evidence handoff." },
    { stage: "deliver", time: "01:48", tag: "SAMPLE COMPLETE", text: "Fixed walkthrough finished. This is not a real run or an execution result." }
  ];
  const playToggle = document.querySelector("#play-toggle");
  const playLabel = document.querySelector("#play-label");
  const playReset = document.querySelector("#play-reset");
  const eventList = document.querySelector("#event-list");
  const progressTrack = document.querySelector("#progress-track");
  const progressFill = document.querySelector("#progress-fill");
  const progressCount = document.querySelector("#progress-count");
  const runState = document.querySelector("#run-state");
  let eventIndex = 0;
  let playbackTimer = null;

  function setPlaybackState(playing) {
    playToggle.setAttribute("aria-pressed", String(playing));
    playLabel.textContent = playing ? "Pause walkthrough" : "Play walkthrough";
    playToggle.querySelector(".play-icon").textContent = playing ? "Ⅱ" : "▶";
    runState.textContent = playing ? "PLAYING · SIMULATED EVENTS" : "PAUSED · FIXED SAMPLE";
  }

  function appendEvent(event) {
    if (eventIndex === 0) eventList.replaceChildren();
    const item = document.createElement("li");
    item.className = "event-row";
    const time = document.createElement("time");
    time.textContent = event.time;
    const tag = document.createElement("span");
    tag.className = "event-tag";
    tag.textContent = event.tag;
    const text = document.createElement("p");
    text.textContent = event.text;
    item.append(time, tag, text);
    eventList.append(item);
    eventList.scrollTop = eventList.scrollHeight;
    const completedEvents = eventIndex + 1;
    progressCount.textContent = `${completedEvents} / ${sampleEvents.length} EVENTS`;
    progressTrack.setAttribute("aria-valuenow", String(completedEvents));
    progressFill.style.transform = `scaleX(${completedEvents / sampleEvents.length})`;
    selectStage(event.stage);
  }

  function stopPlayback(completed = false) {
    if (playbackTimer) window.clearInterval(playbackTimer);
    playbackTimer = null;
    setPlaybackState(false);
    if (completed) {
      playLabel.textContent = "Replay walkthrough";
      runState.textContent = "COMPLETE · SIMULATED SAMPLE";
    }
  }

  function advancePlayback() {
    if (eventIndex >= sampleEvents.length) {
      stopPlayback(true);
      return;
    }
    appendEvent(sampleEvents[eventIndex]);
    eventIndex += 1;
    if (eventIndex >= sampleEvents.length) stopPlayback(true);
  }

  playToggle.addEventListener("click", () => {
    if (playbackTimer) {
      stopPlayback();
      return;
    }
    if (eventIndex >= sampleEvents.length) {
      eventIndex = 0;
      eventList.replaceChildren();
    }
    setPlaybackState(true);
    advancePlayback();
    if (eventIndex < sampleEvents.length) playbackTimer = window.setInterval(advancePlayback, 1450);
  });

  playReset.addEventListener("click", () => {
    if (playbackTimer) window.clearInterval(playbackTimer);
    playbackTimer = null;
    eventIndex = 0;
    eventList.replaceChildren();
    const empty = document.createElement("li");
    empty.className = "console-empty";
    empty.textContent = "Playback is paused. Start the fixed sample to see the handoffs unfold.";
    eventList.append(empty);
    progressCount.textContent = `0 / ${sampleEvents.length} EVENTS`;
    progressTrack.setAttribute("aria-valuenow", "0");
    progressFill.style.transform = "scaleX(0)";
    setPlaybackState(false);
    runState.textContent = "READY · FIXED SAMPLE";
    selectStage("plan");
  });
})();
