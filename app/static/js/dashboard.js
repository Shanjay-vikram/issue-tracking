(function () {
  var el = document.getElementById('dash-data');
  if (!el || typeof Chart === 'undefined') return;
  var data = JSON.parse(el.textContent);
  var palette = ['#14284b', '#2f6fb0', '#6f9fd1', '#b26a00', '#1f7a4d', '#b3261e', '#7a8aa6', '#a8c4e3'];
  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
  Chart.defaults.color = '#4a566b';

  function make(id, config) {
    var c = document.getElementById(id);
    if (c) new Chart(c, config);
  }
  var opts = { responsive: true, maintainAspectRatio: false };
  var noLegend = { legend: { display: false } };
  var intAxis = { y: { beginAtZero: true, ticks: { precision: 0 } } };

  if (data.status_chart && data.status_chart.values.length)
    make('chart-status', { type: 'doughnut', data: { labels: data.status_chart.labels,
      datasets: [{ data: data.status_chart.values, backgroundColor: palette, borderWidth: 1 }] },
      options: Object.assign({}, opts, { plugins: { legend: { position: 'right' } } }) });
  make('chart-monthly', { type: 'line', data: { labels: data.monthly.labels,
    datasets: [{ label: 'Tickets', data: data.monthly.values, borderColor: '#2f6fb0', backgroundColor: 'rgba(47,111,176,.12)', fill: true, tension: .25 }] },
    options: Object.assign({}, opts, { plugins: noLegend, scales: intAxis }) });
  if (data.workload && data.workload.values.length)
    make('chart-workload', { type: 'bar', data: { labels: data.workload.labels,
      datasets: [{ label: 'Open tickets', data: data.workload.values, backgroundColor: '#14284b' }] },
      options: Object.assign({}, opts, { indexAxis: 'y', plugins: noLegend, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }) });
  if (data.categories && data.categories.values.length)
    make('chart-categories', { type: 'bar', data: { labels: data.categories.labels,
      datasets: [{ label: 'Problems', data: data.categories.values, backgroundColor: '#2f6fb0' }] },
      options: Object.assign({}, opts, { plugins: noLegend, scales: intAxis }) });
})();
