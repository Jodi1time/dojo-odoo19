(function () {
  var boot = window.DOJANG_BOOT || {};
  var NAV = ['dashboard', 'students', 'checkin', 'email', 'automation', 'timesheets', 'reports'];
  var ROLE_BUTTONS = {
    openExportModal: ['admin', 'front_desk'],
    openAnalyticsModal: ['admin'],
    openAddModal: ['admin', 'front_desk'],
    openMassPromoteModal: ['admin'],
    openPromotionHistoryModal: ['admin'],
    openTestRosterModal: ['admin'],
    openSavedRostersModal: ['admin'],
    openInactiveReportModal: ['admin', 'front_desk', 'instructor'],
    saveStudent: ['admin', 'front_desk'],
    deleteStudent: ['admin']
  };
  var BELT_SELECTS = { 'f-belt': false, 'promo-from-belt': false, 'promo-to-belt': false, 'belt-filter': true, 'roster-belt-filter': true };
  var CLASS_SELECTS = { 'f-class': false, 'class-filter': true, 'promo-class-filter': true, 'roster-class-filter': true, 'inactive-class-filter': true };
  var me = null;

  function getJson(path) {
    return fetch(path, { credentials: 'include', headers: { 'Content-Type': 'application/json' } })
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; });
  }

  function allowed(page) {
    return !me || me.pages.indexOf(page) >= 0;
  }

  function byOnclick(name) {
    return Array.prototype.slice.call(document.querySelectorAll('[onclick^="' + name + '("]'));
  }

  function setVisible(el, visible) {
    if (el) el.style.display = visible ? '' : 'none';
  }

  function fillSelect(id, values, keepFirst) {
    var select = document.getElementById(id);
    if (!select || !values.length) return;
    var first = keepFirst && select.options.length ? select.options[0].cloneNode(true) : null;
    select.innerHTML = '';
    if (first) select.appendChild(first);
    values.forEach(function (value) {
      var option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function fillChips(classes) {
    var holder = document.getElementById('recipient-chips');
    if (!holder || !classes.length) return;
    var expiring = holder.querySelector('[data-val="Expiring"]');
    Array.prototype.slice.call(holder.querySelectorAll('.chip')).forEach(function (chip) {
      var value = chip.getAttribute('data-val');
      if (value !== 'all' && value !== 'Expiring') chip.remove();
    });
    classes.forEach(function (name) {
      var chip = document.createElement('div');
      chip.className = 'chip';
      chip.setAttribute('data-val', name);
      chip.textContent = name;
      chip.onclick = function () { window.toggleChip(chip); };
      holder.insertBefore(chip, expiring);
    });
  }

  async function applyCatalog() {
    var catalog = await getJson('/api/admin/catalog');
    if (!catalog) return;
    Object.keys(BELT_SELECTS).forEach(function (id) { fillSelect(id, catalog.belts || [], BELT_SELECTS[id]); });
    Object.keys(CLASS_SELECTS).forEach(function (id) { fillSelect(id, catalog.classes || [], CLASS_SELECTS[id]); });
    fillChips(catalog.classes || []);
  }

  function applyRole() {
    var items = document.querySelectorAll('.nav-item');
    NAV.forEach(function (page, index) { setVisible(items[index], allowed(page)); });
    var nav = items.length ? items[0].parentNode : null;
    if (nav) {
      var label = null;
      var anyVisible = false;
      Array.prototype.slice.call(nav.children).forEach(function (child) {
        if (child.classList.contains('nav-section-label') || child.querySelector && child.querySelector('.nav-section-label')) {
          if (label) setVisible(label, anyVisible);
          label = child;
          anyVisible = false;
        } else if (child.classList.contains('nav-item') && child.style.display !== 'none') {
          anyVisible = true;
        }
      });
      if (label) setVisible(label, anyVisible);
    }
    Object.keys(ROLE_BUTTONS).forEach(function (name) {
      byOnclick(name).forEach(function (button) { setVisible(button, ROLE_BUTTONS[name].indexOf(me.role) >= 0); });
    });
    if (!boot.kiosk) {
      byOnclick('staffLogout').forEach(function (button) {
        if (/kiosk/i.test(button.textContent)) setVisible(button, false);
      });
    }
    var title = document.getElementById('page-title');
    var badge = document.getElementById('dojang-role-badge');
    if (title && !badge) {
      badge = document.createElement('div');
      badge.id = 'dojang-role-badge';
      badge.title = '';
      var style = document.createElement('style');
      style.textContent = '#dojang-role-badge{margin-left:12px;padding:3px 9px;border:1px solid var(--border);border-radius:999px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--gold);white-space:nowrap}@media (max-width:1279px){#dojang-role-badge{display:none}}';
      document.head.appendChild(style);
      title.parentNode.insertBefore(badge, title.nextSibling);
    }
    if (badge) {
      badge.textContent = me.role_label;
      badge.title = me.user.name;
    }
  }

  function showLoginOnly() {
    try { staffSessionActive = false; } catch (e) { }
    document.getElementById('kiosk-overlay').style.display = 'none';
    document.getElementById('app-root').classList.add('hidden-app');
    document.getElementById('login-screen').style.display = 'flex';
  }

  if (!boot.kiosk) {
    window.loadDisplaySessions = async function () { };
    window.showDisplay = showLoginOnly;
    window.cancelStaffLogin = function () {
      document.getElementById('login-password').value = '';
      document.getElementById('login-error').textContent = '';
    };
    byOnclick('cancelStaffLogin').forEach(function (button) { setVisible(button, false); });
  }

  window.todaysScheduledClasses = function () {
    var schedule = CLASS_SCHEDULE[new Date().getDay()] || [];
    if (!adminSessions.length) return schedule;
    return adminSessions.map(function (session) {
      var name = session.template_name || '';
      var planned = schedule.filter(function (row) { return row.name === name; })[0];
      return { name: name, time: formatSchoolTime(session.start), sessionId: session.id,
               note: planned ? planned.note : (session.instructor || '') };
    });
  };

  var originalLoadAutomation = window.loadAutomation;
  window.loadAutomation = async function () {
    if (allowed('automation')) return originalLoadAutomation.apply(this, arguments);
  };
  var originalLoadEmailHistory = window.loadEmailHistory;
  window.loadEmailHistory = async function () {
    if (allowed('email')) return originalLoadEmailHistory.apply(this, arguments);
    emailHistory = [];
  };
  var originalOpenEditModal = window.openEditModal;
  window.openEditModal = function () {
    var result = originalOpenEditModal.apply(this, arguments);
    if (me) {
      setVisible(document.getElementById('modal-btn-delete'), me.role === 'admin');
      byOnclick('saveStudent').forEach(function (button) { setVisible(button, me.role !== 'instructor'); });
    }
    return result;
  };
  var originalShowApp = window.showAppAfterLogin;
  window.showAppAfterLogin = async function () {
    me = await getJson('/api/auth/me');
    if (me && me.role === 'parent') {
      window.location.href = me.home;
      return;
    }
    if (me) {
      await applyCatalog();
      applyRole();
    }
    await originalShowApp.apply(this, arguments);
    if (me && !allowed('dashboard') && me.pages.length) window.navigate(me.pages[0]);
  };
})();
