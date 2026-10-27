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
})();
