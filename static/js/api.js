/** PulseDesk shared API + auth helpers (Phase 6). */
(function (global) {
  const API = "/api/v1";
  const TOKEN_KEY = "agentcare_token";
  const USER_KEY = "agentcare_user";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  function setSession(token, user) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function roleHome(role) {
    if (role === "STAFF" || role === "ADMIN") return "/staff";
    return "/patient";
  }

  function requireAuth(roles) {
    const token = getToken();
    if (!token) {
      window.location.replace("/");
      return null;
    }
    const user = getUser();
    if (roles && user && roles.indexOf(user.role) === -1) {
      window.location.replace(roleHome(user.role));
      return null;
    }
    return { token: token, user: user };
  }

  function api(path, options) {
    options = options || {};
    const headers = Object.assign({}, options.headers || {});
    const token = getToken();
    if (token && !headers.Authorization) {
      headers.Authorization = "Bearer " + token;
    }
    const opts = Object.assign({}, options);
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    opts.headers = headers;
    return fetch(API + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (e) {
          data = { detail: text };
        }
        if (!res.ok) {
          var detail = data && data.detail;
          var msg =
            typeof detail === "string"
              ? detail
              : Array.isArray(detail)
                ? detail.map(function (d) { return d.msg || JSON.stringify(d); }).join("; ")
                : res.statusText;
          var err = new Error(msg || "HTTP " + res.status);
          err.status = res.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  function refreshMe() {
    return api("/auth/me").then(function (me) {
      setSession(getToken(), me);
      return me;
    });
  }

  function logout() {
    clearSession();
    window.location.replace("/");
  }

  function statusBadge(status) {
    var s = String(status || "").toUpperCase();
    var cls = "badge";
    if (s.indexOf("COMPLETE") >= 0 || s === "APPROVED" || s === "AVAILABLE" || s === "BOOKED") cls += " ok";
    else if (s.indexOf("WAIT") >= 0 || s.indexOf("PENDING") >= 0 || s.indexOf("RUNNING") >= 0) cls += " warn";
    else if (s.indexOf("BLOCK") >= 0 || s.indexOf("FAIL") >= 0 || s.indexOf("REJECT") >= 0 || s === "CANCELLED") cls += " danger";
    return '<span class="' + cls + '">' + escapeHtml(status || "—") + "</span>";
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fillNav(active) {
    var el = document.getElementById("topbar");
    if (!el) return;
    var user = getUser() || { name: "User", role: "" };
    var links = [];
    if (user.role === "PATIENT") {
      links.push('<a href="/patient"' + (active === "patient" ? ' class="nav-active"' : "") + ">Dashboard</a>");
    }
    if (user.role === "STAFF" || user.role === "ADMIN") {
      links.push('<a href="/staff"' + (active === "staff" ? ' class="nav-active"' : "") + ">Staff</a>");
    }
    if (user.role === "ADMIN") {
      links.push('<a href="/staff/admin"' + (active === "admin" ? ' class="nav-active"' : "") + ">Admin</a>");
    }
    links.push('<span class="muted">' + escapeHtml(user.name) + " · " + escapeHtml(user.role) + "</span>");
    links.push('<a href="#" id="logout-link">Log out</a>');
    el.innerHTML =
      '<a class="brand" href="' + roleHome(user.role) + '">PulseDesk</a>' +
      '<div class="nav-links">' + links.join("") + "</div>";
    var logoutLink = document.getElementById("logout-link");
    if (logoutLink) {
      logoutLink.addEventListener("click", function (e) {
        e.preventDefault();
        logout();
      });
    }
  }

  /** Ensure session user is loaded; redirect if unauthorized. Never throws. */
  function bootPage(roles, activeNav) {
    var session = requireAuth(roles);
    if (!session) return Promise.resolve(null);
    var p = session.user ? Promise.resolve(session.user) : refreshMe().catch(function () {
      clearSession();
      window.location.replace("/");
      return null;
    });
    return p.then(function (user) {
      if (!user) return null;
      if (roles && roles.indexOf(user.role) === -1) {
        window.location.replace(roleHome(user.role));
        return null;
      }
      fillNav(activeNav);
      return { token: session.token, user: user };
    });
  }

  global.PulseDesk = {
    API: API,
    getToken: getToken,
    getUser: getUser,
    setSession: setSession,
    clearSession: clearSession,
    requireAuth: requireAuth,
    roleHome: roleHome,
    api: api,
    refreshMe: refreshMe,
    logout: logout,
    statusBadge: statusBadge,
    escapeHtml: escapeHtml,
    fillNav: fillNav,
    bootPage: bootPage,
  };
})(window);
