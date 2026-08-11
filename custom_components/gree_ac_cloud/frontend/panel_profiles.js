const PROFILE_NAMES = ['day','night','away'];
const PROFILE_LABELS = {day:'Giorno',night:'Notte',away:'Assente'};
const PROFILE_MODE_LABELS = {auto:'Auto profilo',cool:'Solo Cool',heat:'Solo Heat',dry:'Solo Dry'};
const PROFILE_CURVE_LABELS = {gentle:'Graduale',balanced:'Bilanciata',rapid:'Rapida'};
const PROFILE_MODE_ICONS = {
  cool: '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12h20M12 2v20"/><path d="m20 16-4-4 4-4"/><path d="M4 8l4 4-4 4"/><path d="m16 4-4 4-4-4"/><path d="m8 20 4-4 4 4"/></svg>',
  heat: '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
  dry: '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/></svg>',
};
let _profileEditor = null;

function profileModeChip(mode) {
  const icon = PROFILE_MODE_ICONS[mode] || '';
  const label = mode === 'cool' || mode === 'heat' || mode === 'dry' ? ({cool:'Cool',heat:'Heat',dry:'Dry'})[mode] : escHtml(mode);
  return `<span class="profile-mode-chip">${icon}${label}</span>`;
}
function renderProfilesPage(data) {
  const content = document.getElementById('profilesContent');
  if (!content) return;
  const devices = data.map(device => {
    const state = device.state || {};
    const active = state.ActivePreset || 'manual';
    const trend = finiteNumber(state.smart_temperature_trend_c_per_hour);
    const trendSummary = trend == null ? 'storico decisionale in raccolta' : `${trend > 0 ? '+' : ''}${trend.toFixed(2)} °C/h (${trend > .15 ? 'in aumento' : trend < -.15 ? 'in calo' : 'stabile'})`;
    const presets = state.Presets || {};
    const cards = PROFILE_NAMES.map(name => {
      const preset = presets[name] || {};
      const strategy = preset.smart_mode || 'auto';
      const allowed = preset.allowed_modes || (strategy === 'auto' ? ['cool','heat','dry'] : [strategy]);
      const modes = strategy === 'auto' ? allowed : [strategy];
      const target = finiteNumber(preset.target_temperature);
      const margin = finiteNumber(preset.deadband) ?? .5;
      const curve = preset.work_curve || 'balanced';
      return `<article class="profile-card ${active === name ? 'active' : ''}" role="button" tabindex="0" onclick="openProfileEditor('${escHtml(device.mac)}','${name}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openProfileEditor('${escHtml(device.mac)}','${name}')}" aria-label="Configura profilo ${PROFILE_LABELS[name]}"><div class="profile-card-top"><h4>${PROFILE_LABELS[name]}</h4><span class="profile-badge">${active === name ? 'ATTIVO' : preset.enabled ? 'DISPONIBILE' : 'DISABILITATO'}</span></div><div class="profile-mode-path">${PROFILE_MODE_LABELS[strategy] || strategy}</div><div>${modes.map(profileModeChip).join('') || '<span class="profile-mode-chip">Nessuna modalità</span>'}</div><div class="profile-rule"><span>Target configurato</span><b>${target == null ? '--' : target.toFixed(1)+' °C'}</b></div><div class="profile-rule"><span>Target effettivo</span><b>${active === name && finiteNumber(state.smart_effective_target) != null ? Number(state.smart_effective_target).toFixed(1)+' °C' : 'quando attivo'}</b></div><div class="profile-rule"><span>Arresto / ripartenza</span><b>${target == null ? '--' : `${target.toFixed(1)}° / ${(target+margin).toFixed(1)}° Cool`}</b></div><div class="profile-rule"><span>Umidità Dry</span><b>${preset.humidity_threshold == null ? 'non configurata' : '&gt; '+Number(preset.humidity_threshold).toFixed(0)+'%'}</b></div><div class="profile-rule"><span>Curva di lavoro</span><b>${PROFILE_CURVE_LABELS[curve]}</b></div><div class="profile-rule"><span>Ventola · Quiet</span><b>${escHtml(preset.fan_speed || 'Smart')} · ${preset.quiet ? 'sì' : 'no'}</b></div><div class="profile-rule"><span>Compensazione esterna</span><b>${preset.outdoor_compensation ? 'attiva' : 'disattiva'}</b></div><div class="profile-rule"><span>I-Demand</span><b>${escHtml(preset.dred || 'Nessuna azione')}</b></div><div class="profile-actions"><button class="btn profile-edit-btn" onclick="event.stopPropagation();openProfileEditor('${escHtml(device.mac)}','${name}')">Configura</button><button class="btn ${active === name ? 'active' : ''}" ${preset.enabled ? `onclick="event.stopPropagation();setPreset('${escHtml(device.mac)}','${name}')"` : 'disabled'}>${active === name ? 'Profilo attivo' : 'Applica'}</button></div></article>`;
    }).join('');
    return `<section class="profile-device"><div class="profile-device-head"><div><h3>${escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac)}</h3><span class="ops-sub">Decisione: ${escHtml(state.smart_last_action || 'inattiva')} · ambiente ${finiteNumber(state.RoomTemperature)?.toFixed(1) || '--'} °C · andamento ${trendSummary}</span></div><span class="profile-badge">${active === 'manual' ? 'MANUALE' : 'PROFILO '+(PROFILE_LABELS[active] || active).toUpperCase()}</span></div><div class="profile-cards">${cards}</div></section>`;
  }).join('');
  content.innerHTML = `<div class="profiles-layout"><div>${devices}</div><aside class="profile-docs"><h3>Come decide il profilo</h3><p>Ogni profilo trasforma temperatura, umidità e andamento recente dell’ambiente in un comando. Non è una programmazione oraria: il profilo resta attivo finché non ne selezioni un altro.</p><div class="profile-callout"><b>Auto profilo non è Auto Gree.</b><br>È l’integrazione a scegliere Cool, Heat oppure Dry tra le modalità autorizzate. La modalità Gree viene comandata esplicitamente.</div><h4>Ordine delle decisioni</h4><ol><li>I limiti Min/Max hanno priorità e proteggono l’ambiente.</li><li>La soglia umidità può richiedere Dry.</li><li>Target e margine decidono arresto e riavvio.</li><li>Ventola, Quiet e I-Demand rifiniscono il comando.</li></ol><h4>I-Demand Smart</h4><p>Non seleziona mai D1 durante una richiesta di raffrescamento: D1 arresta il compressore. Usa piena capacità con domanda elevata e introduce D3/D2 avvicinandosi al target.</p><button class="config-btn" style="width:100%;margin-bottom:7px" onclick="openRoomSensorSettings()">Associa sensori interni</button><button class="config-btn" style="width:100%" onclick="openSensorSettings()">Configura sensori esterni</button></aside></div>`;
}
function profileField(id,label,description,control) {
  return `<div class="profile-editor-field"><div class="profile-editor-copy"><label for="${id}">${label}</label><p>${description}</p></div><div class="profile-editor-control">${control}</div></div>`;
}
function profileInput(id,value,type='number',extra='') {
  return `<input class="config-input" id="${id}" type="${type}" value="${value ?? ''}" ${extra}>`;
}
function profileSelect(id,value,options) {
  return `<select class="config-select" id="${id}">${options.map(([item,label]) => `<option value="${item}" ${value === item ? 'selected' : ''}>${label}</option>`).join('')}</select>`;
}
async function openProfileEditor(mac, profileName) {
  const modal = document.getElementById('profileEditorModal');
  const body = document.getElementById('profileEditorBody');
  modal.style.display = 'block';
  body.innerHTML = '<div class="config-loading">Caricamento profilo…</div>';
  try {
    const data = await apiFetch(PANEL_ROOM_SENSORS_URL);
    const device = data.devices.find(item => item.mac === mac);
    if (!device) throw new Error('Unità non trovata');
    const preset = device.presets?.[profileName] || {};
    _profileEditor = {entryId:device.entry_id,mac,profileName,preset};
    document.getElementById('profileEditorTitle').textContent = `${PROFILE_LABELS[profileName]} · ${__DEVICE_NAMES__[mac] || device.name || mac}`;
    document.getElementById('profileEditorSubtitle').textContent = 'Le modifiche riguardano esclusivamente questo profilo.';
    renderProfileEditorForm(preset);
  } catch (error) {
    body.innerHTML = `<div class="chart-empty">Impossibile caricare il profilo: ${escHtml(error.message)}</div>`;
  }
}
function renderProfileEditorForm(preset) {
  const strategy = preset.smart_mode || 'auto';
  const workCurve = preset.work_curve || 'balanced';
  const allowed = preset.allowed_modes || (strategy === 'auto' ? ['cool','heat','dry'] : [strategy]);
  const body = document.getElementById('profileEditorBody');
  body.innerHTML = `<div class="profile-editor-grid"><section class="profile-editor-section"><div class="profile-editor-section-head"><span>01</span><div><h3>Attivazione e strategia</h3><p>Qui scegli se il profilo può essere applicato e quali azioni climatiche gli sono consentite.</p></div></div>${profileField('pe-enabled','Profilo abilitato','Disattivato: il profilo scompare dalle scelte del climate, ma i valori salvati non vengono cancellati.',`<label class="profile-toggle"><input id="pe-enabled" type="checkbox" ${preset.enabled ? 'checked' : ''}><span>Disponibile</span></label>`)}${profileField('pe-smart','Regolazione Smart','Attivo: ogni due minuti e a ogni variazione dei sensori decide se avviare, arrestare o cambiare modalità. Spento: applica i parametri una volta, senza governare accensioni e arresti.',`<label class="profile-toggle"><input id="pe-smart" type="checkbox" ${preset.smart_enabled !== false ? 'checked' : ''}><span>Automazione attiva</span></label>`)}${profileField('pe-mode','Strategia climatica','Auto profilo è la logica dell’integrazione: sceglie soltanto fra Cool, Heat e Dry autorizzati qui. Solo Cool/Heat/Dry blocca il profilo su una singola modalità. Non usa la modalità Auto del controller Gree.',profileSelect('pe-mode',strategy,[['auto','Auto profilo'],['cool','Solo Cool'],['heat','Solo Heat'],['dry','Solo Dry']]).replace('id="pe-mode"','id="pe-mode" onchange="syncProfileAllowedModes()"'))}${profileField('pe-allowed','Modalità consentite','Sono i permessi di Auto profilo. Cool raffresca, Heat riscalda, Dry interviene solo con una soglia umidità configurata. Una modalità non selezionata non verrà mai comandata.',`<div class="profile-mode-selector"><label><input id="pe-allow-cool" type="checkbox" ${allowed.includes('cool') ? 'checked' : ''}>❄ Cool</label><label><input id="pe-allow-heat" type="checkbox" ${allowed.includes('heat') ? 'checked' : ''}>☀ Heat</label><label><input id="pe-allow-dry" type="checkbox" ${allowed.includes('dry') ? 'checked' : ''}>◇ Dry</label></div>`)}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>02</span><div><h3>Comfort e isteresi</h3><p>Target arresta la richiesta; il margine stabilisce quanto la stanza deve allontanarsi prima di ripartire.</p></div></div>${profileField('pe-target','Target comfort','In Cool il profilo raffresca finché l’ambiente scende al target. In Heat riscalda finché lo raggiunge dal basso. Non è necessariamente il setpoint inviato alla macchina quando è attiva la compensazione esterna.',profileInput('pe-target',preset.target_temperature ?? 26,'number','min="16" max="30" step="0.5"'))}${profileField('pe-deadband','Margine di riaccensione','È isteresi di riavvio, non tolleranza attorno al target. Esempio: Cool con target 26 °C si ferma a 26,0 °C e riparte oltre 26,5 °C con margine 0,5. Se la temperatura sta ancora evolvendo nella direzione corretta, lo storico live aggiunge fino a 0,2 °C di guardia.',profileInput('pe-deadband',preset.deadband ?? .5,'number','min="0.2" max="2" step="0.1"'))}${profileField('pe-humidity','Soglia umidità Dry','Usa la media dei sensori di umidità interni. Sopra la soglia, Auto profilo può scegliere Dry solo se Dry è autorizzato. Vuoto significa che l’umidità non influenza la modalità.',profileInput('pe-humidity',preset.humidity_threshold ?? '','number','min="20" max="90" step="1"'))}${profileField('pe-auto-off','Temperatura spegnimento opzionale','Parametro legacy conservato per compatibilità. Al momento non partecipa alle decisioni Smart: per arresto e riavvio valgono Target e Margine.',profileInput('pe-auto-off',preset.auto_off_temperature ?? '','number','min="16" max="30" step="0.5"'))}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>03</span><div><h3>Limiti e ambiente esterno</h3><p>Questi limiti hanno priorità sul comfort normale; la compensazione modifica invece il target in base al meteo.</p></div></div>${profileField('pe-min','Temperatura minima di sicurezza','Se la stanza scende sotto questo valore, il profilo richiede Heat anche se non ha ancora superato il normale margine. Funziona solo quando Heat è tra le modalità autorizzate. Vuoto = protezione disattivata.',profileInput('pe-min',preset.min_temperature ?? '','number','min="10" max="30" step="0.5"'))}${profileField('pe-max','Temperatura massima di sicurezza','Se la stanza supera questo valore, il profilo richiede Cool con priorità sul normale margine. Funziona solo quando Cool è autorizzato e deve essere maggiore della soglia minima.',profileInput('pe-max',preset.max_temperature ?? '','number','min="16" max="35" step="0.5"'))}${profileField('pe-adaptive','Compensazione esterna','Con temperatura esterna oltre 30 °C alza gradualmente il target Cool fino a +2 °C; sotto 8 °C abbassa il target Heat fino a −1,5 °C. Un sensore esterno più vecchio di tre ore viene ignorato.',`<label class="profile-toggle"><input id="pe-adaptive" type="checkbox" ${preset.outdoor_compensation !== false ? 'checked' : ''}><span>Compensa target</span></label>`)}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>04</span><div><h3>Curva di avvicinamento</h3><p>Stabilisce quanto rapidamente aumentare portata e capacità per raggiungere il target.</p></div></div>${profileField('pe-work-curve','Curva di lavoro','Graduale privilegia silenzio e consumi; Bilanciata offre una risposta normale; Rapida mantiene più capacità fino a ridosso del target.',profileSelect('pe-work-curve',workCurve,[['gentle','Graduale · comfort silenzioso'],['balanced','Bilanciata · uso quotidiano'],['rapid','Rapida · priorità al target']]))}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>05</span><div><h3>Ventola, silenzio e consumi</h3><p>Queste opzioni non decidono se climatizzare: stabiliscono come lavora l’unità dopo che il profilo ha rilevato una richiesta.</p></div></div>${profileField('pe-fan','Gestione ventola','Smart sceglie Bassa, Media-Bassa, Media, Media-Alta o Alta in base ai °C che mancano al target. Durante il mantenimento ventilato usa Bassa; Auto delega la velocità al controller Gree.',profileSelect('pe-fan',preset.fan_speed || 'Smart',[['Smart','Smart'],['Auto','Auto controller'],['Bassa','Bassa'],['Media-Bassa','Media-Bassa'],['Media','Media'],['Media-Alta','Media-Alta'],['Alta','Alta']]))}${profileField('pe-hold','Al raggiungimento del comfort','Decide cosa fare quando non serve più Cool, Heat o Dry. Spegni minimizza i consumi. Solo ventola è la scelta più chiara per movimentare aria senza compressore. Cool + D1 mantiene Cool selezionato ma inibisce il compressore tramite I-Demand; usalo solo se il controller supporta DRED e preferisci questo comportamento.',profileSelect('pe-hold',preset.hold_action || 'off',[['off','Spegni unità'],['fan_only','Solo ventola'],['d1_ventilation','Cool + D1 · compressore inibito']]))}${profileField('pe-quiet','Modalità Quiet','Invia Quiet insieme agli altri comandi del profilo. Riduce il rumore ma può limitare la capacità: evita di combinarlo con obiettivi di recupero rapido.',`<label class="profile-toggle"><input id="pe-quiet" type="checkbox" ${preset.quiet ? 'checked' : ''}><span>Quiet attivo</span></label>`)}${profileField('pe-dred','I-Demand durante una richiesta Cool','Regola la capacità quando il profilo sta davvero raffrescando. No action non invia comandi DRED; Off rimuove il limite. Smart usa piena capacità con scarto ≥1,5 °C, D3 fra 0,8 e 1,5 °C e D2 sotto 0,8 °C. La ventilazione a comfort è una decisione separata nel campo precedente.',profileSelect('pe-dred',preset.dred || 'No action',[['No action','Non inviare comandi'],['Smart','Smart capacità'],['Off','Off · piena capacità'],['D1','D1 · nessun raffrescamento'],['D2','D2 · max 50%'],['D3','D3 · max 75%']]))}</section></div>`;
  syncProfileAllowedModes();
}
function syncProfileAllowedModes() {
  const strategy = document.getElementById('pe-mode')?.value || 'auto';
  for (const mode of ['cool','heat','dry']) {
    const input = document.getElementById(`pe-allow-${mode}`);
    if (!input) continue;
    input.disabled = strategy !== 'auto';
    if (strategy !== 'auto') input.checked = mode === strategy;
  }
}
function closeProfileEditor() {
  document.getElementById('profileEditorModal').style.display = 'none';
  _profileEditor = null;
}
function optionalProfileNumber(id) {
  const value = document.getElementById(id).value.trim();
  return value === '' ? null : Number(value);
}
async function saveProfileEditor() {
  if (!_profileEditor) return;
  const strategy = document.getElementById('pe-mode').value;
  const allowedModes = ['cool','heat','dry'].filter(mode => document.getElementById(`pe-allow-${mode}`).checked);
  if (strategy === 'auto' && !allowedModes.length) { alert('Seleziona almeno una modalità per Auto profilo.'); return; }
  const minimum = optionalProfileNumber('pe-min'), maximum = optionalProfileNumber('pe-max');
  if (minimum != null && maximum != null && minimum >= maximum) { alert('La temperatura minima deve essere inferiore alla massima.'); return; }
  const profile = {
    enabled:document.getElementById('pe-enabled').checked,
    smart_enabled:document.getElementById('pe-smart').checked,
    smart_mode:strategy,
    allowed_modes:strategy === 'auto' ? allowedModes : [strategy],
    target_temperature:Number(document.getElementById('pe-target').value),
    deadband:Number(document.getElementById('pe-deadband').value),
    humidity_threshold:optionalProfileNumber('pe-humidity'),
    auto_off_temperature:optionalProfileNumber('pe-auto-off'),
    min_temperature:minimum,max_temperature:maximum,
    outdoor_compensation:document.getElementById('pe-adaptive').checked,
    fan_speed:document.getElementById('pe-fan').value,
    quiet:document.getElementById('pe-quiet').checked,
    hold_action:document.getElementById('pe-hold').value,
    work_curve:document.getElementById('pe-work-curve').value,
    dred:document.getElementById('pe-dred').value,
  };
  const button = document.getElementById('saveProfileEditor');
  const status = document.getElementById('profileEditorStatus');
  button.disabled = true; status.textContent = 'Salvataggio e ricarica integrazione…';
  try {
    await apiFetch(PANEL_PROFILE_URL,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({entry_id:_profileEditor.entryId,mac:_profileEditor.mac,profile_name:_profileEditor.profileName,profile})});
    status.textContent = 'Profilo salvato.';
    closeProfileEditor();
    setTimeout(loadData,1800);
  } catch (error) {
    status.textContent = `Errore: ${error.message}`;
  } finally { button.disabled = false; }
}
