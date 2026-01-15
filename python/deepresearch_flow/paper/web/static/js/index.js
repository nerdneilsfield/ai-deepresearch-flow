/* Index page functionality */
(function() {
  'use strict';

  var page = 1;
  var loading = false;
  var done = false;

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
    var url = "/api/papers?" + currentParams(page).toString();
    fetch(url).then(function(res) { return res.json(); }).then(function(data) {
      if (data.stats) updateStats(data.stats);
      var results = document.getElementById("results");
    if (results) {
      var startIndex = (data.page - 1) * data.page_size;
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
      if (!data.has_more) {
        done = true;
        if (loadingEl) loadingEl.textContent = "End.";
      } else {
        page++;
        if (loadingEl) loadingEl.textContent = "Scroll to load more...";
      }
      loading = false;
    }).catch(function() {
      loading = false;
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
    loadMore();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
