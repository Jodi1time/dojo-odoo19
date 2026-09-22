/* Dojang Kiosk AI Companion — additive shell for 1Club/Dreams workflows. */
(() => {
  const token = window.KIOSK_TOKEN;
  if (!token) return;

  const post = async (url, params = {}) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({jsonrpc: "2.0", method: "call", params: {token, ...params}}),
    });
    const data = await response.json();
    return data.result || {};
  };

  const icon = (name) => '<span class="material-symbols-outlined">' + name + '</span>';

  function focusMemberSearch() {
    const input = [...document.querySelectorAll("input")].find(el =>
      /search|name|member/i.test((el.placeholder || "") + " " + (el.getAttribute("aria-label") || ""))
    );
    if (input) { input.focus(); input.scrollIntoView({behavior: "smooth", block: "center"}); }
  }

  function showClasses(ctx, body) {
    const sessions = ctx.sessions || [];
    body.innerHTML = '<button class="kc-back">← Back</button><h2>Today</h2>' +
      (sessions.length ? sessions.map(s =>
        '<div class="kc-session"><div><strong>' + (s.template_name || s.name || "Class") +
        '</strong><small>' + (s.program_name || "") + '</small></div><span>' +
        new Date((s.start || "").replace(" ", "T") + "Z").toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}) +
        '</span></div>').join("") : '<p class="kc-muted">No open sessions today.</p>');
    body.querySelector(".kc-back").onclick = () => renderActions(ctx, body);
  }

  function renderActions(ctx, body) {
    const actions = ctx.next_actions || [];
    body.innerHTML =
      '<div class="kc-eyebrow">DOJANG AI COMPANION</div>' +
      '<h2>What do you need?</h2>' +
      '<p class="kc-muted">Fast actions powered by live Odoo data.</p>' +
      '<div class="kc-actions">' +
      actions.map(a => '<button class="kc-action" data-action="' + a.id + '" ' +
        (a.enabled ? '' : 'disabled') + '>' + icon(a.icon) + '<span>' + a.label +
        '</span>' + (!a.enabled ? '<small>Coming soon</small>' : '') + '</button>').join("") +
      '</div>';

    body.querySelectorAll(".kc-action:not([disabled])").forEach(btn => {
      btn.onclick = () => {
        const action = btn.dataset.action;
        if (action === "classes") return showClasses(ctx, body);
        if (action === "check_in" || action === "find_member") {
          closePanel(); focusMemberSearch(); return;
        }
        if (action === "help") {
          body.innerHTML = '<button class="kc-back">← Back</button><div class="kc-ai">' +
            icon("auto_awesome") + '<h2>How can I help?</h2>' +
            '<p>For now I can get you to check-in, member search, and today’s classes. ' +
            'More actions unlock only when the matching Odoo capability is installed.</p></div>';
          body.querySelector(".kc-back").onclick = () => renderActions(ctx, body);
          return;
        }
        body.innerHTML = '<button class="kc-back">← Back</button><h2>' +
          btn.querySelector("span:not(.material-symbols-outlined)").textContent +
          '</h2><p class="kc-muted">This capability is available in Odoo. The dedicated kiosk flow is being connected next.</p>';
        body.querySelector(".kc-back").onclick = () => renderActions(ctx, body);
      };
    });
  }

  let overlay;
  function closePanel() { if (overlay) overlay.classList.remove("kc-open"); }

  async function openPanel() {
    overlay.classList.add("kc-open");
    const body = overlay.querySelector(".kc-body");
    body.innerHTML = '<div class="kc-loading">' + icon("progress_activity") + '<p>Loading live dojo data…</p></div>';
    try {
      const ctx = await post("/kiosk/companion/context");
      if (ctx.error) throw new Error(ctx.error);
      renderActions(ctx, body);
    } catch (e) {
      body.innerHTML = '<h2>Companion unavailable</h2><p class="kc-muted">Check the kiosk connection and try again.</p>';
    }
  }

  function mount() {
    if (document.querySelector(".kc-launcher")) return;
    const launcher = document.createElement("button");
    launcher.className = "kc-launcher";
    launcher.setAttribute("aria-label", "Open Dojang AI Companion");
    launcher.innerHTML = icon("auto_awesome") + '<span>Ask Dojang</span>';
    launcher.onclick = openPanel;

    overlay = document.createElement("div");
    overlay.className = "kc-overlay";
    overlay.innerHTML = '<div class="kc-scrim"></div><aside class="kc-panel" role="dialog" aria-label="Dojang AI Companion">' +
      '<header><div class="kc-brand">' + icon("auto_awesome") + '<span>Dojang</span></div>' +
      '<button class="kc-close" aria-label="Close">×</button></header><div class="kc-body"></div></aside>';
    overlay.querySelector(".kc-scrim").onclick = closePanel;
    overlay.querySelector(".kc-close").onclick = closePanel;
    document.body.append(launcher, overlay);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
