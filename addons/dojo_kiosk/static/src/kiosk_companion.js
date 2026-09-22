/* Dojang Kiosk AI Companion — additive 1Club/Dreams workflow layer.
 * Odoo remains authoritative. This file intentionally reuses the kiosk's
 * existing check-in/search APIs instead of creating a second client data model.
 */
(() => {
  const token = window.KIOSK_TOKEN;
  if (!token) return;

  const state = {
    overlay: null,
    body: null,
    context: null,
    member: null,
    idleTimer: null,
    searchAbort: null,
    online: navigator.onLine,
  };

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const post = async (url, params = {}, options = {}) => {
    if (!navigator.onLine) throw new Error("offline");
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({jsonrpc: "2.0", method: "call", params: {token, ...params}}),
      signal: options.signal,
    });
    const data = await response.json();
    if (data.error) throw new Error(data.error.data?.message || data.error.message || "Request failed");
    return data.result || {};
  };

  const icon = (name) => '<span class="material-symbols-outlined" aria-hidden="true">' + esc(name) + '</span>';
  const fmtTime = (value) => {
    if (!value) return "";
    const parsed = new Date(String(value).replace(" ", "T") + "Z");
    return parsed.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
  };

  function resetIdle() {
    clearTimeout(state.idleTimer);
    if (!state.overlay?.classList.contains("kc-open")) return;
    state.idleTimer = setTimeout(() => closePanel(true), 75000);
  }

  function clearPrivateState() {
    state.member = null;
    state.context = null;
    if (state.searchAbort) state.searchAbort.abort();
    state.searchAbort = null;
    if (state.body) state.body.innerHTML = "";
  }

  function closePanel(clear = true) {
    state.overlay?.classList.remove("kc-open");
    clearTimeout(state.idleTimer);
    if (clear) clearPrivateState();
  }

  async function loadContext(memberId = null) {
    const ctx = await post("/kiosk/companion/context", memberId ? {member_id: memberId} : {});
    if (ctx.error || ctx.success === false) throw new Error(ctx.error || "Companion unavailable");
    state.context = ctx;
    if (ctx.member) state.member = ctx.member;
    return ctx;
  }

  function memberHeader() {
    if (!state.member) {
      return '<button class="kc-member-select" data-nav="find">' +
        icon("person_search") + '<span><strong>Select a member</strong><small>Required for personal actions</small></span></button>';
    }
    return '<div class="kc-member-chip">' +
      '<img src="' + esc(state.member.image_url || "") + '" alt=""/>' +
      '<div><small>ACTIVE MEMBER</small><strong>' + esc(state.member.name) + '</strong>' +
      '<span>' + esc(state.member.rank || state.member.membership_state || "") + '</span></div>' +
      '<button data-nav="find" aria-label="Change member">' + icon("swap_horiz") + '</button></div>';
  }

  function wireNav() {
    state.body.querySelectorAll("[data-nav]").forEach((el) => {
      el.onclick = () => {
        resetIdle();
        const nav = el.dataset.nav;
        if (nav === "home") renderHome();
        if (nav === "find") renderMemberSearch();
        if (nav === "classes") renderClasses();
        if (nav === "membership") renderMembership();
        if (nav === "family") renderFamily();
        if (nav === "help") renderHelp();
        if (nav === "credential") renderCredentialEntry();
      };
    });
  }

  function renderHome() {
    const ctx = state.context || {};
    const actions = ctx.next_actions || [];
    state.body.innerHTML =
      memberHeader() +
      '<div class="kc-eyebrow">DOJANG AI COMPANION</div>' +
      '<h2>What do you need?</h2>' +
      '<p class="kc-muted">Quick actions use live Odoo data.</p>' +
      '<div class="kc-actions">' +
      actions.map((a) => {
        const needsMember = ["check_in", "membership", "family", "events"].includes(a.id);
        const disabled = !a.enabled;
        const action = a.id === "check_in" ? "classes" : a.id;
        return '<button class="kc-action" data-action="' + esc(action) + '" ' + (disabled ? "disabled" : "") + '>' +
          icon(a.icon) + '<span>' + esc(a.label) + '</span>' +
          (disabled ? '<small>Not enabled in Odoo</small>' : needsMember && !state.member ? '<small>Select member first</small>' : '') +
          '</button>';
      }).join("") +
      '</div>';

    state.body.querySelectorAll("[data-nav]").forEach((el) => {
      el.onclick = () => el.dataset.nav === "find" && renderMemberSearch();
    });
    state.body.querySelectorAll(".kc-action:not([disabled])").forEach((btn) => {
      btn.onclick = () => {
        resetIdle();
        const action = btn.dataset.action;
        if (action === "find_member") return renderMemberSearch();
        if (action === "classes") return renderClasses();
        if (action === "membership") return state.member ? renderMembership() : renderMemberSearch();
        if (action === "family") return state.member ? renderFamily() : renderMemberSearch();
        if (action === "help") return renderHelp();
        if (action === "events" || action === "map") return renderCapabilityNotice(action);
      };
    });
  }

  function renderMemberSearch() {
    state.body.innerHTML =
      '<button class="kc-back" data-nav="home">← Home</button>' +
      '<div class="kc-eyebrow">IDENTIFY</div><h2>Who’s checking in?</h2>' +
      '<div class="kc-search-wrap">' + icon("search") +
      '<input class="kc-search" autocomplete="off" inputmode="search" placeholder="Name or member number" aria-label="Search members"/>' +
      '</div>' +
      '<div class="kc-code-row"><input class="kc-code-input" autocomplete="off" placeholder="Scan / enter member or QR code" aria-label="Member or QR code"/>' +
      '<button class="kc-secondary kc-code-go">' + icon("qr_code_scanner") + '</button></div>' +
      '<div class="kc-results"><p class="kc-muted">Start typing a member name, or scan a code.</p></div>';
    wireNav();
    const input = state.body.querySelector(".kc-search");
    const results = state.body.querySelector(".kc-results");
    const codeInput = state.body.querySelector(".kc-code-input");
    const codeGo = state.body.querySelector(".kc-code-go");

    const resolveCode = async () => {
      const credential = codeInput.value.trim();
      if (!credential) return;
      resetIdle();
      results.innerHTML = '<div class="kc-inline-loading">' + icon("progress_activity") + ' Checking code…</div>';
      try {
        const data = await post("/kiosk/companion/credential", {credential, kind: "qr"});
        if (!data.success || !data.found || !data.member?.member_id) {
          results.innerHTML = '<p class="kc-error">No member matched that code.</p>';
          return;
        }
        await loadContext(Number(data.member.member_id));
        renderHome();
      } catch (e) {
        results.innerHTML = '<p class="kc-error">' +
          (e.message === "offline" ? "Kiosk is offline. Reconnect before identifying a member." : "Could not read that code.") +
          '</p>';
      }
    };
    codeGo.onclick = resolveCode;
    codeInput.onkeydown = (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); resolveCode(); }
    };

    let timer;
    input.oninput = () => {
      resetIdle();
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) {
        results.innerHTML = '<p class="kc-muted">Enter at least 2 characters.</p>';
        return;
      }
      timer = setTimeout(async () => {
        if (state.searchAbort) state.searchAbort.abort();
        state.searchAbort = new AbortController();
        results.innerHTML = '<div class="kc-inline-loading">' + icon("progress_activity") + ' Searching…</div>';
        try {
          const rows = await post("/kiosk/search", {query: q}, {signal: state.searchAbort.signal});
          if (!rows.length) {
            results.innerHTML = '<p class="kc-muted">No matching members found.</p>';
            return;
          }
          results.innerHTML = rows.slice(0, 12).map((row) => {
            if (!row.member_id) {
              return '<div class="kc-result kc-result--trial"><div><strong>' + esc(row.name) +
                '</strong><small>Trial arrival</small></div><span>Use main check-in</span></div>';
            }
            return '<button class="kc-result" data-member-id="' + row.member_id + '">' +
              '<div><strong>' + esc(row.name) + '</strong><small>' +
              esc(row.belt_rank || "Member") + '</small></div>' + icon("chevron_right") + '</button>';
          }).join("");
          results.querySelectorAll("[data-member-id]").forEach((btn) => {
            btn.onclick = async () => {
              results.innerHTML = '<div class="kc-inline-loading">' + icon("progress_activity") + ' Loading member…</div>';
              try {
                await loadContext(Number(btn.dataset.memberId));
                renderHome();
              } catch {
                results.innerHTML = '<p class="kc-error">Could not load this member.</p>';
              }
            };
          });
        } catch (e) {
          if (e.name !== "AbortError") results.innerHTML = '<p class="kc-error">Search failed. Check the connection.</p>';
        }
      }, 220);
    };
    setTimeout(() => input.focus(), 50);
  }

  function renderCredentialEntry() {
    state.body.innerHTML =
      '<button class="kc-back" data-nav="find">← Member search</button>' +
      '<div class="kc-eyebrow">IDENTIFY</div><h2>Member code</h2>' +
      '<p class="kc-muted">Scan a barcode/QR code or enter the member number. NFC and wallet credentials stay disabled until an Odoo credential provider is configured.</p>' +
      '<div class="kc-search-wrap">' + icon("qr_code_scanner") +
      '<input class="kc-search kc-credential" autocomplete="off" placeholder="DJ-00001" aria-label="Member code"/></div>' +
      '<button class="kc-primary kc-wide" data-identify="1">Continue</button>' +
      '<div class="kc-credential-result"></div>';
    wireNav();
    const input = state.body.querySelector(".kc-credential");
    const resultEl = state.body.querySelector(".kc-credential-result");
    const submit = async () => {
      resetIdle();
      if (!navigator.onLine) {
        resultEl.innerHTML = '<p class="kc-error">This action needs a connection to Odoo.</p>';
        return;
      }
      const value = input.value.trim();
      if (!value) return;
      resultEl.innerHTML = '<div class="kc-inline-loading">' + icon("progress_activity") + ' Checking…</div>';
      try {
        const data = await post("/kiosk/companion/identify", {credential_type: "qr", value});
        if (!data.success || !data.member) throw new Error(data.error || "Member not found");
        await loadContext(data.member.id);
        renderHome();
      } catch (e) {
        resultEl.innerHTML = '<p class="kc-error">' +
          (e.message === "member_not_found" ? "No member matched that code." : "Could not verify that credential.") +
          '</p>';
      }
    };
    state.body.querySelector("[data-identify]").onclick = submit;
    input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    setTimeout(() => input.focus(), 50);
  }

  async function renderClasses() {
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><h2>Classes</h2>' +
      '<div class="kc-inline-loading">' + icon("progress_activity") + ' Loading…</div>';
    wireNav();
    try {
      if (!state.member) {
        const ctx = state.context || await loadContext();
        const sessions = ctx.sessions || [];
        state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button>' +
          '<div class="kc-eyebrow">TODAY</div><h2>Classes</h2>' +
          '<p class="kc-muted">Select a member to book or check in.</p>' +
          renderPublicSessions(sessions) +
          '<button class="kc-primary kc-wide" data-nav="find">' + icon("person_search") + ' Select member</button>';
        wireNav();
        return;
      }
      const data = await post("/kiosk/companion/sessions", {member_id: state.member.id});
      if (!data.success) throw new Error(data.error);
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button>' +
        memberHeader() + '<div class="kc-eyebrow">TODAY</div><h2>Classes</h2>' +
        '<div class="kc-session-list">' + (data.sessions || []).map(renderMemberSession).join("") + '</div>';
      wireNav();
      state.body.querySelectorAll("[data-session-action]").forEach((button) => {
        button.onclick = () => handleSessionAction(button);
      });
    } catch {
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><p class="kc-error">Could not load classes.</p>';
      wireNav();
    }
  }

  function renderPublicSessions(sessions) {
    if (!sessions.length) return '<p class="kc-empty">No open classes today.</p>';
    return '<div class="kc-session-list">' + sessions.map((s) =>
      '<article class="kc-session-card"><div><small>' + esc(s.program_name || "Class") + '</small>' +
      '<strong>' + esc(s.template_name || s.name) + '</strong>' +
      '<span>' + esc(s.instructor || "") + '</span></div><time>' + esc(fmtTime(s.start)) + '</time></article>'
    ).join("") + '</div>';
  }

  function renderMemberSession(s) {
    let status = "";
    let action = "";
    if (s.attendance_state === "present") status = '<span class="kc-badge kc-good">Checked in</span>';
    else if (s.enrollment_status === "waitlist") status = '<span class="kc-badge">Waitlisted</span>';
    else if (s.can_check_in) action = '<button class="kc-primary" data-session-action="checkin" data-session="' + s.id + '">Check in</button>';
    else if (s.can_book) action = '<button class="kc-primary" data-session-action="book" data-session="' + s.id + '">Book</button>';
    else if (s.can_waitlist) action = '<button class="kc-secondary" data-session-action="book" data-session="' + s.id + '">Join waitlist</button>';
    else if (!s.eligible) status = '<span class="kc-badge kc-warn">' + esc(s.eligibility_reason || "Not eligible") + '</span>';
    else if (s.enrollment_status === "registered") status = '<span class="kc-badge">Booked</span>';

    const cancel = ["registered", "waitlist"].includes(s.enrollment_status) && s.attendance_state !== "present"
      ? '<button class="kc-link" data-session-action="cancel" data-session="' + s.id + '">Cancel</button>' : "";

    return '<article class="kc-session-card"><div class="kc-session-main"><small>' + esc(s.program_name || "Class") +
      '</small><strong>' + esc(s.template_name || s.name) + '</strong><span>' + esc(fmtTime(s.start)) +
      (s.instructor ? ' · ' + esc(s.instructor) : "") + '</span></div><div class="kc-session-actions">' +
      status + action + cancel + '</div></article>';
  }

  async function handleSessionAction(button) {
    resetIdle();
    if (!navigator.onLine) {
      showToast("You’re offline. Reconnect before booking or checking in.", true);
      return;
    }
    if (!navigator.onLine) {
      showToast("Kiosk is offline. Reconnect before booking or checking in.", true);
      return;
    }
    const sessionId = Number(button.dataset.session);
    const action = button.dataset.sessionAction;
    button.disabled = true;
    const old = button.textContent;
    button.textContent = action === "checkin" ? "Checking in…" : action === "cancel" ? "Cancelling…" : "Saving…";
    try {
      let result;
      if (action === "checkin") {
        result = await post("/kiosk/checkin", {member_id: state.member.id, session_id: sessionId});
        if (!result.success) throw new Error(result.error || "Check-in failed");
        return renderReceipt(result);
      }
      if (action === "cancel") {
        result = await post("/kiosk/companion/cancel-booking", {member_id: state.member.id, session_id: sessionId});
      } else {
        result = await post("/kiosk/companion/book", {member_id: state.member.id, session_id: sessionId});
      }
      if (!result.success) throw new Error(result.error || "Action failed");
      await renderClasses();
    } catch (e) {
      button.disabled = false;
      button.textContent = old;
      showToast(e.message || "Could not complete that action.", true);
    }
  }

  function renderReceipt(result) {
    state.body.innerHTML =
      '<div class="kc-receipt">' + icon("check_circle") +
      '<div class="kc-eyebrow">CHECK-IN COMPLETE</div><h2>' + esc(state.member?.name || "You're in") + '</h2>' +
      '<p>Checked in to <strong>' + esc(result.session_name || "class") + '</strong>.</p>' +
      '<div class="kc-next"><strong>What next?</strong>' +
      '<button class="kc-secondary kc-wide" data-nav="classes">' + icon("calendar_month") + ' View classes</button>' +
      '<button class="kc-secondary kc-wide" data-nav="family">' + icon("family_restroom") + ' Family</button>' +
      '<button class="kc-primary kc-wide" data-finish="1">Done</button></div></div>';
    wireNav();
    state.body.querySelector("[data-finish]").onclick = () => closePanel(true);
  }

  async function renderMembership() {
    if (!state.member) return renderMemberSearch();
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><div class="kc-inline-loading">' +
      icon("progress_activity") + ' Loading membership…</div>';
    wireNav();
    try {
      const data = await post("/kiosk/companion/membership", {member_id: state.member.id});
      if (!data.success) throw new Error(data.error);
      const m = data.membership;
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button>' + memberHeader() +
        '<div class="kc-eyebrow">MEMBERSHIP</div><h2>' + esc(m.plan_name || "No active plan") + '</h2>' +
        '<div class="kc-detail-grid"><div><small>Member status</small><strong>' + esc(m.state || "—") +
        '</strong></div><div><small>Subscription</small><strong>' + esc(m.subscription_state || "—") +
        '</strong></div><div><small>Plan type</small><strong>' + esc(m.plan_type || "—") +
        '</strong></div><div><small>Billing cycle</small><strong>' + esc(m.billing_period || "—") +
        '</strong></div></div>' +
        ((m.issues || []).length ? '<div class="kc-alert"><strong>Needs attention</strong>' +
          m.issues.map(i => '<span>' + esc(i.label) + '</span>').join("") + '</div>' :
          '<div class="kc-ok">' + icon("verified") + '<span>Membership is clear for kiosk use.</span></div>');
      wireNav();
    } catch {
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><p class="kc-error">Could not load membership.</p>';
      wireNav();
    }
  }

  async function renderFamily() {
    if (!state.member) return renderMemberSearch();
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><div class="kc-inline-loading">' +
      icon("progress_activity") + ' Loading household…</div>';
    wireNav();
    try {
      const data = await post("/kiosk/companion/household", {member_id: state.member.id});
      if (!data.success) throw new Error(data.error);
      const householdName = data.household?.name || "Your account";
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button>' +
        '<div class="kc-eyebrow">FAMILY</div><h2>' + esc(householdName) + '</h2>' +
        '<p class="kc-muted">Choose a family member to continue.</p><div class="kc-family-list">' +
        (data.members || []).map(m => '<button class="kc-family-member" data-family-member="' + m.id + '">' +
          '<img src="' + esc(m.image_url || "") + '" alt=""/><span><strong>' + esc(m.name) + '</strong><small>' +
          esc(m.rank || m.membership_state || "Member") + '</small></span>' + icon("chevron_right") + '</button>').join("") +
        '</div>' +
        ((data.guardians || []).length ? '<div class="kc-guardian-note"><small>GUARDIAN</small><strong>' +
          esc(data.guardians.find(g => g.is_primary)?.name || data.guardians[0].name) + '</strong></div>' : '');
      wireNav();
      state.body.querySelectorAll("[data-family-member]").forEach(btn => {
        btn.onclick = async () => {
          await loadContext(Number(btn.dataset.familyMember));
          renderHome();
        };
      });
    } catch {
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><p class="kc-error">Could not load family information.</p>';
      wireNav();
    }
  }

  async function renderTesting() {
    if (!state.member) return renderMemberSearch();
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><div class="kc-inline-loading">' +
      icon("progress_activity") + ' Loading testing schedule…</div>';
    wireNav();
    try {
      const data = await post("/kiosk/companion/testing", {member_id: state.member.id});
      if (!data.success) throw new Error(data.error);
      const rows = data.tests || [];
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button>' + memberHeader() +
        '<div class="kc-eyebrow">TESTING & EVENTS</div><h2>Upcoming belt tests</h2>' +
        (data.test_invite_pending ? '<div class="kc-ok">' + icon("mark_email_unread") +
          '<span>You have a testing invitation pending.</span></div>' : '') +
        (rows.length ? '<div class="kc-test-list">' + rows.map(t =>
          '<article class="kc-test-card"><div><small>' + esc(t.program || "Testing") + '</small><strong>' +
          esc(t.name) + '</strong><span>' + esc(t.date) + (t.location ? ' · ' + esc(t.location) : '') +
          '</span></div>' + (t.registered ? '<span class="kc-badge kc-good">' +
          esc(t.target_rank ? 'Testing for ' + t.target_rank : 'Registered') + '</span>' :
          '<span class="kc-badge">Not registered</span>') + '</article>').join("") + '</div>' :
          '<p class="kc-empty">No upcoming belt tests.</p>');
      wireNav();
    } catch {
      state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><p class="kc-error">Could not load testing schedule.</p>';
      wireNav();
    }
  }

  function renderHelp() {
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><div class="kc-ai kc-ai--ask">' +
      icon("auto_awesome") + '<div class="kc-eyebrow">DOJANG AI COMPANION</div><h2>Ask Dojang</h2>' +
      '<p>I can answer kiosk-safe questions about classes, your selected member’s schedule/rank, and guide check-in without bypassing Odoo rules.</p>' +
      '<div class="kc-ask-box"><input class="kc-ask-input" autocomplete="off" placeholder="What classes are on today?" aria-label="Ask Dojang"/>' +
      '<button class="kc-primary" data-ask="1">' + icon("arrow_upward") + '</button></div>' +
      '<div class="kc-ai-answer"></div>' +
      '<div class="kc-help-actions"><button class="kc-secondary" data-nav="find">Find a member</button>' +
      '<button class="kc-secondary" data-nav="classes">Classes</button></div></div>';
    wireNav();
    const input = state.body.querySelector(".kc-ask-input");
    const answer = state.body.querySelector(".kc-ai-answer");
    const submit = async () => {
      resetIdle();
      const text = input.value.trim();
      if (!text) return;
      if (!navigator.onLine) {
        answer.innerHTML = '<p class="kc-error">Ask Dojang needs a connection to Odoo.</p>';
        return;
      }
      answer.innerHTML = '<div class="kc-inline-loading">' + icon("progress_activity") + ' Thinking…</div>';
      try {
        const data = await post("/kiosk/companion/ask", {
          text,
          member_id: state.member ? state.member.id : null,
        });
        if (!data.success) throw new Error(data.response || data.error || "Could not answer");
        answer.innerHTML = '<div class="kc-answer-card"><strong>Dojang</strong><p>' + esc(data.response || "") + '</p>' +
          (data.action && data.action !== "home" ?
            '<button class="kc-primary kc-wide" data-ai-route="' + esc(data.action) + '">Continue</button>' : '') +
          '</div>';
        const route = answer.querySelector("[data-ai-route]");
        if (route) route.onclick = () => {
          if (route.dataset.aiRoute === "find_member") renderMemberSearch();
          else if (route.dataset.aiRoute === "classes") renderClasses();
        };
      } catch (e) {
        answer.innerHTML = '<p class="kc-error">' + esc(e.message || "Could not answer right now.") + '</p>';
      }
    };
    state.body.querySelector("[data-ask]").onclick = submit;
    input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    setTimeout(() => input.focus(), 50);
  }

  function renderCapabilityNotice(kind) {
    const label = kind === "map" ? "Facility map" : "Testing & events";
    state.body.innerHTML = '<button class="kc-back" data-nav="home">← Home</button><div class="kc-ai">' +
      icon(kind === "map" ? "map" : "emoji_events") + '<h2>' + label + '</h2>' +
      '<p>This Odoo capability is detected, but its kiosk-specific action contract is not enabled on this branch yet.</p></div>';
    wireNav();
  }

  function showToast(message, error = false) {
    let toast = document.querySelector(".kc-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "kc-toast";
      document.body.appendChild(toast);
    }
    toast.classList.toggle("kc-toast--error", error);
    toast.textContent = message;
    toast.classList.add("kc-toast--show");
    setTimeout(() => toast.classList.remove("kc-toast--show"), 3500);
  }

  async function openPanel() {
    state.overlay.classList.add("kc-open");
    resetIdle();
    state.body.innerHTML = '<div class="kc-loading">' + icon("progress_activity") + '<p>Loading live dojo data…</p></div>';
    try {
      await loadContext();
      renderHome();
    } catch {
      state.body.innerHTML = '<h2>Companion unavailable</h2><p class="kc-muted">Check the kiosk connection and try again.</p>';
    }
  }

  function mount() {
    if (document.querySelector(".kc-launcher")) return;
    const launcher = document.createElement("button");
    launcher.className = "kc-launcher";
    launcher.setAttribute("aria-label", "Open Dojang AI Companion");
    launcher.innerHTML = icon("auto_awesome") + '<span>Ask Dojang</span>';
    launcher.onclick = openPanel;

    state.overlay = document.createElement("div");
    state.overlay.className = "kc-overlay";
    state.overlay.innerHTML = '<div class="kc-scrim"></div><aside class="kc-panel" role="dialog" aria-label="Dojang AI Companion">' +
      '<header><div class="kc-brand">' + icon("auto_awesome") + '<span>Dojang</span></div>' +
      '<div class="kc-header-tools"><span class="kc-network"></span><button class="kc-close" aria-label="Close">×</button></div></header><div class="kc-body"></div></aside>';
    state.body = state.overlay.querySelector(".kc-body");
    state.overlay.querySelector(".kc-scrim").onclick = () => closePanel(true);
    state.overlay.querySelector(".kc-close").onclick = () => closePanel(true);
    state.overlay.addEventListener("pointerdown", resetIdle);
    state.overlay.addEventListener("keydown", resetIdle);

    const updateNetwork = () => {
      state.online = navigator.onLine;
      const badge = state.overlay.querySelector(".kc-network");
      badge.textContent = state.online ? "Online" : "Offline";
      badge.classList.toggle("kc-network--offline", !state.online);
      if (!state.online && state.overlay.classList.contains("kc-open")) {
        showToast("Kiosk is offline. Read-only cached screens may be stale.", true);
      }
    };
    window.addEventListener("online", updateNetwork);
    window.addEventListener("offline", updateNetwork);
    updateNetwork();

    document.body.append(launcher, state.overlay);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
