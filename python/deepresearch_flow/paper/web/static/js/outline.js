/* Outline functionality extracted from outline_assets */
(function() {
  var bound = false;

  function buildOutline() {
    var content = document.getElementById('content');
    if (!content) return;

    var headings = content.querySelectorAll('h2, h3, h4');
    var outline = document.getElementById('outline');
    var toggle = document.getElementById('outlineToggle');
    var close = document.getElementById('outlineClose');
    var outlineContent = document.getElementById('outlineContent');

    if (!outline || !toggle || !close || !outlineContent) return;

    outlineContent.innerHTML = '';
    if (headings.length === 0) return;
    for (var i = 0; i < headings.length; i++) {
      var h = headings[i];
      if (!h.id) h.id = 'heading-' + i;
      var a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.trim();
      a.className = 'outline-' + h.tagName.toLowerCase();
      outlineContent.appendChild(a);
    }

    if (!bound) {
      toggle.addEventListener('click', function() {
        outline.style.display = 'block';
        toggle.style.display = 'none';
        document.body.classList.add('outline-open');
      });

      close.addEventListener('click', function() {
        outline.style.display = 'none';
        toggle.style.display = 'block';
        document.body.classList.remove('outline-open');
      });

      var savedState = sessionStorage.getItem('outlineVisible');
      if (savedState === 'true') {
        outline.style.display = 'block';
        toggle.style.display = 'none';
        document.body.classList.add('outline-open');
      } else {
        toggle.style.display = 'block';
        document.body.classList.remove('outline-open');
      }

      var observer = new MutationObserver(function() {
        var isVisible = outline.style.display === 'block';
        sessionStorage.setItem('outlineVisible', String(isVisible));
      });
      observer.observe(outline, { attributes: true, attributeFilter: ['style'] });
      bound = true;
    }
  }

  function initOutline() {
    buildOutline();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initOutline);
  } else {
    initOutline();
  }

  document.addEventListener('content:updated', initOutline);
})();
