const PROFILE_NAMES = ['day','night','away'];
const PROFILE_LABELS = {day:'Giorno',night:'Notte',away:'Assente'};
const PROFILE_MODE_LABELS = {auto:'Auto profilo',cool:'Solo Cool',heat:'Solo Heat',dry:'Solo Dry'};
let _profileEditor = null;

function profileModeChip(mode) {
  return `<span class="profile-mode-chip">${{cool:'❄ Cool',heat:'☀ Heat',dry:'◇ Dry'}[mode] || escHtml(mode)}</span>`;
}
function renderProfilesPage(data) {
  const content = document.getElementById('profilesContent');
  if (!content) return;
  const devices = data.map(device => {
    const state = device.state || {};
    const active = state.ActivePreset || 'manual';
    const presets = state.Presets || {};
    const cards = PROFILE_NAMES.map(name => {
      const preset = presets[name] || {};
      const strategy = preset.smart_mode || 'auto';
      const allowed = preset.allowed_modes || (strategy === 'auto' ? ['cool','heat','dry'] : [strategy]);
      const modes = strategy === 'auto' ? allowed : [strategy];
      const target = finiteNumber(preset.target_temperature);
      const margin = finiteNumber(preset.deadband) ?? .5;
      return `<article class="profile-card ${active === name ? 'active' : ''}" role="button" tabindex="0" onclick="openProfileEditor('${escHtml(device.mac)}','${name}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openProfileEditor('${escHtml(device.mac)}','${name}')}" aria-label="Configura profilo ${PROFILE_LABELS[name]}"><div class="profile-card-top"><h4>${PROFILE_LABELS[name]}</h4><span class="profile-badge">${active === name ? 'ATTIVO' : preset.enabled ? 'DISPONIBILE' : 'DISABILITATO'}</span></div><div class="profile-mode-path">${PROFILE_MODE_LABELS[strategy] || strategy}</div><div>${modes.map(profileModeChip).join('') || '<span class="profile-mode-chip">Nessuna modalità</span>'}</div><div class="profile-rule"><span>Target configurato</span><b>${target == null ? '--' : target.toFixed(1)+' °C'}</b></div><div class="profile-rule"><span>Target effettivo</span><b>${active === name && finiteNumber(state.smart_effective_target) != null ? Number(state.smart_effective_target).toFixed(1)+' °C' : 'quando attivo'}</b></div><div class="profile-rule"><span>Arresto / ripartenza</span><b>${target == null ? '--' : `${target.toFixed(1)}° / ${(target+margin).toFixed(1)}° Cool`}</b></div><div class="profile-rule"><span>Umidità Dry</span><b>${preset.humidity_threshold == null ? 'non configurata' : '&gt; '+Number(preset.humidity_threshold).toFixed(0)+'%'}</b></div><div class="profile-rule"><span>Ventola · Quiet</span><b>${escHtml(preset.fan_speed || 'Smart')} · ${preset.quiet ? 'sì' : 'no'}</b></div><div class="profile-rule"><span>Compensazione esterna</span><b>${preset.outdoor_compensation ? 'attiva' : 'disattiva'}</b></div><div class="profile-rule"><span>I-Demand</span><b>${escHtml(preset.dred || 'Nessuna azione')}</b></div><div class="profile-actions"><button class="btn profile-edit-btn" onclick="event.stopPropagation();openProfileEditor('${escHtml(device.mac)}','${name}')">Configura</button><button class="btn ${active === name ? 'active' : ''}" ${preset.enabled ? `onclick="event.stopPropagation();setPreset('${escHtml(device.mac)}','${name}')"` : 'disabled'}>${active === name ? 'Profilo attivo' : 'Applica'}</button></div></article>`;
    }).join('');
    return `<section class="profile-device"><div class="profile-device-head"><div><h3>${escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac)}</h3><span class="ops-sub">Decisione: ${escHtml(state.smart_last_action || 'inattiva')} · ambiente ${finiteNumber(state.RoomTemperature)?.toFixed(1) || '--'} °C</span></div><span class="profile-badge">${active === 'manual' ? 'MANUALE' : 'PROFILO '+(PROFILE_LABELS[active] || active).toUpperCase()}</span></div><div class="profile-cards">${cards}</div></section>`;
  }).join('');
  content.innerHTML = `<div class="profiles-layout"><div>${devices}</div><aside class="profile-docs"><h3>Configurazione dedicata</h3><p>Clicca una scheda Giorno, Notte o Assente per aprire tutti i parametri del singolo profilo, organizzati per funzione e spiegati direttamente accanto al controllo.</p><div class="profile-callout"><b>Auto profilo non è Auto Gree.</b><br>È l’integrazione a scegliere Cool, Heat oppure Dry tra le modalità autorizzate. La modalità Gree viene comandata esplicitamente.</div><h4>Come iniziare</h4><ol><li>Scegli il profilo da modificare.</li><li>Imposta strategia, target e margine.</li><li>Configura comfort, ventola e risparmio.</li><li>Salva: viene aggiornato solo quel profilo.</li></ol><h4>I-Demand Smart</h4><p>Non seleziona mai D1 durante una richiesta di raffrescamento: D1 arresta il compressore. Usa piena capacità con domanda elevata e introduce D3/D2 avvicinandosi al target.</p><button class="config-btn" style="width:100%" onclick="openSensorSettings()">Configura sensori ambiente</button></aside></div>`;
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
  const allowed = preset.allowed_modes || (strategy === 'auto' ? ['cool','heat','dry'] : [strategy]);
  const body = document.getElementById('profileEditorBody');
  body.innerHTML = `<div class="profile-editor-grid"><section class="profile-editor-section"><div class="profile-editor-section-head"><span>01</span><div><h3>Attivazione e strategia</h3><p>Definisce se il profilo è disponibile e quali modalità può comandare.</p></div></div>${profileField('pe-enabled','Profilo abilitato','Se disabilitato resta configurato ma non può essere applicato.',`<label class="profile-toggle"><input id="pe-enabled" type="checkbox" ${preset.enabled ? 'checked' : ''}><span>Disponibile</span></label>`)}${profileField('pe-smart','Regolazione Smart','Valuta periodicamente temperatura e umidità. Se spenta, il profilo applica il setpoint senza regolazione dinamica.',`<label class="profile-toggle"><input id="pe-smart" type="checkbox" ${preset.smart_enabled !== false ? 'checked' : ''}><span>Automazione attiva</span></label>`)}${profileField('pe-mode','Strategia climatica','Auto profilo sceglie esplicitamente fra le modalità consentite; le strategie Solo impediscono cambi di modalità.',profileSelect('pe-mode',strategy,[['auto','Auto profilo'],['cool','Solo Cool'],['heat','Solo Heat'],['dry','Solo Dry']]).replace('id="pe-mode"','id="pe-mode" onchange="syncProfileAllowedModes()"'))}${profileField('pe-allowed','Modalità consentite','Usate solo da Auto profilo. Almeno una modalità deve rimanere selezionata.',`<div class="profile-mode-selector"><label><input id="pe-allow-cool" type="checkbox" ${allowed.includes('cool') ? 'checked' : ''}>❄ Cool</label><label><input id="pe-allow-heat" type="checkbox" ${allowed.includes('heat') ? 'checked' : ''}>☀ Heat</label><label><input id="pe-allow-dry" type="checkbox" ${allowed.includes('dry') ? 'checked' : ''}>◇ Dry</label></div>`)}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>02</span><div><h3>Comfort e isteresi</h3><p>Imposta la temperatura desiderata e quando riavviare la macchina.</p></div></div>${profileField('pe-target','Target comfort','Temperatura ambiente desiderata. Cool si arresta al target; Heat fa l’opposto.',profileInput('pe-target',preset.target_temperature ?? 26,'number','min="16" max="30" step="0.5"'))}${profileField('pe-deadband','Margine di riaccensione','Evita accensioni frequenti. Con target 26 °C e margine 0,5, Cool riparte oltre 26,5 °C.',profileInput('pe-deadband',preset.deadband ?? .5,'number','min="0.2" max="2" step="0.1"'))}${profileField('pe-humidity','Soglia umidità Dry','Se Auto profilo consente Dry, sopra questa umidità viene preferita la deumidificazione. Vuoto = disattivata.',profileInput('pe-humidity',preset.humidity_threshold ?? '','number','min="20" max="90" step="1"'))}${profileField('pe-auto-off','Temperatura spegnimento opzionale','Valore conservato per compatibilità del profilo. La regolazione Smart corrente usa target e margine.',profileInput('pe-auto-off',preset.auto_off_temperature ?? '','number','min="16" max="30" step="0.5"'))}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>03</span><div><h3>Limiti e ambiente esterno</h3><p>Protegge l’ambiente e adatta il comfort alle condizioni estreme.</p></div></div>${profileField('pe-min','Temperatura minima di sicurezza','Sotto questa soglia forza Heat soltanto se Heat è consentito. Vuoto = nessun limite.',profileInput('pe-min',preset.min_temperature ?? '','number','min="10" max="30" step="0.5"'))}${profileField('pe-max','Temperatura massima di sicurezza','Sopra questa soglia forza Cool soltanto se Cool è consentito. Deve essere maggiore della minima.',profileInput('pe-max',preset.max_temperature ?? '','number','min="16" max="35" step="0.5"'))}${profileField('pe-adaptive','Compensazione esterna','Con molto caldo esterno allenta il target Cool; con freddo intenso modifica il target Heat. Richiede il sensore esterno.',`<label class="profile-toggle"><input id="pe-adaptive" type="checkbox" ${preset.outdoor_compensation !== false ? 'checked' : ''}><span>Compensa target</span></label>`)}</section><section class="profile-editor-section"><div class="profile-editor-section-head"><span>04</span><div><h3>Ventola, silenzio e consumi</h3><p>Personalizza portata, rumore e limite della domanda elettrica.</p></div></div>${profileField('pe-fan','Gestione ventola','Smart aumenta la portata con la distanza dal target; Auto delega la velocità al controller Gree.',profileSelect('pe-fan',preset.fan_speed || 'Smart',[['Smart','Smart'],['Auto','Auto controller'],['Bassa','Bassa'],['Media-Bassa','Media-Bassa'],['Media','Media'],['Media-Alta','Media-Alta'],['Alta','Alta']]))}${profileField('pe-quiet','Modalità Quiet','Riduce il rumore dell’unità. Consigliata per il profilo Notte, ma può rallentare il recupero.',`<label class="profile-toggle"><input id="pe-quiet" type="checkbox" ${preset.quiet ? 'checked' : ''}><span>Quiet attivo</span></label>`)}${profileField('pe-dred','I-Demand / DRED','Smart non usa D1 mentre deve raffrescare: piena capacità con domanda alta, poi D3/D2 vicino al target. D1 manuale arresta il compressore.',profileSelect('pe-dred',preset.dred || 'No action',[['No action','Nessuna azione'],['Smart','Smart'],['Off','Off · piena capacità'],['D1','D1 · compressore fermo'],['D2','D2 · max 50%'],['D3','D3 · max 75%']]))}</section></div>`;
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
