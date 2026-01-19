/* Detail page JavaScript - extracted from embedded inline scripts in paper_detail handler */
(function() {
  'use strict';

  // ========================================
  // View-specific initialization based on body classes/data attributes
  // ========================================

  function init() {
    initFullscreen();
    initMarkdownContent();
    initBackToTop();

    // View-specific initializers
    if (document.getElementById('translationLang')) {
      initTranslationSelect();
    }
    if (document.getElementById('splitLeft') || document.getElementById('splitRight')) {
      initSplitView();
    }
    if (document.getElementById('the-canvas')) {
      initPdfView();
    }
  }

  // ========================================
  // Back-to-top button
  // ========================================

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = String(text || '');
    return div.innerHTML;
  }

  function initBackToTop() {
    var button = document.getElementById('backToTop');
    if (!button) return;

    function updateVisibility() {
      if (window.scrollY > 120) {
        button.classList.add('visible');
      } else {
        button.classList.remove('visible');
      }
    }

    button.addEventListener('click', function() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    document.addEventListener('scroll', updateVisibility, { passive: true });
    updateVisibility();
  }

  // ========================================
  // Fullscreen functionality
  // ========================================

  function initFullscreen() {
    var fullscreenEnter = document.getElementById('fullscreenEnter');
    var fullscreenExit = document.getElementById('fullscreenExit');

    function setFullscreen(enable) {
      document.body.classList.toggle('detail-fullscreen', enable);
      if (document.body.classList.contains('split-view')) {
        document.body.classList.toggle('split-controls-collapsed', enable);
        var toggle = document.getElementById('splitControlsToggle');
        if (toggle) {
          toggle.setAttribute('aria-expanded', enable ? 'false' : 'true');
        }
      }
    }

    if (fullscreenEnter) {
      fullscreenEnter.addEventListener('click', function() { setFullscreen(true); });
    }
    if (fullscreenExit) {
      fullscreenExit.addEventListener('click', function() { setFullscreen(false); });
    }
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape' && document.body.classList.contains('detail-fullscreen')) {
        setFullscreen(false);
      }
    });
  }

  // ========================================
  // Markdown rendering (Marked + DOMPurify) + enhancements
  // ========================================

  var DOMPURIFY_CONFIG = {
    ADD_TAGS: ['sup', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col'],
    ADD_ATTR: ['colspan', 'rowspan', 'align'],
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel|data:image\/)|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i
  };

  function initMarkdownContent() {
    var content = document.getElementById('content');
    if (!content) return;

    var markdownUrl = content.dataset.markdownUrl;
    var rawMarkdownUrl = content.dataset.rawMarkdownUrl;
    var imagesBaseUrl = content.dataset.imagesBaseUrl || '';
    if (!markdownUrl) {
      var rawText = content.textContent || '';
      if (content.children.length === 0 && rawText.trim()) {
        var rendered = renderMarkdown(rawText, imagesBaseUrl);
        content.innerHTML = rendered;
      } else {
        rewriteRenderedImages(imagesBaseUrl);
      }
      if (rawMarkdownUrl) {
        var statusEl = document.getElementById('markdownStatus');
        var rawEl = document.getElementById('rawMarkdown');
        if (statusEl) statusEl.textContent = 'Loading raw markdown...';
        fetch(rawMarkdownUrl).then(function(res) {
          if (!res.ok) throw new Error('Failed to load raw markdown');
          return res.text();
        }).then(function(text) {
          if (rawEl) rawEl.textContent = text;
          if (statusEl) statusEl.remove();
        }).catch(function() {
          if (statusEl) statusEl.textContent = 'Failed to load raw markdown.';
        });
      }
      runMarkdownEnhancements();
      initFootnotes();
      dispatchContentUpdated();
      return;
    }

    var status = document.getElementById('markdownStatus');
    var rawContainer = document.getElementById('rawMarkdown');
    if (status) status.textContent = 'Loading markdown...';

    fetch(markdownUrl).then(function(res) {
      if (!res.ok) throw new Error('Failed to load markdown');
      return res.text();
    }).then(function(text) {
      if (rawContainer) rawContainer.textContent = text;
      var html = renderMarkdown(text, imagesBaseUrl);
      content.innerHTML = html;
      if (status) status.remove();
      rewriteRenderedImages(imagesBaseUrl);
      runMarkdownEnhancements();
      initFootnotes();
      dispatchContentUpdated();
    }).catch(function() {
      if (status) status.textContent = 'Failed to load markdown.';
    });
  }

  function renderMarkdown(markdown, imagesBaseUrl) {
    var normalized = normalizeFootnoteDefinitions(markdown || '');
    var footnoteData = extractFootnotes(normalized);
    var mathData = extractMathPlaceholders(footnoteData.markdown);

    if (!window.marked) {
      return '<pre><code>' + escapeHtml(markdown) + '</code></pre>';
    }

    var renderer = new window.marked.Renderer();
    renderer.image = function(href, title, text) {
      var src = rewriteImageUrl(href, imagesBaseUrl);
      if (!src) return '';
      var html = '<img src="' + escapeHtml(src) + '" alt="' + escapeHtml(text || '') + '"';
      if (title) {
        html += ' title="' + escapeHtml(title) + '"';
      }
      return html + ' />';
    };

    if (window.marked && window.marked.setOptions) {
      window.marked.setOptions({ gfm: true, breaks: false });
    }
    var parsed = window.marked.parse(mathData.text || '', { renderer: renderer });
    parsed = replaceMathPlaceholders(parsed, mathData.placeholders);
    parsed = injectFootnotes(parsed, footnoteData);
    if (window.DOMPurify) {
      return window.DOMPurify.sanitize(parsed, DOMPURIFY_CONFIG);
    }
    return parsed;
  }

  function rewriteImageUrl(url, imagesBaseUrl) {
    if (!url) return url;
    if (!imagesBaseUrl) return url;
    if (isAbsoluteUrl(url)) return url;

    var cleaned = String(url);
    while (cleaned.indexOf('../') === 0) {
      cleaned = cleaned.slice(3);
    }
    cleaned = cleaned.replace(/^\.\//, '');
    cleaned = cleaned.replace(/^\/+/, '');
    if (cleaned.indexOf('images/') === 0) {
      cleaned = cleaned.slice('images/'.length);
    }
    var base = String(imagesBaseUrl).replace(/\/+$/, '');
    return base + '/' + cleaned;
  }

  function rewriteRenderedImages(imagesBaseUrl) {
    if (!imagesBaseUrl) return;
    var content = document.getElementById('content');
    if (!content) return;
    content.querySelectorAll('img').forEach(function(img) {
      var src = img.getAttribute('src');
      var rewritten = rewriteImageUrl(src, imagesBaseUrl);
      if (rewritten && rewritten !== src) {
        img.setAttribute('src', rewritten);
      }
    });
  }

  function isAbsoluteUrl(url) {
    return /^(?:[a-z][a-z0-9+.\-]*:|\/\/|#)/i.test(url) || url.charAt(0) === '/';
  }

  function normalizeFootnoteDefinitions(text) {
    return String(text || '').replace(/^\[\^([^\]]+)\]\s+/gm, '[^$1]: ');
  }

  function extractFootnotes(text) {
    var lines = String(text || '').split(/\r?\n/);
    var out = [];
    var notes = {};
    var order = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var match = line.match(/^\[\^([^\]]+)\]:\s*(.*)$/);
      if (!match) {
        out.push(line);
        i += 1;
        continue;
      }
      var rawId = match[1].trim();
      var id = sanitizeFootnoteId(rawId);
      var content = match[2] || '';
      var parts = [content];
      i += 1;
      while (i < lines.length) {
        var next = lines[i];
        if (/^\[\^([^\]]+)\]:\s*/.test(next)) {
          break;
        }
        if (/^\s{2,}|\t/.test(next)) {
          parts.push(next.replace(/^\s{2,}|\t/, ''));
          i += 1;
          continue;
        }
        if (next.trim() === '') {
          parts.push('');
          i += 1;
          continue;
        }
        break;
      }
      if (!notes[id]) {
        order.push(id);
      }
      notes[id] = parts.join('\n').trim();
    }
    return { markdown: out.join('\n'), notes: notes, order: order };
  }

  function injectFootnotes(html, footnoteData) {
    if (!footnoteData || !footnoteData.order || footnoteData.order.length === 0) {
      return html;
    }
    var notes = footnoteData.notes || {};
    var order = footnoteData.order;
    var replaced = String(html || '').replace(/\[\^([^\]]+)\]/g, function(match, rawId) {
      var id = sanitizeFootnoteId(rawId);
      if (!notes[id]) return match;
      return '<sup class="footnote-ref"><a href="#fn' + id + '" id="fnref' + id + '">[' +
        escapeHtml(rawId) + ']</a></sup>';
    });

    var items = order.map(function(id) {
      var noteText = notes[id] || '';
      var body = window.marked ? window.marked.parse(noteText) : '<p>' + escapeHtml(noteText) + '</p>';
      return '<li id="fn' + id + '">' + body +
        ' <a class="footnote-backref" href="#fnref' + id + '">↩</a></li>';
    }).join('');
    var footnotesHtml = '<div class="footnotes"><ol>' + items + '</ol></div>';
    return replaced + footnotesHtml;
  }

  function sanitizeFootnoteId(value) {
    return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '-');
  }

  function extractMathPlaceholders(text) {
    var placeholders = {};
    var out = [];
    var idx = 0;
    var inFence = false;
    var fenceChar = '';
    var fenceLen = 0;
    var inlineDelimLen = 0;

    function nextPlaceholder(value) {
      var key = '@@MATH_' + Object.keys(placeholders).length + '@@';
      placeholders[key] = value;
      return key;
    }

    while (idx < text.length) {
      var atLineStart = idx === 0 || text[idx - 1] === '\n';
      if (inlineDelimLen === 0 && atLineStart) {
        var lineEnd = text.indexOf('\n', idx);
        if (lineEnd === -1) lineEnd = text.length;
        var line = text.slice(idx, lineEnd);
        var stripped = line.replace(/^[ ]+/, '');
        var leadingSpaces = line.length - stripped.length;
        if (leadingSpaces <= 3 && stripped) {
          var first = stripped[0];
          if (first === '`' || first === '~') {
            var runLen = 0;
            while (runLen < stripped.length && stripped[runLen] === first) {
              runLen += 1;
            }
            if (runLen >= 3) {
              if (!inFence) {
                inFence = true;
                fenceChar = first;
                fenceLen = runLen;
              } else if (first === fenceChar && runLen >= fenceLen) {
                inFence = false;
                fenceChar = '';
                fenceLen = 0;
              }
              out.push(line);
              idx = lineEnd;
              continue;
            }
          }
        }
      }

      if (inFence) {
        out.push(text[idx]);
        idx += 1;
        continue;
      }

      if (inlineDelimLen > 0) {
        var delim = '`'.repeat(inlineDelimLen);
        if (text.slice(idx, idx + inlineDelimLen) === delim) {
          out.push(delim);
          idx += inlineDelimLen;
          inlineDelimLen = 0;
          continue;
        }
        out.push(text[idx]);
        idx += 1;
        continue;
      }

      if (text[idx] === '`') {
        var inlineRun = 0;
        while (idx + inlineRun < text.length && text[idx + inlineRun] === '`') {
          inlineRun += 1;
        }
        inlineDelimLen = inlineRun;
        out.push('`'.repeat(inlineRun));
        idx += inlineRun;
        continue;
      }

      if (text.slice(idx, idx + 2) === '$$' && (idx === 0 || text[idx - 1] !== '\\')) {
        var searchFrom = idx + 2;
        var end = text.indexOf('$$', searchFrom);
        while (end !== -1 && text[end - 1] === '\\') {
          searchFrom = end + 2;
          end = text.indexOf('$$', searchFrom);
        }
        if (end !== -1) {
          out.push(nextPlaceholder(text.slice(idx, end + 2)));
          idx = end + 2;
          continue;
        }
      }

      if (text[idx] === '$' && text.slice(idx, idx + 2) !== '$$' && (idx === 0 || text[idx - 1] !== '\\')) {
        var lineEndInline = text.indexOf('\n', idx + 1);
        if (lineEndInline === -1) lineEndInline = text.length;
        var searchInline = idx + 1;
        var endInline = text.indexOf('$', searchInline);
        while (endInline !== -1 && endInline < lineEndInline && text[endInline - 1] === '\\') {
          searchInline = endInline + 1;
          endInline = text.indexOf('$', searchInline);
        }
        if (endInline !== -1 && endInline < lineEndInline) {
          out.push(nextPlaceholder(text.slice(idx, endInline + 1)));
          idx = endInline + 1;
          continue;
        }
      }

      out.push(text[idx]);
      idx += 1;
    }

    return { text: out.join(''), placeholders: placeholders };
  }

  function replaceMathPlaceholders(html, placeholders) {
    var out = String(html || '');
    Object.keys(placeholders || {}).forEach(function(key) {
      var value = escapeHtml(placeholders[key]);
      out = out.split(key).join(value);
    });
    return out;
  }

  function runMarkdownEnhancements() {
    var content = document.getElementById('content');
    if (!content) return;

    // Markmap: convert fenced markmap blocks to autoloader containers
    var markmapBlocks = 0;
    document.querySelectorAll('code.language-markmap').forEach(function(code) {
      var pre = code.parentElement;
      if (!pre) return;
      var wrapper = document.createElement('div');
      wrapper.className = 'markmap';
      var template = document.createElement('script');
      template.type = 'text/template';
      template.textContent = code.textContent || '';
      wrapper.appendChild(template);
      pre.replaceWith(wrapper);
      markmapBlocks += 1;
    });
    function resizeMarkmaps() {
      document.querySelectorAll('.markmap svg').forEach(function(svg) {
        try {
          var bbox = svg.getBBox();
          if (!bbox || !bbox.height) {
            svg.style.height = '800px';
            svg.style.width = '100%';
            return;
          }
          var height = Math.ceil(bbox.height * 2);
          svg.style.height = height + 'px';
          if (bbox.width && bbox.width > svg.clientWidth) {
            svg.style.width = Math.ceil(bbox.width * 2) + 'px';
            if (svg.parentElement) {
              svg.parentElement.style.overflowX = 'auto';
            }
          } else {
            svg.style.width = '100%';
          }
        } catch (err) {
          // Ignore sizing errors
        }
      });
    }

    if (markmapBlocks && window.markmap && window.markmap.autoLoader && window.markmap.autoLoader.renderAll) {
      window.markmap.autoLoader.renderAll();
      setTimeout(resizeMarkmaps, 120);
      setTimeout(resizeMarkmaps, 600);
      setTimeout(resizeMarkmaps, 1600);
      if (!window.__markmapResizeBound) {
        window.__markmapResizeBound = true;
        window.addEventListener('resize', function() {
          setTimeout(resizeMarkmaps, 120);
        });
      }
    }

    // Mermaid: convert fenced code blocks to mermaid divs
    document.querySelectorAll('code.language-mermaid').forEach(function(code) {
      var pre = code.parentElement;
      var div = document.createElement('div');
      div.className = 'mermaid';
      div.textContent = code.textContent;
      pre.replaceWith(div);
    });

    if (window.mermaid) {
      mermaid.initialize({ startOnLoad: false });
      mermaid.run();
    }

    if (window.renderMathInElement) {
      renderMathInElement(content, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '$', right: '$', display: false},
          {left: '\\(', right: '\\)', display: false},
          {left: '\\[', right: '\\]', display: true}
        ],
        throwOnError: false
      });
    }
  }

  function dispatchContentUpdated() {
    var event;
    try {
      event = new CustomEvent('content:updated');
    } catch (err) {
      event = document.createEvent('Event');
      event.initEvent('content:updated', true, true);
    }
    document.dispatchEvent(event);
  }

  // ========================================
  // Footnote tooltips
  // ========================================

  function initFootnotes() {
    if (!document.querySelector('.footnotes')) return;

    var notes = {};
    document.querySelectorAll('.footnotes li[id]').forEach(function(li) {
      var id = li.getAttribute('id');
      if (!id) return;
      var clone = li.cloneNode(true);
      clone.querySelectorAll('a.footnote-backref').forEach(function(el) { el.remove(); });
      var text = (clone.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) notes['#' + id] = text.length > 400 ? text.slice(0, 397) + '…' : text;
    });
    document.querySelectorAll('.footnote-ref a[href^="#fn"]').forEach(function(link) {
      var ref = link.getAttribute('href');
      var text = notes[ref];
      if (!text) return;
      link.dataset.footnote = text;
      link.classList.add('footnote-tip');
    });
  }

  // ========================================
  // Translation language select
  // ========================================

  function initTranslationSelect() {
    var translationSelect = document.getElementById('translationLang');
    if (!translationSelect) return;

    translationSelect.addEventListener('change', function() {
      var params = new URLSearchParams(window.location.search);
      params.set('view', 'translated');
      params.set('lang', translationSelect.value);
      window.location.search = params.toString();
    });
  }

  // ========================================
  // Split view controls
  // ========================================

  function initSplitView() {
    var leftSelect = document.getElementById('splitLeft');
    var rightSelect = document.getElementById('splitRight');
    var swapButton = document.getElementById('splitSwap');
    var tightenButton = document.getElementById('splitTighten');
    var widenButton = document.getElementById('splitWiden');
    var controlsToggle = document.getElementById('splitControlsToggle');
    var searchInput = document.getElementById('splitSearch');

    if (!leftSelect || !rightSelect) return;

    function updateSplit() {
      var params = new URLSearchParams(window.location.search);
      params.set('view', 'split');
      params.set('left', leftSelect.value);
      params.set('right', rightSelect.value);
      window.location.search = params.toString();
    }

    leftSelect.addEventListener('change', updateSplit);
    rightSelect.addEventListener('change', updateSplit);

    function setControlsCollapsed(collapsed) {
      document.body.classList.toggle('split-controls-collapsed', collapsed);
      if (controlsToggle) {
        controlsToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      }
    }

    if (controlsToggle) {
      controlsToggle.addEventListener('click', function() {
        var collapsed = document.body.classList.contains('split-controls-collapsed');
        setControlsCollapsed(!collapsed);
      });
    }

    if (document.body.classList.contains('detail-fullscreen')) {
      setControlsCollapsed(true);
    }

    function filterOptions(select, term) {
      var value = select.value;
      for (var i = 0; i < select.options.length; i++) {
        var option = select.options[i];
        var label = (option.textContent || "").toLowerCase();
        var match = !term || label.indexOf(term) !== -1 || option.value === value;
        option.hidden = !match;
        option.style.display = match ? "" : "none";
      }
    }

    if (searchInput) {
      searchInput.addEventListener('input', function() {
        var term = searchInput.value.trim().toLowerCase();
        filterOptions(leftSelect, term);
        filterOptions(rightSelect, term);
      });
    }

    if (swapButton) {
      swapButton.addEventListener('click', function() {
        var leftValue = leftSelect.value;
        leftSelect.value = rightSelect.value;
        rightSelect.value = leftValue;
        updateSplit();
      });
    }

    // Width adjustment with localStorage persistence
    var widthSteps = ["1200px", "1400px", "1600px", "1800px", "2000px", "100%"];
    var widthIndex = widthSteps.length - 1;

    try {
      var stored = localStorage.getItem('splitWidthIndex');
      if (stored !== null) {
        var parsed = Number.parseInt(stored, 10);
        if (!Number.isNaN(parsed)) {
          widthIndex = Math.max(0, Math.min(widthSteps.length - 1, parsed));
        }
      }
    } catch (err) {
      // Ignore storage errors (e.g. private mode)
    }

    function applySplitWidth() {
      var value = widthSteps[widthIndex];
      document.documentElement.style.setProperty('--split-max-width', value);
      try {
        localStorage.setItem('splitWidthIndex', String(widthIndex));
      } catch (err) {
        // Ignore storage errors
      }
    }

    if (tightenButton) {
      tightenButton.addEventListener('click', function() {
        widthIndex = Math.max(0, widthIndex - 1);
        applySplitWidth();
      });
    }

    if (widenButton) {
      widenButton.addEventListener('click', function() {
        widthIndex = Math.min(widthSteps.length - 1, widthIndex + 1);
        applySplitWidth();
      });
    }

    applySplitWidth();
  }

  // ========================================
  // PDF view (canvas-based)
  // ========================================

  function initPdfView() {
    var pdfUrl = document.body.dataset.pdfUrl;
    if (!pdfUrl) return;

    var started = false;
    var loadRequested = false;

    function startPdf() {
      if (started) return true;
      var pdfJsLib = window.pdfjsLib;
      if (!pdfJsLib || !pdfJsLib.getDocument) return false;

      started = true;
      pdfJsLib.GlobalWorkerOptions.workerSrc =
        window.PDFJS_WORKER_SRC || '/pdfjs/build/pdf.worker.js';

      var pdfDoc = null;
      var pageNum = 1;
      var pageRendering = false;
      var pageNumPending = null;
      var zoomLevel = 1.0;
      var canvas = document.getElementById('the-canvas');
      var ctx = canvas.getContext('2d');

      function renderPage(num) {
        pageRendering = true;
        pdfDoc.getPage(num).then(function(page) {
          var baseViewport = page.getViewport({scale: 1});
          var containerWidth = canvas.clientWidth || baseViewport.width;
          var fitScale = containerWidth / baseViewport.width;
          var scale = fitScale * zoomLevel;

          var viewport = page.getViewport({scale: scale});
          var outputScale = window.devicePixelRatio || 1;

          canvas.width = Math.floor(viewport.width * outputScale);
          canvas.height = Math.floor(viewport.height * outputScale);
          canvas.style.width = Math.floor(viewport.width) + 'px';
          canvas.style.height = Math.floor(viewport.height) + 'px';

          var transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null;
          var renderContext = { canvasContext: ctx, viewport: viewport, transform: transform };
          var renderTask = page.render(renderContext);
          renderTask.promise.then(function() {
            pageRendering = false;
            document.getElementById('page_num').textContent = String(pageNum);
            if (pageNumPending !== null) {
              var next = pageNumPending;
              pageNumPending = null;
              renderPage(next);
            }
          });
        });
      }

      function queueRenderPage(num) {
        if (pageRendering) {
          pageNumPending = num;
        } else {
          renderPage(num);
        }
      }

      function onPrevPage() {
        if (pageNum <= 1) return;
        pageNum--;
        queueRenderPage(pageNum);
      }

      function onNextPage() {
        if (pageNum >= pdfDoc.numPages) return;
        pageNum++;
        queueRenderPage(pageNum);
      }

      function adjustZoom(delta) {
        zoomLevel = Math.max(0.5, Math.min(3.0, zoomLevel + delta));
        queueRenderPage(pageNum);
      }

      document.getElementById('prev').addEventListener('click', onPrevPage);
      document.getElementById('next').addEventListener('click', onNextPage);
      document.getElementById('zoomOut').addEventListener('click', function() { adjustZoom(-0.1); });
      document.getElementById('zoomIn').addEventListener('click', function() { adjustZoom(0.1); });

      pdfJsLib.getDocument(pdfUrl).promise.then(function(pdfDoc_) {
        pdfDoc = pdfDoc_;
        document.getElementById('page_count').textContent = String(pdfDoc.numPages);
        renderPage(pageNum);
      });

      var resizeTimer = null;
      window.addEventListener('resize', function() {
        if (!pdfDoc) return;
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function() { queueRenderPage(pageNum); }, 150);
      });

      return true;
    }

    if (startPdf()) return;

    if (!loadRequested && window.PDFJS_SCRIPT_SRC) {
      loadRequested = true;
      var script = document.createElement('script');
      script.src = window.PDFJS_SCRIPT_SRC;
      script.defer = true;
      script.onload = function() {
        try {
          window.dispatchEvent(new Event('pdfjs:loaded'));
        } catch (err) {
          var event = document.createEvent('Event');
          event.initEvent('pdfjs:loaded', true, true);
          window.dispatchEvent(event);
        }
      };
      document.head.appendChild(script);
    }

    window.addEventListener('pdfjs:loaded', function() {
      startPdf();
    }, { once: true });
  }

  // ========================================
  // Initialize on DOM ready
  // ========================================

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
