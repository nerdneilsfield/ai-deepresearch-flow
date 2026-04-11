/* Index page functionality */
(function() {
  'use strict';

  var page = 1;
  var loading = false;
  var done = false;
  var semanticMode = false;
  var semanticToken = "";
  var TOKEN_DB_NAME = "deepresearch_flow";
  var TOKEN_STORE_NAME = "settings";
  var TOKEN_KEY = "search_access_token";

  function openSettingsDb() {
    return new Promise(function(resolve, reject) {
      if (!window.indexedDB) {
        reject(new Error("IndexedDB not available"));
        return;
      }
      var request = window.indexedDB.open(TOKEN_DB_NAME, 1);
      request.onupgradeneeded = function(event) {
        var db = event.target.result;
        if (!db.objectStoreNames.contains(TOKEN_STORE_NAME)) {
          db.createObjectStore(TOKEN_STORE_NAME);
        }
      };
      request.onsuccess = function() { resolve(request.result); };
      request.onerror = function() { reject(request.error || new Error("Failed to open IndexedDB")); };
    });
  }

  function readToken() {
    return openSettingsDb().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(TOKEN_STORE_NAME, "readonly");
        var store = tx.objectStore(TOKEN_STORE_NAME);
        var request = store.get(TOKEN_KEY);
        request.onsuccess = function() {
          var value = request.result;
          if (value && typeof value === "object" && typeof value.token === "string") {
            resolve(value.token);
            return;
          }
          if (typeof value === "string") {
            resolve(value);
            return;
          }
          resolve("");
        };
        request.onerror = function() { reject(request.error || new Error("Failed to read token")); };
      }).finally(function() { db.close(); });
    }).catch(function() {
      return "";
    });
  }

  function writeToken(token) {
    return openSettingsDb().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(TOKEN_STORE_NAME, "readwrite");
        var store = tx.objectStore(TOKEN_STORE_NAME);
        var request = store.put({ token: token, saved_at: new Date().toISOString() }, TOKEN_KEY);
        request.onsuccess = function() { resolve(); };
        request.onerror = function() { reject(request.error || new Error("Failed to write token")); };
      }).finally(function() { db.close(); });
    });
  }

  function clearToken() {
    semanticToken = "";
    return openSettingsDb().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(TOKEN_STORE_NAME, "readwrite");
        var store = tx.objectStore(TOKEN_STORE_NAME);
        var request = store.delete(TOKEN_KEY);
        request.onsuccess = function() { resolve(); };
        request.onerror = function() { reject(request.error || new Error("Failed to clear token")); };
      }).finally(function() { db.close(); });
    }).catch(function() {
      return undefined;
    });
  }

  function currentParams(nextPage) {
    var params = new URLSearchParams();
    params.set("page", String(nextPage));
    params.set("page_size", "30");
    var q = document.getElementById("query").value.trim();
    if (q) params.set("q", q);
    var fq = document.getElementById("filterQuery").value.trim();
    if (fq) params.set("fq", fq);
    var sortBy = document.getElementById("sortBy").value;
    if (sortBy) params.set("sort_by", sortBy);
    var sortDir = document.getElementById("sortDir").value;
    if (sortDir) params.set("sort_dir", sortDir);
    function addMulti(id, key) {
      var el = document.getElementById(id);
      var values = Array.from(el.selectedOptions).map(function(opt) { return opt.value; }).filter(Boolean);
      for (var i = 0; i < values.length; i++) {
        params.append(key, values[i]);
      }
    }
    addMulti("filterPdf", "pdf");
    addMulti("filterSource", "source");
    addMulti("filterTranslated", "translated");
    addMulti("filterSummary", "summary");
    addMulti("filterTemplate", "template");
    return params;
  }

  function currentSemanticParams() {
    var params = new URLSearchParams();
    params.set("top_n", "30");
    var q = document.getElementById("query").value.trim();
    if (q) params.set("q", q);
    var advYear = document.getElementById("advYear");
    if (advYear && advYear.value.trim()) params.set("year", advYear.value.trim());
    var advVenue = document.getElementById("advVenue");
    if (advVenue && advVenue.value.trim()) params.set("venue", advVenue.value.trim());
    return params;
  }

  function setTokenError(message) {
    var tokenError = document.getElementById("token-error");
    if (!tokenError) return;
    if (!message) {
      tokenError.style.display = "none";
      tokenError.textContent = "";
      return;
    }
    tokenError.textContent = message;
    tokenError.style.display = "block";
  }

  function updateSemanticUi() {
    var badge = document.getElementById("semantic-badge");
    var icon = document.getElementById("semantic-icon");
    var toggle = document.getElementById("semantic-toggle");
    if (badge) badge.style.display = semanticMode ? "inline-flex" : "none";
    if (icon) icon.textContent = semanticMode ? "🔓" : "🔒";
    if (toggle) toggle.title = semanticMode ? "Semantic search unlocked" : "Unlock semantic search";
  }

  function deactivateSemanticMode() {
    semanticMode = false;
    semanticToken = "";
    updateSemanticUi();
  }

  function activateSemanticMode(token) {
    semanticMode = true;
    semanticToken = token;
    updateSemanticUi();
  }

  function probeSemanticToken(token) {
    return fetch("/api/papers/semantic?probe=1", {
      headers: {
        "Authorization": "Bearer " + token
      }
    }).then(function(response) {
      if (!response.ok) {
        throw new Error(response.status === 403 ? "Invalid token" : "Semantic search unavailable");
      }
      return response.json();
    });
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function normalizeText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function cleanVenue(text) {
    return normalizeText(text).replace(/\{\{|\}\}/g, "");
  }

  function viewSuffixForItem(item) {
    var viewSelect = document.getElementById("openView");
    var view = viewSelect ? viewSelect.value : "summary";
    var isPdfOnly = item.is_pdf_only;
    var pdfFallback = item.has_pdf ? "pdfjs" : "pdf";
    if (isPdfOnly && (view === "summary" || view === "source" || view === "translated")) {
      view = pdfFallback;
    }
    if (!view || view === "summary") return "";
    var params = new URLSearchParams();
    params.set("view", view);
    if (view === "split") {
      if (isPdfOnly) {
        params.set("left", pdfFallback);
        params.set("right", pdfFallback);
      } else {
        params.set("left", "summary");
        if (item.has_pdf) {
          params.set("right", "pdfjs");
        } else if (item.has_source) {
          params.set("right", "source");
        } else {
          params.set("right", "summary");
        }
      }
    }
    return "?" + params.toString();
  }

  function renderItem(item, ordinal) {
    var tags = (item.tags || []).map(function(t) { return '<span class="pill">' + escapeHtml(t) + '</span>'; }).join("");
    var templateTags = (item.template_tags || []).map(function(t) { return '<span class="pill template">tmpl:' + escapeHtml(t) + '</span>'; }).join("");
    var authors = (item.authors || []).slice(0, 6).map(function(a) { return escapeHtml(a); }).join(", ");
    var venue = cleanVenue(item.venue || "");
    var dateLabel = escapeHtml(item.year || "") + "-" + escapeHtml(item.month || "");
    var meta = venue ? (dateLabel + " · <strong>" + escapeHtml(venue) + "</strong>") : dateLabel;
    var excerpt = "";
    var fullSummary = normalizeText(item.summary_full || "");
    var shortSummary = normalizeText(item.summary_excerpt || fullSummary);
    if (shortSummary) {
      if (fullSummary && fullSummary !== shortSummary) {
        excerpt = '<div class="summary-snippet" data-summary="1">' +
          '<button class="summary-toggle" type="button" aria-expanded="false" title="Expand summary">▾</button>' +
          '<div class="summary-text summary-short">' + escapeHtml(shortSummary) + '</div>' +
          '<div class="summary-text summary-full">' + escapeHtml(fullSummary) + '</div>' +
          '</div>';
      } else {
        excerpt = '<div class="summary-snippet"><div class="summary-text summary-short">' + escapeHtml(shortSummary) + '</div></div>';
      }
    }
    var viewSuffix = viewSuffixForItem(item);
    var badges = [];
    if (item.has_source) badges.push('<span class="pill">source</span>');
    if (item.has_translation) badges.push('<span class="pill">translated</span>');
    if (item.has_pdf) badges.push('<span class="pill">pdf</span>');
    if (item.is_pdf_only) badges.push('<span class="pill pdf-only">pdf-only</span>');
    var indexBadge = typeof ordinal === "number" ? '<span class="card-index">#' + ordinal + '</span>' : "";
    return '<div class="card paper-card">' +
      indexBadge +
      '<div><a href="/paper/' + encodeURIComponent(item.source_hash) + viewSuffix + '">' + escapeHtml(item.title || "") + '</a></div>' +
      '<div class="muted">' + authors + '</div>' +
      '<div class="muted">' + meta + '</div>' +
      excerpt +
      '<div style="margin-top:6px">' + badges.join("") + " " + templateTags + " " + tags + '</div>' +
      '</div>';
  }

  function renderStatsRow(targetId, label, counts) {
    var row = document.getElementById(targetId);
    if (!row || !counts) return;
    var pills = [];
    pills.push('<span class="stats-label">' + escapeHtml(label) + '</span>');
    pills.push('<span class="pill stat">Count ' + counts.total + '</span>');
    pills.push('<span class="pill stat">PDF ' + counts.pdf + '</span>');
    pills.push('<span class="pill stat">Source ' + counts.source + '</span>');
    pills.push('<span class="pill stat">Translated ' + (counts.translated || 0) + '</span>');
    pills.push('<span class="pill stat">Summary ' + counts.summary + '</span>');
    var order = counts.template_order || Object.keys(counts.templates || {});
    for (var i = 0; i < order.length; i++) {
      var tag = order[i];
      var count = (counts.templates && counts.templates[tag]) || 0;
      pills.push('<span class="pill stat">tmpl:' + escapeHtml(tag) + ' ' + count + '</span>');
    }
    row.innerHTML = pills.join("");
  }

  function updateStats(stats) {
    if (!stats) return;
    renderStatsRow("statsTotal", "Total", stats.all);
    renderStatsRow("statsFiltered", "Filtered", stats.filtered);
  }

  function loadMore() {
    if (loading || done) return;
    loading = true;
    var loadingEl = document.getElementById("loading");
    if (loadingEl) loadingEl.textContent = "Loading...";
    var url = semanticMode ? "/api/papers/semantic?" + currentSemanticParams().toString() : "/api/papers?" + currentParams(page).toString();
    var headers = {};
    if (semanticMode && semanticToken) {
      headers.Authorization = "Bearer " + semanticToken;
    }
    fetch(url, { headers: headers }).then(function(res) {
      if (semanticMode && res.status === 403) {
        return clearToken().then(function() {
          deactivateSemanticMode();
          throw new Error("semantic-forbidden");
        });
      }
      return res.json();
    }).then(function(data) {
      if (!semanticMode && data.stats) updateStats(data.stats);
      var results = document.getElementById("results");
      if (results) {
        var startIndex = semanticMode ? 0 : (data.page - 1) * data.page_size;
        for (var i = 0; i < data.items.length; i++) {
          results.insertAdjacentHTML("beforeend", renderItem(data.items[i], startIndex + i + 1));
        }
        if (window.renderMathInElement) {
          renderMathInElement(results, {
            delimiters: [
              {left: '$$', right: '$$', display: true},
              {left: '$', right: '$', display: false},
              {left: '\\\\(', right: '\\\\)', display: false},
              {left: '\\\\[', right: '\\\\]', display: true}
            ],
            throwOnError: false
          });
        }
      }
      if (semanticMode) {
        done = true;
        if (loadingEl) loadingEl.textContent = data.items.length ? "Semantic results loaded." : "No semantic results.";
      } else if (!data.has_more) {
        done = true;
        if (loadingEl) loadingEl.textContent = "End.";
      } else {
        page++;
        if (loadingEl) loadingEl.textContent = "Scroll to load more...";
      }
      loading = false;
    }).catch(function(error) {
      loading = false;
      if (error && error.message === "semantic-forbidden") {
        if (loadingEl) loadingEl.textContent = "Semantic token expired. Falling back to keyword search...";
        resetAndLoad();
        return;
      }
      if (loadingEl) loadingEl.textContent = "Error loading papers.";
    });
  }

  function resetAndLoad() {
    page = 1;
    done = false;
    var results = document.getElementById("results");
    if (results) results.innerHTML = "";
    loadMore();
  }

  function initEventListeners() {
    var eventElements = ["query", "openView", "filterQuery", "filterPdf", "filterSource", "filterTranslated", "filterSummary", "filterTemplate", "sortBy", "sortDir"];
    for (var i = 0; i < eventElements.length; i++) {
      var el = document.getElementById(eventElements[i]);
      if (el) el.addEventListener("change", resetAndLoad);
    }
    var buildBtn = document.getElementById("buildQuery");
    if (buildBtn) {
      buildBtn.addEventListener("click", function() {
        function add(field, value) {
          value = value.trim();
          if (!value) return "";
          if (value.includes(" ")) return field + ':"' + value + '"';
          return field + ":" + value;
        }
        var parts = [];
        var t = document.getElementById("advTitle").value.trim();
        var a = document.getElementById("advAuthor").value.trim();
        var tag = document.getElementById("advTag").value.trim();
        var y = document.getElementById("advYear").value.trim();
        var m = document.getElementById("advMonth").value.trim();
        var v = document.getElementById("advVenue").value.trim();
        if (t) parts.push(add("title", t));
        if (a) parts.push(add("author", a));
        if (tag) {
          var tagParts = tag.split(",");
          for (var j = 0; j < tagParts.length; j++) {
            var val = tagParts[j].trim();
            if (val) parts.push(add("tag", val));
          }
        }
        if (y) parts.push(add("year", y));
        if (m) parts.push(add("month", m));
        if (v) parts.push(add("venue", v));
        var q = parts.join(" ");
        var generatedEl = document.getElementById("generated");
        if (generatedEl) generatedEl.textContent = q;
        var queryEl = document.getElementById("query");
        if (queryEl) queryEl.value = q;
        resetAndLoad();
      });
    }
    var semanticToggle = document.getElementById("semantic-toggle");
    var tokenModal = document.getElementById("token-modal");
    var tokenSubmit = document.getElementById("token-submit");
    var tokenCancel = document.getElementById("token-cancel");
    var tokenInput = document.getElementById("token-input");
    if (semanticToggle && tokenModal) {
      semanticToggle.addEventListener("click", function() {
        setTokenError("");
        if (semanticMode) {
          clearToken().then(function() {
            deactivateSemanticMode();
            resetAndLoad();
          });
          return;
        }
        if (typeof tokenModal.showModal === "function") {
          tokenModal.showModal();
        } else {
          tokenModal.setAttribute("open", "open");
        }
        if (tokenInput) tokenInput.focus();
      });
    }
    if (tokenCancel && tokenModal) {
      tokenCancel.addEventListener("click", function() {
        setTokenError("");
        if (typeof tokenModal.close === "function") {
          tokenModal.close();
        } else {
          tokenModal.removeAttribute("open");
        }
      });
    }
    if (tokenSubmit && tokenInput && tokenModal) {
      tokenSubmit.addEventListener("click", function() {
        var token = tokenInput.value.trim();
        if (!token) {
          setTokenError("Token is required");
          return;
        }
        setTokenError("");
        probeSemanticToken(token).then(function() {
          return writeToken(token).then(function() {
            activateSemanticMode(token);
            if (typeof tokenModal.close === "function") {
              tokenModal.close();
            } else {
              tokenModal.removeAttribute("open");
            }
            resetAndLoad();
          });
        }).catch(function(error) {
          setTokenError(error && error.message ? error.message : "Invalid token");
        });
      });
      tokenInput.addEventListener("keydown", function(event) {
        if (event.key === "Enter") {
          event.preventDefault();
          tokenSubmit.click();
        }
      });
    }
  }

  function initScrollHandler() {
    window.addEventListener("scroll", function() {
      if ((window.innerHeight + window.scrollY) >= (document.body.offsetHeight - 600)) {
        loadMore();
      }
    });
  }

  function initSummaryToggle() {
    document.addEventListener("click", function(event) {
      var target = event.target;
      if (!target || !target.classList.contains("summary-toggle")) return;
      var container = target.closest(".summary-snippet");
      if (!container) return;
      var isOpen = container.classList.toggle("is-open");
      target.setAttribute("aria-expanded", isOpen ? "true" : "false");
      target.textContent = isOpen ? "▴" : "▾";
    });
  }

  function init() {
    initEventListeners();
    initScrollHandler();
    initSummaryToggle();
    updateSemanticUi();
    readToken().then(function(token) {
      if (!token) return;
      return probeSemanticToken(token).then(function() {
        activateSemanticMode(token);
      }).catch(function() {
        return clearToken().then(function() {
          deactivateSemanticMode();
        });
      });
    }).finally(function() {
      loadMore();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
