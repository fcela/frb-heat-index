/* FRB Heat Index — all data loaded from data/*.json; nothing hardcoded. */
(function () {
  'use strict';

  var DATA = 'data/';
  var charts = [];

  function $(id) { return document.getElementById(id); }

  function loadJSON(name) {
    return fetch(DATA + name).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' loading ' + name);
      return r.json();
    });
  }

  function onResize() {
    charts.forEach(function (c) { try { c.resize(); } catch (e) {} });
  }
  window.addEventListener('resize', onResize);

  var HEAT_COLORS = ['#2166ac', '#4393c3', '#92c5de', '#d1e5f0', '#f7f7f7',
                     '#fddbc7', '#f4a582', '#d6604d', '#b2182b']; // cool -> hot

  function renderHeatmap(components, compData) {
    var el = $('heatmap');
    var chart = echarts.init(el);
    charts.push(chart);

    // quarters from the first component that has any data
    var quarters = null;
    components.forEach(function (c) {
      if (!quarters && compData[c] && compData[c].length) quarters = compData[c].map(function (d) { return d.q; });
    });

    var values = [], missing = [];
    components.forEach(function (c, yi) {
      (compData[c] || []).forEach(function (d, xi) {
        if (d.v === null || d.v === undefined) missing.push([xi, yi]);
        else values.push([xi, yi, d.v]);
      });
    });

    var yLabels = components.slice().reverse();
    // ECharts heatmap: y index 0 is at the bottom; reverse so first component is on top.
    var nY = components.length;
    function flipY(yi) { return nY - 1 - yi; }
    var vFlip = values.map(function (t) { return [t[0], flipY(t[1]), t[2]]; });
    var mFlip = missing.map(function (t) { return [t[0], flipY(t[1]), 1]; });

    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'item',
        formatter: function (p) {
          if (p.seriesName === 'missing') {
            var ci0 = nY - 1 - p.value[1];
            return '<b>' + esc(components[ci0]) + '</b><br/>' +
                   esc(quarters[p.value[0]]) + ': no data';
          }
          var ci = nY - 1 - p.value[1];
          var v = p.value[2];
          return '<b>' + esc(components[ci]) + '</b><br/>' +
                 esc(quarters[p.value[0]]) + ': <b>' + v.toFixed(2) + '</b><br/>' +
                 '<span style="color:#888">hotter than ' + Math.round(v * 100) + '% of history</span>';
        }
      },
      grid: { left: 150, right: 30, top: 20, bottom: 70 },
      xAxis: {
        type: 'category', data: quarters, splitArea: { show: false },
        axisLabel: { interval: 15, rotate: 0, fontSize: 10, color: '#5b6b7f' },
        axisTick: { show: false }, axisLine: { show: false }
      },
      yAxis: {
        type: 'category', data: yLabels, inverse: false,
        axisLabel: { fontSize: 11, color: '#334155' },
        axisTick: { show: false }, axisLine: { show: false }
      },
      visualMap: {
        seriesIndex: 0,
        min: 0, max: 1, calculable: true,
        orient: 'horizontal', left: 'center', bottom: 0,
        inRange: { color: HEAT_COLORS },
        text: ['hot — high vulnerability', 'cool — subdued'],
        textStyle: { color: '#5b6b7f', fontSize: 11 }
      },
      series: [
        {
          name: 'heat', type: 'heatmap', data: vFlip,
          itemStyle: { borderColor: '#fff', borderWidth: 0 },
          emphasis: { itemStyle: { borderColor: '#0f1c2e', borderWidth: 1 } }
        },
        {
          name: 'missing', type: 'heatmap', data: mFlip, silent: true,
          itemStyle: { color: '#e6ebf1', borderColor: '#fff', borderWidth: 0 }
        }
      ]
    });
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderAggregate(agg) {
    var el = $('aggchart');
    var chart = echarts.init(el);
    charts.push(chart);
    var qs = agg.map(function (d) { return d.q; });
    var vs = agg.map(function (d) { return d.v; });
    var peak = 0, peakQ = '';
    agg.forEach(function (d) { if (d.v !== null && d.v > peak) { peak = d.v; peakQ = d.q; } });

    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'axis',
        formatter: function (ps) {
          var d = agg[ps[0].dataIndex];
          return '<b>' + esc(d.q) + '</b><br/>aggregate index: <b>' +
                 (d.v === null ? 'n/a' : d.v.toFixed(3)) + '</b>';
        }
      },
      grid: { left: 44, right: 24, top: 30, bottom: 40 },
      xAxis: {
        type: 'category', data: qs, boundaryGap: false,
        axisLabel: { interval: 15, fontSize: 10, color: '#5b6b7f' }
      },
      yAxis: {
        type: 'value', min: 0, max: 1,
        axisLabel: { fontSize: 10, color: '#5b6b7f' },
        splitLine: { lineStyle: { color: '#eef1f5' } }
      },
      series: [{
        name: 'aggregate', type: 'line', data: vs,
        showSymbol: false, connectNulls: false,
        lineStyle: { width: 2, color: '#1d4ed8' },
        areaStyle: { color: 'rgba(29,78,216,0.08)' },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(178,24,43,0.07)' },
          label: { color: '#7f1d1d', fontSize: 11, position: 'insideTop' },
          data: [
            [{ name: '2007 — imbalances build', xAxis: '2007Q1' }, { xAxis: '2007Q4' }],
            [{ name: '2008 — crisis', xAxis: '2008Q1' }, { xAxis: '2008Q4' }]
          ]
        },
        markPoint: peakQ ? {
          symbol: 'pin', symbolSize: 44,
          label: { formatter: 'peak {c}', fontSize: 10, color: '#fff' },
          itemStyle: { color: '#b2182b' },
          data: [{ name: 'peak', coord: [peakQ, peak], value: peak.toFixed(2) }]
        } : undefined
      }]
    });
  }

  function renderMultiples(components, compData) {
    var grid = $('multiples');
    components.forEach(function (c) {
      var card = document.createElement('div');
      card.className = 'multi';
      var h = document.createElement('h3');
      h.textContent = c;
      card.appendChild(h);
      var pts = compData[c] || [];
      var hasData = pts.some(function (d) { return d.v !== null && d.v !== undefined; });
      if (!hasData) {
        var nd = document.createElement('div');
        nd.className = 'nodata';
        nd.textContent = 'No observations in the current panel.';
        card.appendChild(nd);
        grid.appendChild(card);
        return;
      }
      var div = document.createElement('div');
      div.className = 'mini';
      card.appendChild(div);
      grid.appendChild(card);

      var chart = echarts.init(div);
      charts.push(chart);
      chart.setOption({
        animation: false,
        tooltip: {
          trigger: 'axis',
          formatter: function (ps) {
            var d = pts[ps[0].dataIndex];
            return '<b>' + esc(d.q) + '</b><br/>' + esc(c) + ': <b>' +
                   (d.v === null ? 'n/a' : d.v.toFixed(3)) + '</b>';
          }
        },
        grid: { left: 34, right: 10, top: 12, bottom: 22 },
        xAxis: {
          type: 'category',
          data: pts.map(function (d) { return d.q; }),
          boundaryGap: false,
          axisLabel: { interval: 39, fontSize: 9, color: '#8fa3bb' },
          axisTick: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } }
        },
        yAxis: {
          type: 'value', min: 0, max: 1,
          axisLabel: { fontSize: 9, color: '#8fa3bb' },
          splitLine: { lineStyle: { color: '#f1f4f8' } }
        },
        series: [{
          type: 'line',
          data: pts.map(function (d) { return d.v; }),
          showSymbol: false, connectNulls: false,
          lineStyle: { width: 1.5, color: '#0f1c2e' }
        }]
      });
    });
  }

  function renderMeta(meta) {
    var stamp = $('stamp');
    var q = meta.last_updated_quarter || '?';
    var first = meta.start_quarter || '?';
    var n = meta.n_quarters || '?';
    var built = meta.built_at_utc ? meta.built_at_utc.replace('T', ' ').replace('Z', ' UTC') : 'unknown';
    stamp.innerHTML = 'Data through <strong>' + esc(q) + '</strong> · ' + esc(n) +
      ' quarters (' + esc(first) + '–' + esc(q) + ') · panel built ' + esc(built) + '.';
    $('hero-range').textContent = first + ' – ' + q + ' · ' + n + ' quarters · quarterly refresh';

    var stale = meta.stale_sources || [];
    if (stale.length) {
      var b = $('stale-banner');
      b.classList.add('show');
      b.innerHTML = '<strong>Data note:</strong> the following source(s) are flagged stale in the current build: ' +
        stale.map(esc).join(', ') + '. Values that depend on them may lag.';
    }
  }

  function fail(err) {
    ['heatmap', 'aggchart'].forEach(function (id) {
      var el = $(id);
      if (el && !el.dataset.done) el.innerHTML = '<p class="err">Could not load chart data (' + esc(err.message) + ').</p>';
    });
    var m = $('multiples');
    if (m) m.innerHTML = '<p class="err">Could not load component data (' + esc(err.message) + ').</p>';
  }

  function boot() {
    if (typeof echarts === 'undefined') {
      fail(new Error('chart library (ECharts CDN) did not load'));
      return;
    }
    Promise.all([loadJSON('aggregate.json'), loadJSON('components.json'), loadJSON('meta.json')])
      .then(function (res) {
        var agg = res[0], compData = res[1], meta = res[2];
        var components = (meta.components && meta.components.length)
          ? meta.components
          : Object.keys(compData);
        renderMeta(meta);
        renderHeatmap(components, compData);
        renderAggregate(agg);
        renderMultiples(components, compData);
        ['heatmap', 'aggchart'].forEach(function (id) { $(id).dataset.done = '1'; });
      })
      .catch(fail);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
