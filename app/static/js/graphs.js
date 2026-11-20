// Renders saved graphs and drives the graph builder on the problem page.
(function () {
  if (typeof Chart === 'undefined') return;
  var ink = '#4a566b';
  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
  Chart.defaults.color = ink;

  function draw(canvas, payload, title) {
    if (canvas._chart) canvas._chart.destroy();
    var common = { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, title: { display: !!title, text: title || '' } } };
    if (payload.type === 'scatter') {
      canvas._chart = new Chart(canvas, { type: 'scatter', data: { datasets: [{ label: payload.y, data: payload.points, backgroundColor: '#2f6fb0' }] },
        options: Object.assign({}, common, { scales: { x: { title: { display: true, text: payload.x } }, y: { title: { display: true, text: payload.y } } } }) });
    } else {
      canvas._chart = new Chart(canvas, { type: payload.type, data: { labels: payload.labels,
        datasets: [{ label: payload.y, data: payload.values, borderColor: '#2f6fb0', backgroundColor: payload.type === 'bar' ? '#2f6fb0' : 'rgba(47,111,176,.15)', pointRadius: payload.values.length > 80 ? 0 : 3, tension: .15 }] },
        options: Object.assign({}, common, { scales: { x: { title: { display: true, text: payload.x }, ticks: { maxTicksLimit: 12 } }, y: { title: { display: true, text: payload.y } } } }) });
    }
  }

  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'Request failed'); return j; });
    });
  }

  document.querySelectorAll('canvas[data-graph-url]').forEach(function (c) {
    getJSON(c.dataset.graphUrl).then(function (p) { draw(c, p, c.dataset.title); })
      .catch(function (e) { c.insertAdjacentHTML('afterend', '<div class="text-danger small">' + e.message.replace(/</g, '&lt;') + '</div>'); });
  });

  var builder = document.getElementById('graph-builder');
  if (!builder) return;
  var fileSel = builder.querySelector('[data-role=file]'), xSel = builder.querySelector('[data-role=x]'),
      ySel = builder.querySelector('[data-role=y]'), typeSel = builder.querySelector('[data-role=type]'),
      msg = builder.querySelector('[data-role=msg]'), prevBox = builder.querySelector('[data-role=preview]'),
      canvas = builder.querySelector('canvas'), saveBtn = builder.querySelector('[data-role=save]'),
      hiddenFile = builder.querySelector('input[name=test_file_id]'), hiddenX = builder.querySelector('input[name=x_column]'),
      hiddenY = builder.querySelector('input[name=y_column]'), hiddenType = builder.querySelector('input[name=chart_type]');
  var previewUrl = fileSel && fileSel.dataset.previewUrl;

  function fill(sel, cols) {
    sel.innerHTML = '';
    cols.forEach(function (c) { var o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
  }
  function loadFile() {
    msg.textContent = ''; prevBox.innerHTML = ''; saveBtn.disabled = true;
    if (!fileSel.value) return;
    getJSON(previewUrl.replace('__ID__', fileSel.value)).then(function (p) {
      fill(xSel, p.columns); fill(ySel, p.numeric.length ? p.numeric : p.columns);
      if (p.columns.length > 1 && xSel.value === ySel.value) ySel.selectedIndex = Math.min(1, ySel.options.length - 1);
      var t = document.createElement('table'); t.className = 'table table-sm';
      var h = '<thead><tr>' + p.columns.map(function (c) { return '<th></th>'; }).join('') + '</tr></thead>';
      t.innerHTML = h + '<tbody></tbody>';
      t.querySelectorAll('th').forEach(function (th, i) { th.textContent = p.columns[i]; });
      p.rows.forEach(function (r) { var tr = document.createElement('tr'); r.forEach(function (v) { var td = document.createElement('td'); td.textContent = v; tr.appendChild(td); }); t.tBodies[0].appendChild(tr); });
      var wrap = document.createElement('div'); wrap.className = 'preview-wrap'; wrap.appendChild(t);
      var cap = document.createElement('div'); cap.className = 'small text-muted mb-1'; cap.textContent = 'Preview: first ' + p.rows.length + ' of ' + p.total_rows + ' rows';
      prevBox.appendChild(cap); prevBox.appendChild(wrap);
      render();
    }).catch(function (e) { msg.textContent = e.message; });
  }
  function render() {
    if (!fileSel.value || !xSel.value || !ySel.value) return;
    var url = fileSel.dataset.chartUrl.replace('__ID__', fileSel.value) + '?x=' + encodeURIComponent(xSel.value) + '&y=' + encodeURIComponent(ySel.value) + '&type=' + encodeURIComponent(typeSel.value);
    getJSON(url).then(function (p) {
      msg.textContent = p.truncated ? 'Large data set: the chart shows a sample of points.' : '';
      draw(canvas, p, ''); saveBtn.disabled = false;
      hiddenFile.value = fileSel.value; hiddenX.value = xSel.value; hiddenY.value = ySel.value; hiddenType.value = typeSel.value;
    }).catch(function (e) { msg.textContent = e.message; saveBtn.disabled = true; if (canvas._chart) canvas._chart.destroy(); });
  }
  fileSel.addEventListener('change', loadFile);
  [xSel, ySel, typeSel].forEach(function (s) { s.addEventListener('change', render); });
  loadFile();
})();
