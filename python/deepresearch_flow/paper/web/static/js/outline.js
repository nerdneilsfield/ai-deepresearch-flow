/* Outline functionality extracted from outline_assets */
(function() {
  function initOutline() {
    const content = document.getElementById('content');
    if (!content) return;

    const headings = content.querySelectorAll('h2, h3, h4');
    if (headings.length === 0) return;

    const outline = document.getElementById('outline');
    const toggle = document.getElementById('outlineToggle');
    const close = document.getElementById('outlineClose');
    const outlineContent = document.getElementById('outlineContent');

    if (!outline || !toggle || !close || !outlineContent) return;

    for (let i = 0; i < headings.length; i++) {
      const h = headings[i];
      if (!h.id) h.id = 'heading-' + i;
      const a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.trim();
      a.className = 'outline-' + h.tagName.toLowerCase();
      outlineContent.appendChild(a);
    }

    toggle.addEventListener('click', function() {
      outline.style.display = 'block';
      toggle.style.display = 'none';
    });

    close.addEventListener('click', function() {
      outline.style.display = 'none';
      toggle.style.display = 'block';
    });

    const savedState = sessionStorage.getItem('outlineVisible');
    if (savedState === 'true') {
      outline.style.display = 'block';
      toggle.style.display = 'none';
    } else {
      toggle.style.display = 'block';
    }

    const observer = new MutationObserver(function() {
      const isVisible = outline.style.display === 'block';
      sessionStorage.setItem('outlineVisible', String(isVisible));
    });
    observer.observe(outline, { attributes: true, attributeFilter: ['style'] });
  }

  // Run when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initOutline);
  } else {
    initOutline();
  }
})();
