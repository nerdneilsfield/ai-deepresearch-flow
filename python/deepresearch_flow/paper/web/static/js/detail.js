/* Detail page JavaScript - extracted from embedded inline scripts in paper_detail handler */
(function() {
  'use strict';

  // ========================================
  // View-specific initialization based on body classes/data attributes
  // ========================================

  function init() {
    initFullscreen();
    initMarkdownRendering();
    initFootnotes();
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
  // Markdown rendering (Mermaid + KaTeX)
  // ========================================

  function initMarkdownRendering() {
    // Only run if we have content with mermaid/katex
    if (!document.getElementById('content')) return;

    function initRendering() {
      // Markmap: convert fenced markmap blocks to svg mindmaps
      if (window.markmap && window.markmap.Transformer && window.markmap.Markmap) {
        var transformer = new window.markmap.Transformer();
        document.querySelectorAll('code.language-markmap').forEach(function(code) {
          var pre = code.parentElement;
          if (!pre) return;
          var svg = document.createElement('svg');
          svg.className = 'markmap';
          pre.replaceWith(svg);
          try {
            var result = transformer.transform(code.textContent || '');
            window.markmap.Markmap.create(svg, null, result.root);
          } catch (err) {
            // Ignore markmap parse errors
          }
        });
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
        renderMathInElement(document.getElementById('content'), {
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

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initRendering);
    } else {
      initRendering();
    }
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

    var pdfJsLib = window.pdfjsLib;
    if (!pdfJsLib) return;

    pdfJsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.js';

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
