const _environmentHistory = {};
const _persistentHistory = {};
const _historyView = { period: '24h', end: null, loading: false, generation: 0 };
const HISTORY_PERIOD_MS = { '6h':21600000, '24h':86400000, '3d':259200000, '7d':604800000, '30d':2592000000 };

function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function chartTarget(state) {
  const smart = finiteNumber(state.smart_effective_target);
  if (smart !== null && smart >= 10 && smart <= 40) return smart;
  const climate = finiteNumber(state.ClimateTargetTemperature);
  if (climate !== null && climate >= 10 && climate <= 40) return climate;
  const deci = finiteNumber(state.SetDeciTem);
  if (deci !== null && deci >= 100 && deci <= 400) return deci / 10;
  const raw = finiteNumber(state.SetTem);
  return raw !== null && raw >= 10 && raw <= 40 ? raw : null;
}
function updateEnvironmentHistory(mac, state) {
  const now = Date.now();
  const point = {
    t: now,
    room: finiteNumber(state.RoomTemperature),
    target: chartTarget(state),
    outdoor: finiteNumber(state.OutdoorTemperature),
    humidity: finiteNumber(state.RoomHumidity),
    outdoorHumidity: finiteNumber(state.OutdoorHumidity),
  };
  const history = _environmentHistory[mac] || (_environmentHistory[mac] = []);
  if (!history.length || now - history[history.length - 1].t >= 9000) history.push(point);
  while (history.length > 180) history.shift();
  return history;
}
function showChartTooltip(event, id, label, value, unit, timestamp) {
  const tip = document.getElementById(id);
  if (!tip) return;
  const plot = tip.parentElement.getBoundingClientRect();
  const marker = event.currentTarget?.getBoundingClientRect();
  const pointerX = Number.isFinite(event.clientX) && event.clientX > 0 ? event.clientX : marker.left + marker.width / 2;
  const pointerY = Number.isFinite(event.clientY) && event.clientY > 0 ? event.clientY : marker.top;
  tip.innerHTML = `<b>${escHtml(label)} · ${Number(value).toFixed(1)} ${unit}</b><small>${new Date(Number(timestamp)).toLocaleString('it-IT')}</small>`;
  tip.style.left = `${Math.max(85,Math.min(plot.width-85,pointerX-plot.left))}px`;
  tip.style.top = `${Math.max(70,pointerY-plot.top)}px`;
  tip.classList.add('visible');
}
function hideChartTooltip(id) {
  document.getElementById(id)?.classList.remove('visible');
}
function renderTimeSeriesPanel(mac, history, config) {
  // A portrait-specific coordinate system is essential here: merely making
  // the SVG element taller leaves the wide viewBox letterboxed and the actual
  // plot remains tiny. In portrait the plot therefore gets a genuinely tall
  // viewBox, while landscape and desktop retain the wide timeline.
  const mobileChart = window.matchMedia('(max-width:720px)').matches;
  const portraitChart = !config.compact && window.matchMedia('(max-width:720px) and (orientation:portrait)').matches;
  const compactMobile = config.compact && mobileChart;
  const width = portraitChart || compactMobile ? 460 : 1000;
  const height = portraitChart ? 580 : compactMobile ? 300 : 380;
  const left = portraitChart || compactMobile ? 52 : 64;
  const right = portraitChart || compactMobile ? 16 : 26;
  const top = 24, bottom = portraitChart || compactMobile ? 62 : 54;
  const values = history.flatMap(point => config.series.map(item => point[item.key])).filter(Number.isFinite);
  if (!values.length) return `<section class="chart-panel ${config.className || ''}"><div class="chart-panel-header"><div><div class="chart-panel-title">${config.title}</div><span class="chart-panel-subtitle">${config.subtitle}</span></div></div><div class="chart-empty">Nessun dato disponibile nello storico di Home Assistant</div></section>`;
  let min = config.fixedMin ?? Math.floor(Math.min(...values) - config.padding);
  let max = config.fixedMax ?? Math.ceil(Math.max(...values) + config.padding);
  if (max - min < config.minimumRange) { const middle = (max + min) / 2; min = middle - config.minimumRange / 2; max = middle + config.minimumRange / 2; }
  const times = history.map(point => point.t);
  const tMin = Math.min(...times), tMax = Math.max(...times);
  const x = t => left + (tMax === tMin ? .5 : (t - tMin) / (tMax - tMin)) * (width-left-right);
  const y = value => top + (max-value)/(max-min)*(height-top-bottom);
  const ticks = Array.from({length:5},(_,index) => ({value:max-index*(max-min)/4,y:top+index*(height-top-bottom)/4}));
  const timeTickCount = portraitChart || compactMobile ? 3 : 5;
  const timeTicks = Array.from({length:timeTickCount},(_,index) => ({t:tMin+index*(tMax-tMin)/(timeTickCount-1),x:left+index*(width-left-right)/(timeTickCount-1)}));
  const tooltipId = `chart-tip-${mac}-${config.id}`.replace(/[^a-zA-Z0-9_-]/g,'_');
  const drawSeries = item => {
    const points = history.map(point => ({point,value:point[item.key]})).filter(entry => Number.isFinite(entry.value));
    if (!points.length) return '';
    const coords = points.map(entry => `${x(entry.point.t)},${y(entry.value)}`).join(' ');
    const area = item.area && points.length > 1 ? `<polygon class="chart-area" fill="${item.color}" points="${x(points[0].point.t)},${height-bottom} ${coords} ${x(points[points.length-1].point.t)},${height-bottom}"/>` : '';
    const line = points.length > 1 ? `<polyline class="chart-series ${item.css || ''}" stroke="${item.color}" points="${coords}"/>` : '';
    // Keep long Recorder histories readable: show a limited number of visual
    // markers while preserving every sample in the line. Transparent hit
    // targets make the markers easy to select with a finger.
    const markerLimit = config.compact ? 28 : 48;
    const dotStep = Math.max(1, Math.ceil(points.length / markerLimit));
    const dots = points.filter((_,index) => index % dotStep === 0 || index === points.length - 1).map(entry => `<g class="chart-point-group" tabindex="0" role="button" aria-label="${item.label}: ${Number(entry.value).toFixed(1)} ${config.unit}" onpointerdown="showChartTooltip(event,'${tooltipId}','${item.label}',${entry.value},'${config.unit}',${entry.point.t})" onmouseenter="showChartTooltip(event,'${tooltipId}','${item.label}',${entry.value},'${config.unit}',${entry.point.t})" onmousemove="showChartTooltip(event,'${tooltipId}','${item.label}',${entry.value},'${config.unit}',${entry.point.t})" onmouseleave="hideChartTooltip('${tooltipId}')" onfocus="showChartTooltip(event,'${tooltipId}','${item.label}',${entry.value},'${config.unit}',${entry.point.t})" onblur="hideChartTooltip('${tooltipId}')"><circle class="chart-point-hit" cx="${x(entry.point.t)}" cy="${y(entry.value)}" r="14"/><circle class="chart-point" cx="${x(entry.point.t)}" cy="${y(entry.value)}" r="4.5" fill="${item.color}"/><title>${item.label}: ${Number(entry.value).toFixed(1)} ${config.unit} · ${new Date(entry.point.t).toLocaleString('it-IT')}</title></g>`).join('');
    return area + line + dots;
  };
  return `<section class="chart-panel ${config.className || ''} ${portraitChart ? 'portrait-chart' : ''} ${compactMobile ? 'compact-mobile-chart' : ''}"><div class="chart-panel-header"><div><div class="chart-panel-title">${config.title}</div><span class="chart-panel-subtitle">${config.subtitle}</span></div><div class="ops-chart-legend">${config.series.map(item => `<span><i style="background:${item.color};${item.css === 'target' ? 'background:repeating-linear-gradient(90deg,'+item.color+' 0 7px,transparent 7px 11px)' : ''}"></i>${item.label}</span>`).join('')}</div></div><div class="ops-chart-plot"><div class="chart-tooltip" id="${tooltipId}"></div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${config.title}">${ticks.map(tick => `<line class="chart-grid-line" x1="${left}" y1="${tick.y}" x2="${width-right}" y2="${tick.y}"/><text class="chart-axis-label" x="${left-10}" y="${tick.y+4}" text-anchor="end">${tick.value.toFixed(config.decimals)}${config.unit}</text>`).join('')}<line class="chart-axis-line" x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}"/><line class="chart-axis-line" x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}"/>${config.series.map(drawSeries).join('')}${timeTicks.map(tick => `<text class="chart-axis-label" x="${tick.x}" y="${height-13}" text-anchor="middle">${new Date(tick.t).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</text>`).join('')}</svg></div></section>`;
}
function renderEnvironmentChart(mac, state, detailed = false) {
  const liveHistory = updateEnvironmentHistory(mac, state);
  const recorderHistory = _persistentHistory[mac]?.points || [];
  const history = detailed ? recorderHistory : (recorderHistory.length > 1 ? recorderHistory : liveHistory);
  if (!detailed) {
    if (history.length < 2) return `<div class="ops-chart control-chart-loading"><div class="ops-chart-legend"><span><i style="background:#22d3ee"></i>Interna</span><span><i style="background:#facc15"></i>Target</span><span><i style="background:#fb7185"></i>Esterna</span></div><div class="chart-empty">${_historyView.loading ? 'Caricamento dati reali da HA Recorder…' : 'Raccolta dati in corso…'}</div></div>`;
    return renderTimeSeriesPanel(mac,history,{id:'control-temperature',compact:true,className:'control-chart',title:'Temperature reali',subtitle:recorderHistory.length > 1 ? `HA Recorder · periodo ${_historyView.period}` : 'Dati live in attesa dello storico Recorder',unit:'°',padding:1,minimumRange:6,decimals:0,series:[{key:'room',label:'Interna',color:'#22d3ee',area:true},{key:'target',label:'Target',color:'#facc15',css:'target'},{key:'outdoor',label:'Esterna',color:'#fb7185',css:'outdoor'}]});
  }
  return `<div class="chart-panels">${renderTimeSeriesPanel(mac,history,{id:'temperature',title:'Temperature',subtitle:'Storico persistente HA Recorder',unit:'°',padding:1,minimumRange:6,decimals:0,series:[{key:'room',label:'Interna',color:'#22d3ee',area:true},{key:'target',label:'Target',color:'#facc15',css:'target'},{key:'outdoor',label:'Esterna',color:'#fb7185',css:'outdoor'}]})}${renderTimeSeriesPanel(mac,history,{id:'humidity',className:'humidity',title:'Umidità relativa',subtitle:'Sensori interni ed esterni',unit:'%',padding:5,minimumRange:20,fixedMin:0,fixedMax:100,decimals:0,series:[{key:'humidity',label:'Interna',color:'#38bdf8',area:true},{key:'outdoorHumidity',label:'Esterna',color:'#a78bfa',css:'outdoor'}]})}</div>`;
}
function historyWindowLabel() {
  const end = _historyView.end || Date.now();
  const start = end - HISTORY_PERIOD_MS[_historyView.period];
  const format = value => new Date(value).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  return `${format(start)} → ${format(end)}`;
}
function historyToolbar() {
  const atNow = !_historyView.end;
  return `<div class="chart-toolbar"><div class="chart-periods">${Object.keys(HISTORY_PERIOD_MS).map(period => `<button class="${_historyView.period === period ? 'active' : ''}" onclick="setHistoryPeriod('${period}')">${period}</button>`).join('')}</div><div class="chart-navigation"><button onclick="shiftHistory(-1)">← Indietro</button><span class="chart-window-label">${historyWindowLabel()}</span><button onclick="shiftHistory(1)" ${atNow ? 'disabled' : ''}>Avanti →</button><button onclick="goToLatestHistory()" ${atNow ? 'disabled' : ''}>Adesso</button></div><span class="chart-recorder-note">Memoria persistente HA Recorder${_historyView.loading ? ' · caricamento…' : ''}</span></div>`;
}
function clearPersistentHistory() {
  for (const key of Object.keys(_persistentHistory)) delete _persistentHistory[key];
}
async function loadPersistentHistory(force = false) {
  const data = window._lastPanelData || [];
  if (!data.length || _historyView.loading) return;
  const generation = ++_historyView.generation;
  _historyView.loading = true;
  renderChartsPage(data, false);
  const endQuery = _historyView.end ? `&end=${Math.round(_historyView.end)}` : '';
  await Promise.all(data.map(async device => {
    const cacheKey = `${_historyView.period}:${_historyView.end || 'latest'}`;
    if (!force && _persistentHistory[device.mac]?.key === cacheKey) return;
    try {
      const result = await apiFetch(`${PANEL_HISTORY_URL}?mac=${encodeURIComponent(device.mac)}&period=${_historyView.period}${endQuery}`);
      if (generation === _historyView.generation) _persistentHistory[device.mac] = {key:cacheKey,points:result.points || [],entities:result.entities || {}};
    } catch (error) {
      if (generation === _historyView.generation) _persistentHistory[device.mac] = {key:cacheKey,points:[],error:error.message};
    }
  }));
  if (generation !== _historyView.generation) return;
  _historyView.loading = false;
  renderChartsPage(data, false);
  renderControlCharts(data);
}
function renderControlCharts(data) {
  for (const device of data) {
    const container = document.getElementById(`control-chart-${device.mac}`);
    if (container) container.innerHTML = renderEnvironmentChart(device.mac,device.state || {},false);
  }
}
function setHistoryPeriod(period) {
  if (!HISTORY_PERIOD_MS[period]) return;
  _historyView.period = period;
  clearPersistentHistory();
  renderChartsPage(window._lastPanelData || [], false);
  loadPersistentHistory(true);
}
function shiftHistory(direction) {
  const duration = HISTORY_PERIOD_MS[_historyView.period];
  const now = Date.now();
  const next = (_historyView.end || now) + direction * duration;
  _historyView.end = next >= now - 60000 ? null : next;
  clearPersistentHistory();
  renderChartsPage(window._lastPanelData || [], false);
  loadPersistentHistory(true);
}
function goToLatestHistory() {
  _historyView.end = null;
  clearPersistentHistory();
  renderChartsPage(window._lastPanelData || [], false);
  loadPersistentHistory(true);
}
function renderChartsPage(data, requestHistory = true) {
  const content = document.getElementById('chartsContent');
  if (!content) return;
  const cards = data.map(device => {
    const state = device.state || {};
    const value = (raw,unit) => finiteNumber(raw) == null ? '--' : `${Number(raw).toFixed(1)}${unit}`;
    const stored = _persistentHistory[device.mac];
    const chart = stored?.error ? `<div class="chart-empty">Storico non disponibile: ${escHtml(stored.error)}</div>` : renderEnvironmentChart(device.mac,state,true);
    return `<article class="chart-detail-card" id="detail-chart-${escHtml(device.mac)}"><button class="chart-expand" onclick="toggleChartExpand('${escHtml(device.mac)}')">⛶ Espandi</button><h3>${escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac)}</h3><p>Profilo ${escHtml(state.ActivePreset || 'manuale')} · ${state.Pow ? 'unità accesa' : 'unità spenta'} · ${stored?.points?.length || 0} campioni storici</p>${chart}<div class="chart-values"><div>Interna<b>${value(state.RoomTemperature,'°')}</b></div><div>Target<b>${value(chartTarget(state),'°')}</b></div><div>Esterna<b>${value(state.OutdoorTemperature,'°')}</b></div><div>Umi. interna<b>${value(state.RoomHumidity,'%')}</b></div><div>Umi. esterna<b>${value(state.OutdoorHumidity,'%')}</b></div></div></article>`;
  }).join('');
  content.innerHTML = historyToolbar() + (_historyView.loading && !Object.keys(_persistentHistory).length ? '<div class="chart-loading">Caricamento dello storico persistente da Home Assistant…</div>' : cards);
  if (requestHistory && !_historyView.loading) loadPersistentHistory();
}
function toggleChartExpand(mac) {
  document.getElementById(`detail-chart-${mac}`)?.classList.toggle('expanded');
}
