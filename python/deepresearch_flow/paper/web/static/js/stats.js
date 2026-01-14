/* Stats page functionality */
(function() {
  'use strict';

  function initStats() {
    fetch('/api/stats')
      .then(function(res) { return res.json(); })
      .then(function(data) {
        function bar(el, title, items) {
          var chart = echarts.init(document.getElementById(el));
          var labels = items.map(function(x) { return x.label; });
          var counts = items.map(function(x) { return x.count; });
          chart.setOption({
            title: { text: title },
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: labels },
            yAxis: { type: 'value' },
            series: [{ type: 'bar', data: counts }]
          });
        }

        if (data.years) bar('year', 'Publication Year', data.years);
        if (data.months) bar('month', 'Publication Month', data.months);
        if (data.tags) bar('tags', 'Top Tags', data.tags.slice(0, 20));
        if (data.keywords) bar('keywords', 'Top Keywords', data.keywords.slice(0, 20));
        if (data.authors) bar('authors', 'Top Authors', data.authors.slice(0, 20));
        if (data.venues) bar('venues', 'Top Venues', data.venues.slice(0, 20));
      })
      .catch(function(err) {
        console.error('Error loading stats:', err);
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initStats);
  } else {
    initStats();
  }
})();
