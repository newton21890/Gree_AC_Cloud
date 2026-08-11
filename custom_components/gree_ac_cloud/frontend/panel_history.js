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
const _apexCharts = new Map();
const _apexRenderTickets = new Map();

function isApexChartScope(id, scope) {
  const control = id.endsWith('-control-temperature');
  return scope === 'all' || (scope === 'control' ? control : !control);
}
function destroyApexCharts(scope = 'all') {
  for (const [id,chart] of _apexCharts.entries()) {
    if (!isApexChartScope(id,scope)) continue;
    try { chart.destroy(); } catch (error) {}
    _apexCharts.delete(id);
  }
  for (const id of _apexRenderTickets.keys()) {
    if (isApexChartScope(id,scope)) _apexRenderTickets.delete(id);
  }
}
function apexChartHeight(config) {
  const mobile = window.matchMedia('(max-width:720px)').matches;
  const portrait = !config.compact && window.matchMedia('(max-width:720px) and (orientation:portrait)').matches;
  if (portrait) return 520;
  if (config.compact) return mobile ? 265 : 230;
  return mobile ? 330 : 370;
}
function apexValue(value, decimals, unit) {
  const number = finiteNumber(value);
  return number === null ? '—' : `${number.toFixed(decimals)}${unit}`;
}
function apexTimeLabel(timestamp) {
  const date = new Date(Number(timestamp));
  const options = _historyView.period === '6h' || _historyView.period === '24h'
    ? {hour:'2-digit',minute:'2-digit'}
    : {day:'2-digit',month:'2-digit',hour:'2-digit'};
  return date.toLocaleString('it-IT', options);
}
function queueApexChart(id, history, config) {
  const ticket = Symbol(id);
  _apexRenderTickets.set(id,ticket);
  const render = () => {
    if (_apexRenderTickets.get(id) !== ticket) return;
    const element = document.getElementById(id);
    if (!element || !element.isConnected || typeof ApexCharts === 'undefined') return;
    _apexRenderTickets.delete(id);
    const previous = _apexCharts.get(id);
    if (previous) {
      try { previous.destroy(); } catch (error) {}
      _apexCharts.delete(id);
    }
    // Una serie con tutti i valori null non produce alcuna linea: ApexCharts la
    // ometterebbe dal disegno ma lascerebbe la voce in legenda, generando un
    // grafico "mancante". Le filtriamo prima di costruire il grafico (la nota
    // nel pannello è aggiunta da renderTimeSeriesPanel) e ricalcoliamo indici
    // e stili (dropShadow, spessori, tratteggi) sulla lista filtrata.
    const activeSeries = config.series.filter(item =>
      history.some(point => finiteNumber(point[item.key]) !== null)
    );
    if (!activeSeries.length) return;
    const lineSeries = activeSeries.map(item => ({
      name: item.label,
      type: item.area ? 'area' : 'line',
      data: history.map(point => [Number(point.t), finiteNumber(point[item.key])]),
    }));
    const values = history.flatMap(point => activeSeries.map(item => finiteNumber(point[item.key]))).filter(Number.isFinite);
    let minimum = config.fixedMin;
    let maximum = config.fixedMax;
    if (minimum === undefined || maximum === undefined) {
      const low = Math.min(...values), high = Math.max(...values);
      const range = Math.max(high-low,config.minimumRange || 1);
      const pad = Math.max(config.padding || 0,range*.05);
      minimum = minimum ?? Math.floor((low-pad)*10)/10;
      maximum = maximum ?? Math.ceil((high+pad)*10)/10;
    }
    const outdoorSeries = activeSeries.map((item,index) => item.css === 'outdoor' ? index : -1).filter(index => index >= 0);
    const dashArray = activeSeries.map(item => item.css === 'target' ? 7 : 0);
    const widths = activeSeries.map(item => item.css === 'outdoor' ? 4 : item.css === 'target' ? 2 : 2.4);
    const chart = new ApexCharts(element, {
      chart: {
        id,
        type: 'line',
        height: apexChartHeight(config),
        background: 'transparent',
        foreColor: '#8290a5',
        fontFamily: 'Inter,system-ui,-apple-system,sans-serif',
        animations: {enabled:false},
        dropShadow:{enabled:outdoorSeries.length > 0,enabledOnSeries:outdoorSeries,top:0,left:0,blur:4,color:activeSeries[outdoorSeries[0]]?.color,opacity:.5},
        toolbar: {show:false},
        zoom: {enabled:!config.compact,type:'x',autoScaleYaxis:false},
        selection: {enabled:false},
      },
      series: lineSeries,
      colors: activeSeries.map(item => item.color),
      stroke: {curve:'smooth',width:widths,dashArray,lineCap:'round'},
      fill: {
        type:'gradient',
        opacity:activeSeries.map(item => item.area ? .24 : 0),
        gradient:{shade:'dark',type:'vertical',shadeIntensity:.2,opacityFrom:.32,opacityTo:.02,stops:[0,90,100]},
      },
      dataLabels: {enabled:false},
      markers: {size:0,hover:{size:5}},
      grid: {borderColor:'#223047',strokeDashArray:2,padding:{left:4,right:12,top:0,bottom:0}},
      legend: {show:true,position:'top',horizontalAlign:'right',fontSize:'11px',labels:{colors:'#a7b3c5'},markers:{width:16,height:3,radius:2}},
      xaxis: {
        type:'datetime',
        min:Math.min(...history.map(point => Number(point.t))),
        max:Math.max(...history.map(point => Number(point.t))),
        axisBorder:{show:true,color:'#52627a'},
        axisTicks:{show:false},
        labels:{datetimeUTC:false,rotate:0,hideOverlappingLabels:true,formatter:value => apexTimeLabel(value),style:{fontSize:'10px'}},
        tooltip:{enabled:false},
      },
      yaxis: {
        min:minimum,
        max:maximum,
        tickAmount:4,
        forceNiceScale:true,
        labels:{formatter:value => apexValue(value,config.decimals,config.unit),style:{fontSize:'10px'}},
      },
      tooltip: {
        shared:true,
        intersect:false,
        theme:'dark',
        x:{formatter:value => new Date(Number(value)).toLocaleString('it-IT')},
        y:{formatter:value => apexValue(value,config.decimals,config.unit)},
      },
      noData:{text:'Nessun dato disponibile'},
    });
    _apexCharts.set(id,chart);
    chart.render().catch(error => console.error('ApexCharts render failed',error));
  };
  requestAnimationFrame(render);
}
function renderTimeSeriesPanel(mac, history, config) {
  const values = history.flatMap(point => config.series.map(item => finiteNumber(point[item.key]))).filter(Number.isFinite);
  if (!values.length) return `<section class="chart-panel ${config.className || ''}"><div class="chart-panel-header"><div><div class="chart-panel-title">${config.title}</div><span class="chart-panel-subtitle">${config.subtitle}</span></div></div><div class="chart-empty">Nessun dato disponibile nello storico di Home Assistant</div></section>`;
  const missing = config.series.filter(item => !history.some(point => finiteNumber(point[item.key]) !== null));
  const id = `apex-chart-${mac}-${config.id}`.replace(/[^a-zA-Z0-9_-]/g,'_');
  queueApexChart(id,history,config);
  return `<section class="chart-panel apex-chart-panel ${config.className || ''}"><div class="chart-panel-header"><div><div class="chart-panel-title">${config.title}</div><span class="chart-panel-subtitle">${config.subtitle}</span></div></div><div id="${id}" class="apex-chart-host" role="img" aria-label="${config.title}"></div>${missing.length ? `<div class="chart-missing-note">Serie senza dati in questo periodo: ${missing.map(item => escHtml(item.label)).join(', ')}</div>` : ''}</section>`;
}
function renderEnvironmentChart(mac, state, detailed = false) {
  const liveHistory = updateEnvironmentHistory(mac, state);
  const recorderHistory = _persistentHistory[mac]?.points || [];
  const history = detailed ? recorderHistory : (recorderHistory.length > 1 ? recorderHistory : liveHistory);
  if (!detailed) {
    if (history.length < 2) return `<div class="ops-chart control-chart-loading"><div class="ops-chart-legend"><span><i style="background:#22d3ee"></i>Interna</span><span><i style="background:#facc15"></i>Target</span><span><i style="background:#fb7185"></i>Esterna</span></div><div class="chart-empty">${_historyView.loading ? 'Caricamento dati reali da HA Recorder…' : 'Raccolta dati in corso…'}</div></div>`;
    return renderTimeSeriesPanel(mac,history,{id:'control-temperature',compact:true,className:'control-chart',title:'Temperature reali',subtitle:recorderHistory.length > 1 ? `HA Recorder · periodo ${_historyView.period}` : 'Dati live in attesa dello storico Recorder',unit:'°',padding:1,minimumRange:6,decimals:0,series:[{key:'room',label:'Interna',color:'#22d3ee',area:true},{key:'target',label:'Target',color:'#facc15',css:'target'},{key:'outdoor',label:'Esterna',color:'#fb7185',css:'outdoor'}]});
  }
  return `<div class="chart-panels">${renderTimeSeriesPanel(mac,history,{id:'temperature',title:'Temperature',subtitle:'Storico persistente HA Recorder · linea esterna evidenziata',unit:'°',padding:1,minimumRange:6,decimals:0,series:[{key:'room',label:'Interna',color:'#22d3ee',area:true},{key:'target',label:'Target',color:'#facc15',css:'target'},{key:'outdoor',label:'Esterna',color:'#ff5c8a',css:'outdoor'}]})}${renderTimeSeriesPanel(mac,history,{id:'humidity',className:'humidity',title:'Umidità relativa',subtitle:'Sensori interni ed esterni · scala adattiva',unit:'%',padding:5,minimumRange:20,decimals:0,series:[{key:'humidity',label:'Interna',color:'#38bdf8',area:true},{key:'outdoorHumidity',label:'Esterna',color:'#d8b4fe',css:'outdoor'}]})}</div>`;
}
function historyWindowLabel() {
  const end = _historyView.end || Date.now();
  const start = end - HISTORY_PERIOD_MS[_historyView.period];
  const format = value => new Date(value).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  return `${format(start)} → ${format(end)}`;
}
function inferredBaselinePower(point, actualW) {
  const explicit = finiteNumber(point.baselinePower);
  if (explicit !== null) return Math.max(actualW,explicit);
  const explicitSaving = finiteNumber(point.savingPower);
  if (explicitSaving !== null) return actualW + Math.max(0,explicitSaving);
  // Compatibility for history recorded before the baseline sensor existed.
  // The estimator uses 70% nominal without a limit, 50% for D2 and 5% for D1.
  if (point.dred === 'D1') return actualW * 14;
  if (point.dred === 'D2') return actualW * 1.4;
  return actualW;
}
function buildEnergyHistory(history) {
  const source = history.filter(point => finiteNumber(point.power) !== null).sort((a,b) => a.t - b.t);
  if (source.length < 2) return [];
  let actualKwh = 0;
  let baselineKwh = 0;
  return source.map((point,index) => {
    const actualW = Math.max(0,finiteNumber(point.power) || 0);
    const baselineW = inferredBaselinePower(point,actualW);
    if (index) {
      const previous = source[index - 1];
      const elapsedHours = Math.max(0,(point.t - previous.t) / 3600000);
      const previousActual = Math.max(0,finiteNumber(previous.power) || 0);
      const previousBaseline = inferredBaselinePower(previous,previousActual);
      actualKwh += ((previousActual + actualW) / 2) * elapsedHours / 1000;
      baselineKwh += ((previousBaseline + baselineW) / 2) * elapsedHours / 1000;
    }
    return {...point,powerKw:actualW / 1000,baselineKw:baselineW / 1000,savingKw:Math.max(0,baselineW - actualW) / 1000,energyKwh:actualKwh,baselineEnergyKwh:baselineKwh,savingKwh:Math.max(0,baselineKwh - actualKwh)};
  });
}
function profileEnergyScenarios(state) {
  const labels = {day:'Giorno',night:'Notte',away:'Assente'};
  return Object.entries(state.Presets || {}).filter(([,preset]) => preset && preset.enabled !== false).map(([key,preset]) => {
    const dred = preset.dred || 'No action';
    let reduction = 0;
    let range = false;
    if (dred === 'D1') reduction = 93;
    else if (dred === 'D2') reduction = 29;
    else if (dred === 'D3') reduction = 0;
    else if (dred === 'Smart') { reduction = 29; range = true; }
    if (preset.quiet) reduction = 100 - ((100 - reduction) * .85);
    const hold = preset.hold_action === 'fan_only' ? 'Solo ventola a comfort' : preset.hold_action === 'd1_ventilation' ? 'D1 a comfort' : 'Spegnimento a comfort';
    return {name:labels[key] || key,reduction,range,hold,dred,quiet:Boolean(preset.quiet)};
  });
}
function renderEnergyIndicators(mac,state,history) {
  const energy = buildEnergyHistory(history);
  if (energy.length < 2) return `<section class="energy-section"><div class="energy-section-head"><div><span class="ops-eyebrow">ENERGIA STIMATA</span><h4>Consumi e opportunità di risparmio</h4></div></div><div class="energy-empty">Lo storico energetico verrà mostrato dopo la raccolta di almeno due campioni del sensore Estimated Power. Seleziona il modello corretto dell'unità per abilitare la stima.</div></section>`;
  const last = energy[energy.length - 1];
  const actual = last.energyKwh;
  const baseline = last.baselineEnergyKwh;
  const saved = last.savingKwh;
  const savingPct = baseline > 0 ? saved / baseline * 100 : 0;
  const peak = Math.max(...energy.map(point => point.powerKw));
  const average = actual / Math.max((last.t - energy[0].t) / 3600000,.001);
  const profileSavings = {};
  for (let index=1; index<energy.length; index++) {
    const point = energy[index];
    const previous = energy[index - 1];
    const presetNames = {day:'Giorno',night:'Notte',away:'Assente',manual:'Manuale'};
    const preset = presetNames[previous.preset] || previous.preset || 'Manuale';
    profileSavings[preset] = (profileSavings[preset] || 0) + Math.max(0,point.savingKwh - previous.savingKwh);
  }
  const profileRows = Object.entries(profileSavings).filter(([,value]) => value > .0001).sort((a,b) => b[1] - a[1]);
  const scenarios = profileEnergyScenarios(state);
  const powerChart = renderTimeSeriesPanel(mac,energy,{id:'energy-power',title:'Potenza elettrica stimata',subtitle:'Confronto con lo stesso modo operativo senza DRED e Quiet',unit:' kW',padding:.08,minimumRange:.3,decimals:2,series:[{key:'powerKw',label:'Consumo stimato',color:'#fbbf24',area:true},{key:'baselineKw',label:'Riferimento',color:'#94a3b8',css:'target'},{key:'savingKw',label:'Risparmio istantaneo',color:'#34d399'}]});
  const cumulativeChart = renderTimeSeriesPanel(mac,energy,{id:'energy-cumulative',title:'Energia cumulata nel periodo',subtitle:'Integrale della potenza stimata sui campioni HA Recorder',unit:' kWh',padding:.03,minimumRange:.1,decimals:2,series:[{key:'energyKwh',label:'Consumo',color:'#fb923c',area:true},{key:'baselineEnergyKwh',label:'Riferimento',color:'#94a3b8',css:'target'},{key:'savingKwh',label:'Risparmio',color:'#22c55e'}]});
  return `<section class="energy-section"><div class="energy-section-head"><div><span class="ops-eyebrow">ENERGIA STIMATA</span><h4>Consumi e opportunità di risparmio</h4><p>Periodo ${_historyView.period} · stima basata sul modello dell'unità, non su un contatore elettrico.</p></div><span class="energy-estimate-badge">STIMA · NON FATTURAZIONE</span></div><div class="energy-kpis"><article><span>Consumo periodo</span><b>${actual.toFixed(2)} kWh</b><small>Potenza media ${average.toFixed(2)} kW</small></article><article><span>Riferimento comparabile</span><b>${baseline.toFixed(2)} kWh</b><small>Stesso modo senza DRED/Quiet</small></article><article class="saving"><span>Risparmio stimato</span><b>${saved.toFixed(2)} kWh</b><small>${savingPct.toFixed(1)}% sul riferimento</small></article><article><span>Picco stimato</span><b>${peak.toFixed(2)} kW</b><small>Massimo nel periodo selezionato</small></article></div><div class="chart-panels energy-panels">${powerChart}${cumulativeChart}</div><div class="energy-insights"><article><h5>Risparmio attribuito ai profili</h5>${profileRows.length ? `<div class="energy-profile-rows">${profileRows.map(([preset,value]) => `<div><span>${escHtml(preset)}</span><b>${value.toFixed(2)} kWh</b></div>`).join('')}</div>` : '<p>Non sono ancora presenti riduzioni attribuibili nello storico selezionato.</p>'}<small>Attribuzione basata sul preset registrato da Home Assistant durante ogni intervallo.</small></article><article><h5>Potenziale delle impostazioni</h5>${scenarios.length ? `<div class="energy-scenarios">${scenarios.map(item => `<div><span><b>${escHtml(item.name)}</b><small>${escHtml(item.hold)} · I-Demand ${escHtml(item.dred)}${item.quiet ? ' · Quiet' : ''}</small></span><strong>${item.range ? 'fino a ' : ''}${item.reduction.toFixed(0)}%</strong></div>`).join('')}</div>` : '<p>Nessun profilo automatico abilitato.</p>'}<small>Riduzione teorica della potenza durante richieste equivalenti. Il risparmio a comfort dipende dal tempo reale senza domanda e non è una promessa di consumo.</small></article></div></section>`;
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
  const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
  if (activeTab === 'charts') renderChartsPage(data, false);
  if (activeTab === 'devices') renderControlCharts(data);
}
function renderControlCharts(data) {
  destroyApexCharts('control');
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
  destroyApexCharts('detail');
  const cards = data.map(device => {
    const state = device.state || {};
    const value = (raw,unit) => finiteNumber(raw) == null ? '--' : `${Number(raw).toFixed(1)}${unit}`;
    const stored = _persistentHistory[device.mac];
    const chart = stored?.error ? `<div class="chart-empty">Storico non disponibile: ${escHtml(stored.error)}</div>` : renderEnvironmentChart(device.mac,state,true);
    const energy = stored?.error ? '' : renderEnergyIndicators(device.mac,state,stored?.points || []);
    return `<article class="chart-detail-card" id="detail-chart-${escHtml(device.mac)}"><button class="chart-expand" onclick="toggleChartExpand('${escHtml(device.mac)}')">⛶ Espandi</button><h3>${escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac)}</h3><p>Profilo ${escHtml(state.ActivePreset || 'manuale')} · ${state.Pow ? 'unità accesa' : 'unità spenta'} · ${stored?.points?.length || 0} campioni storici</p>${chart}<div class="chart-values"><div>Interna<b>${value(state.RoomTemperature,'°')}</b></div><div>Target<b>${value(chartTarget(state),'°')}</b></div><div>Esterna<b>${value(state.OutdoorTemperature,'°')}</b></div><div>Umi. interna<b>${value(state.RoomHumidity,'%')}</b></div><div>Umi. esterna<b>${value(state.OutdoorHumidity,'%')}</b></div></div>${energy}</article>`;
  }).join('');
  content.innerHTML = historyToolbar() + (_historyView.loading && !Object.keys(_persistentHistory).length ? '<div class="chart-loading">Caricamento dello storico persistente da Home Assistant…</div>' : cards);
  if (requestHistory && !_historyView.loading) loadPersistentHistory();
}
function toggleChartExpand(mac) {
  document.getElementById(`detail-chart-${mac}`)?.classList.toggle('expanded');
  // ApexCharts si riallinea alla larghezza della card (che cambia con
  // l'espansione) ascoltando il resize della finestra: lo segnaliamo dopo
  // il cambio di layout.
  window.dispatchEvent(new Event('resize'));
}
