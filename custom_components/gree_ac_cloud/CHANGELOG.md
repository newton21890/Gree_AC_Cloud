# Changelog

## 0.16.1

### Ripristino automatico della sessione del pannello

- Il pannello attende il token Home Assistant quando l'app Companion riattiva la WebView invece di mostrare subito un errore permanente.
- In caso di HTTP 401 richiede il rinnovo del token HA e ripete automaticamente la richiesta una volta.
- Il recupero viene ritentato dopo pochi secondi e quando il pannello torna visibile o in primo piano.
- Durante un rinnovo temporaneo mantiene visibili gli ultimi dati validi, evitando di obbligare al refresh della pagina.

## 0.16.0

### Telemetria consumi e ore di funzionamento

- La colonna Telemetria mostra ora potenza ed energia effettive accanto ai rispettivi valori stimati.
- Aggiunti il contatore persistente delle ore totali di accensione e il tempo trascorso dall'ultima accensione.
- Il contatore totale può essere azzerato dal pannello per ciascuna unità senza modificare i dati energetici.
- Aggiunte entità sensore dedicate alle ore totali e alle ore della sessione corrente.

## 0.15.0

### Consumi elettrici effettivi affiancati alle stime

- Aggiunta la configurazione **Consumi** per associare a ciascuna unità un sensore HA di potenza effettiva e, facoltativamente, il relativo contatore energia.
- Le misure del contatore non sostituiscono le stime energetiche esistenti: entrambe restano disponibili e vengono mostrate come serie distinte.
- Lo storico Recorder e il grafico energetico confrontano la potenza misurata con la stima del modello.
- Le associazioni vengono applicate senza ricaricare l'integrazione e senza interrompere MQTT.

## 0.14.0

### Scheda descrittiva dell'impianto aeraulico

- Aggiunta nel pannello la sezione **Impianto**, con configurazione separata per ciascuna macchina.
- È possibile registrare pressione statica impostata in Pa, livello P30, lunghezza principale e totale delle condotte, numero di locali, mandate e riprese, diametro/sezione, tipo di condotte, terminali, ripresa, filtro e note.
- I dati sono descrittivi: non scrivono P30, non inviano comandi alla macchina e non vengono presentati come misure in tempo reale.
- La scheda viene salvata nello storage Home Assistant senza ricaricare l'integrazione o interrompere MQTT.
- Pressione statica, locali e mandate sono riepilogati nella telemetria del pannello; la scheda completa e i principali valori sono esposti anche negli attributi dell'entità climate.

## 0.13.9

### Cambio temperatura senza riavvio dell'integrazione

- Corretto il cambio del target mentre è attivo un profilo: il nuovo valore continua a essere salvato nel profilo, ma l'aggiornamento non scarica più le entità e non riavvia la connessione MQTT.
- Il comando della temperatura viene applicato immediatamente all'unità e il profilo in memoria usa subito il nuovo target.
- Gli altri aggiornamenti strutturali delle opzioni continuano a ricaricare normalmente l'integrazione quando necessario.

## 0.13.8

### Correzione comando Turbo U-Match

- Verificato dal feedback del controller che il solo comando `Tur=1` può essere accettato dal cloud senza attivare realmente Turbo sul display cablato.
- Turbo ora invia la combinazione U-Match `Tur=1` e `WdSpd=6`; disattiva inoltre `Quiet` e, quando supportato, rimuove il limite `DRED` impostandolo a `0`.
- Aggiunto `WdSpd=6` come modalità ventola Home Assistant **Turbo**. Se si seleziona un'altra velocità mentre Turbo è attivo, viene inviato anche `Tur=0`.
- Lo switch Home Assistant `Turbo Mode` e i pulsanti Turbo del pannello usano la stessa sequenza completa.
- La disattivazione di Turbo riporta la ventola ad Alta (`WdSpd=5`).

## 0.13.7

### Override manuale ventola e Turbo visibile

- La velocità ventola impostata manualmente tramite l'entità climate o il pannello viene mantenuta anche con un profilo Smart attivo, invece di essere ricalcolata e riportata automaticamente a Media/Bassa.
- L'override manuale resta valido fino al cambio di profilo; selezionando nuovamente un profilo, la ventola torna alla gestione configurata (Smart o velocità fissa).
- I pulsanti ventola del pannello ora usano il servizio Home Assistant `climate.set_fan_mode`, così l'override viene registrato correttamente dall'integrazione.
- Aggiunto un pulsante **🚀 TURBO** direttamente nella sezione principale “Ventilazione e potenza”; resta disponibile anche lo switch Home Assistant `Turbo Mode` e il comando nei controlli avanzati.

## 0.13.6

### InTem sempre disponibile e correzione sonde IDU/ODU

- `InTem` viene esposto come sensore temperatura dell'unità Gree ogni volta che il cloud restituisce un valore numerico valido, indipendentemente da `InTemEn`.
- Il sensore `InTem` può quindi essere selezionato, da solo o insieme ad altri sensori temperatura, come sorgente per il calcolo della temperatura del relativo climate.
- Corretta la decodifica visualizzata in **Sonde IDU / ODU**: sia `InTem` sia `OutTem` usano `raw − 40 °C`, non la precedente divisione per due.
- `OutTem` resta una sonda fisica ODU e non viene presentata come temperatura meteo esterna.

## 0.13.5

### Comando e visualizzazione delle sonde native

- Aggiunti `InTemEn` e `InHumiEn` sia al polling esplicito sia alla lista controllata dei comandi MQTT.
- Il pannello permette di abilitare/disabilitare separatamente le due sonde e mostra i valori nativi restituiti dal dispositivo.
- Dopo il comando viene richiesto subito un nuovo stato di `InTemEn`, `TemSen`, `InTem`, `InHumiEn` e `InHumi`, così da verificare se il controller accetta il flag e fornisce la misura.
- La temperatura nativa associata al flag `InTemEn`, cioè `InTem`, viene convertita esplicitamente con la formula richiesta `raw − 40 °C`, soltanto quando `InTemEn=1`; `TemSen` resta una colonna separata/opzionale.
- L'umidità `InHumi` viene mostrata soltanto quando `InHumiEn=1` e il valore è nell'intervallo valido.

## 0.13.4

### Flag espliciti InTemEn e InHumiEn

- La disponibilità della temperatura interna nativa viene ora subordinata esplicitamente a `InTemEn=1`; il valore associato letto resta `TemSen` con codifica `raw−40 °C`.
- La disponibilità dell'umidità interna nativa viene subordinata esplicitamente a `InHumiEn=1`; il valore associato è `InHumi` nell'intervallo valido 1–100%.
- Con `InTemEn=0` o `InHumiEn=0`, il relativo valore non viene utilizzato anche se il cloud continua a inviare le colonne `InTem`, `TemSen` o `InHumi`.
- I valori grezzi dei due flag enable vengono esposti nell'entità climate, nel pannello e negli attributi delle entità sensore per una diagnosi non ambigua.
- Sulle due unità U-Match verificate entrambi i flag sono `0`: le sonde ambiente native risultano quindi disabilitate; la regolazione usa correttamente i sensori Home Assistant associati.

## 0.13.3

### Validazione delle sonde ambiente Gree

- Verificato il significato di `TemSen`: la temperatura ambiente Gree usa l'offset `+40`, ma il valore `0` indica una sonda non supportata o disabilitata e non deve diventare `−40 °C`.
- Verificato `InHumi`: il valore `0` restituito dalle unità U-Match viene ora trattato come sonda umidità non disponibile, non come umidità reale allo 0%.
- Le entità native `TemSen` e `InHumi` risultano disponibili soltanto quando il dato cloud è valido; `InHumi` non viene più abilitato per impostazione predefinita sui dispositivi che espongono solo il valore sentinella.
- Esposti nel pannello e nell'entità climate i flag di disponibilità delle sonde Gree interne, separati dai sensori ambiente Home Assistant configurati.
- `InTem` e `OutTem` restano sonde diagnostiche grezze/non verificate: sulle unità osservate riportano valori di circuito (67–89), non la temperatura ambiente interna o meteo esterna, e non vengono usate per la regolazione dei profili.
- Confermata la corretta acquisizione delle sonde HA configurate: temperatura e umidità ambiente per ciascuna zona e riferimenti esterni comuni.

## 0.13.2

### Allineamento della modalità al protocollo Gree

- Corretta la mappatura wire-level di `Mod`: `0=Auto`, `1=Cool`, `2=Dry`, `3=Fan`, `4=Heat`.
- In precedenza i valori 2 e 4 erano invertiti: un comando Heat poteva quindi richiedere Dry e viceversa, mentre la lettura dello stato poteva mostrare la modalità errata.
- Allineato anche il modello di potenza stimata: `Mod=4` usa ora il riferimento Heat e `Mod=2` il duty-cycle Dry.
- Aggiunto un test di regressione esplicito sui cinque valori del protocollo per impedire future inversioni.
- Confermate le mappature già corrette di `Pow`, `WdSpd`, `SetDeciTem`, swing, Quiet/Turbo e dei livelli I-Demand verificati sui controller U-Match.

## 0.13.1

### Recupero automatico delle richieste termiche in stallo

- Corretto il calcolo della ventola Smart: il margine di riaccensione non viene più sottratto dalla distanza dal target mentre l'unità è già attiva. Il margine resta esclusivamente un'isteresi di riavvio.
- Con curva Rapida, una distanza di circa 0,6 °C dal target richiede ora almeno ventola Media invece di Bassa.
- Aggiunto un rilevatore di richiesta insoddisfatta: se la temperatura non procede verso il target, la capacità viene aumentata progressivamente in base alla curva selezionata.
- L'aumento anti-stallo agisce congiuntamente su ventola Smart e I-Demand Smart, rimuovendo gradualmente le limitazioni energetiche fino al recupero dell'andamento corretto.
- Esposti gli attributi diagnostici `smart_unmet_minutes` e `smart_stall_boost` per rendere verificabile l'intervento del regolatore.

## 0.13.0

### Curve di lavoro dei profili

- Aggiunta a ogni profilo la scelta della curva di avvicinamento al target: **Graduale**, **Bilanciata** o **Rapida**.
- La curva modula realmente le soglie della ventola Smart sia in raffrescamento sia in riscaldamento: Rapida anticipa le portate elevate, Graduale le introduce solo con una distanza maggiore dal target.
- In raffrescamento con I-Demand Smart, la curva controlla anche quanto a lungo mantenere piena capacità prima di passare progressivamente a D3 e D2.
- Aggiunti configurazione e validazione sia nell'editor Profili del pannello sia nelle opzioni native Home Assistant.
- I profili esistenti senza una curva salvata continuano a funzionare come prima usando automaticamente **Bilanciata**.
- Esposto l'attributo diagnostico `smart_work_curve` sull'entità climate attiva.

## 0.12.1

### Leggibilità dei valori esterni

- Evidenziate le serie di temperatura e umidità esterne con linee continue più spesse, colori più luminosi e una lieve ombra dedicata.
- Resa adattiva la scala verticale dell'umidità, così le variazioni dei sensori esterni non vengono compresse inutilmente nell'intervallo fisso 0–100%.
- Rimossa la toolbar ApexCharts con i pulsanti zoom `+`, zoom `−`, selezione, pan e ripristino; restano disponibili tooltip e lettura condivisa delle serie.

## 0.12.0

### Grafici ApexCharts

- Sostituito il renderer SVG proprietario dei grafici con ApexCharts 3.54.1, distribuito localmente con l'integrazione e senza dipendenze da CDN o dalla ApexCharts Card HACS.
- Aggiunti tooltip condivisi, zoom, selezione temporale, pan e ripristino sui grafici storici dettagliati.
- Mantenuta l'acquisizione dati tramite l'API storico dell'integrazione, alimentata dal Recorder di Home Assistant e dalle entità configurate nel pannello.
- Conservati tema scuro, aree graduate, linee target tratteggiate e layout responsive per desktop, mobile e orientamento verticale.
- Aggiunta gestione esplicita del ciclo di vita dei grafici per evitare istanze duplicate e memoria residua durante aggiornamenti e cambi pagina.

## 0.11.3

### Recupero delle sessioni frontend scadute

- Il pannello preferisce ora il token corrente esposto dall'oggetto auth di Home Assistant rispetto alle copie presenti nel localStorage dell'iframe.
- Dopo una risposta 401 il token rifiutato viene escluso dai tentativi successivi, impedendo che una vecchia sessione memorizzata continui a generare richieste non autenticate.
- Il token escluso viene rivalutato solamente al tentativo controllato successivo, così un eventuale rinnovo della sessione Home Assistant può essere acquisito senza polling aggressivo.

## 0.11.2

### Inizializzazione TLS non bloccante

- Spostata la creazione del contesto TLS MQTT e il caricamento dei certificati di sistema su un thread executor, eliminando il warning `load_default_certs` rilevato da Home Assistant durante l'avvio e le riconnessioni.
- Il medesimo contesto TLS verificato viene riutilizzato nelle riconnessioni MQTT, senza disabilitare alcun controllo dei certificati.

## 0.11.1

### Correzioni prioritarie dai log diagnostici

- Eliminati gli accessi sincroni ai file del pannello dall'event loop di Home Assistant: README, changelog, manifest e moduli frontend vengono ora caricati tramite executor durante la registrazione.
- Le variazioni di accensione provenienti da comando a parete o da altri client restano tracciate come override manuali, ma non vengono più segnalate come warning perché costituiscono un evento operativo previsto.
- Durante un override manuale i diagnostici Smart mostrano ora ventola e I-Demand effettivamente osservati, evitando falsi mismatch causati da una precedente decisione automatica rimasta in memoria.
- Il pannello interrompe il polling ogni 10 secondi dopo una risposta 401, mostra un messaggio esplicito e tenta nuovamente la sessione dopo 60 secondi; questo riduce drasticamente i tentativi non autenticati ripetuti.

## 0.11.0

### Grafici energetici e indicatori di risparmio

- Aggiunti alla pagina Grafici i trend della potenza elettrica stimata e dell'energia cumulata nel periodo selezionato.
- Introdotto un riferimento controfattuale che confronta lo stesso modo HVAC senza DRED e Quiet, mostrando risparmio istantaneo, kWh evitati e percentuale stimata.
- Aggiunti indicatori per consumo del periodo, potenza media, picco, riferimento comparabile e risparmio stimato.
- Registrati in Home Assistant i nuovi sensori Estimated Baseline Power ed Estimated Saving Power, entrambi esplicitamente marcati come stime e non come contatori fiscali.
- Lo storico Recorder include ora potenza, energia, preset attivo, strategia Smart e livello DRED, consentendo di attribuire i risparmi stimati ai profili Giorno, Notte e Assente.
- Aggiunta una lettura preventiva del potenziale dei preset in base a I-Demand, Quiet e comportamento a comfort, distinguendola chiaramente dai risparmi realmente stimati sullo storico.
- Tutte le metriche energetiche restano stime basate sul modello selezionato dell'unità e non sono adatte a fatturazione o verifica fiscale.

## 0.10.1

### Selezione dei sensori interni

- Sostituite le liste native a selezione multipla con schede dotate di checkbox indipendenti, evitando che la selezione di un sensore di umidità deselezioni quello di temperatura.
- Separati visivamente e logicamente i gruppi Temperatura ambiente e Umidità ambiente per ogni macchina.
- Aggiunti stato corrente, entity ID, conteggio delle selezioni e comando Deseleziona tutti per ciascun gruppo.
- Migliorata la disposizione responsive delle liste su desktop e dispositivi mobili.

## 0.10.0

### Sensori interni e mantenimento del comfort configurabile

- Ripristinato nel pannello un editor dedicato per associare a ogni macchina uno o più sensori interni di temperatura e umidità.
- Il salvataggio delle associazioni interne conserva profili e sensori esterni; la regolazione usa la media delle sole entità disponibili.
- Separati chiaramente i pulsanti Sensori interni e Sensori esterni nella pagina Profili.
- Aggiunta a ogni profilo l'azione da eseguire quando non esiste più richiesta termica: spegnere l'unità, passare a Solo ventola oppure mantenere Cool con D1 e compressore inibito.
- Solo ventola è l'opzione consigliata per movimentare aria in modo semanticamente chiaro; Cool + D1 è disponibile per gli impianti DRED che preferiscono mantenere la modalità Cool.
- La strategia I-Demand del raffrescamento resta separata dall'azione di mantenimento: D2/D3/Off modulano una richiesta Cool reale, mentre D1 può ora essere scelto esplicitamente a comfort.
- L'azione di mantenimento rispetta la velocità ventola del profilo; con ventola Smart usa la velocità Bassa.

## 0.9.0

### Profili spiegati, configurazione esterna semplificata e andamento Smart

- Riscritte le spiegazioni dei profili indicando priorità, condizioni reali di intervento, differenza fra target e isteresi, dipendenze fra modalità e soglie ed effetti dei comandi Gree.
- Documentati esplicitamente algoritmo I-Demand Smart, compensazione esterna, ventola Smart, Quiet e parametro legacy di spegnimento.
- Il modale Configurazione impianto è diventato Sensori esterni e contiene soltanto temperatura e umidità esterne comuni.
- Il modale non può più sovrascrivere sensori interni o profili; questi restano gestiti dalle opzioni Home Assistant e dall'editor dedicato.
- Aggiunto al controllo Smart uno storico live mobile di due ore con calcolo della tendenza termica sull'ultima ora.
- La tendenza viene usata in modo prudente: mentre l'unità è spenta e la temperatura continua a muoversi verso il target, il margine di riavvio aumenta temporaneamente fino a 0,2 °C per evitare cicli inutili.
- Esposti negli attributi climate tendenza in °C/h e numero di campioni, mostrati anche nella pagina Profili.
- Home Assistant Recorder resta lo storico persistente per analisi e grafici; non viene interrogato nel ciclo di controllo, evitando di bloccare le decisioni ogni due minuti.

## 0.8.3

### Correzione livello del menu mobile

- Corretto il contesto di sovrapposizione dell'intestazione mobile: pulsante hamburger e pannello laterale ora rimangono sopra lo sfondo oscurato e sfocato.
- Riservato nel drawer lo spazio superiore del pulsante, evitando sovrapposizioni con la prima voce di navigazione.

## 0.8.2

### Menu mobile e grafici reali nel Controllo

- Sostituita la barra mobile orizzontale con un menu hamburger laterale, coerente con la navigazione di Home Assistant.
- Il menu si apre sopra il contenuto con sfondo oscurato e si chiude selezionando una voce, toccando all'esterno oppure premendo Esc.
- I grafici nel tab Controllo non usano più soltanto i campioni raccolti dall'apertura della pagina: ora leggono lo storico persistente reale di Home Assistant Recorder.
- Aggiunte ai grafici del Controllo scale verticali, griglia, asse temporale, marcatori interattivi, tooltip e legenda completa.
- Il grafico compatto viene aggiornato automaticamente appena termina il caricamento dello storico Recorder.
- In assenza temporanea dello storico viene indicato chiaramente che sono mostrati dati live oppure che il caricamento è in corso.

## 0.8.1

### Correzioni dell'interfaccia mobile

- La navigazione superiore su smartphone è ora realmente scorrevole in orizzontale, con inerzia touch, indicatore di scorrimento e centratura automatica della voce selezionata.
- L'intestazione mobile non sovrappone più i controlli alla barra di navigazione.
- Sostituito il carattere Unicode di accensione con un'icona SVG stabile, evitando che venga visualizzato come una X su alcuni dispositivi e font.
- I grafici in verticale sono sensibilmente più alti e sfruttano l'altezza disponibile dello schermo.
- Aumentati spessore e leggibilità di linee, assi e punti sui display stretti.
- Ridotto l'affollamento dei marcatori negli storici lunghi e aggiunte aree touch trasparenti più grandi per selezionare facilmente ogni punto rappresentativo.
- Il tooltip dei grafici risponde ora anche al tocco tramite Pointer Events.

## 0.8.0

### Configurazione dedicata dei profili e correzione I-Demand Smart

- Le schede Giorno, Notte e Assente nella pagina Profili sono ora cliccabili e aprono un editor dedicato al singolo profilo.
- I parametri sono organizzati in quattro sezioni: strategia, comfort, limiti ambientali e gestione di ventola/rumore/consumi.
- Ogni controllo include una spiegazione operativa su effetti, priorità e interazioni con gli altri parametri.
- Il salvataggio aggiorna esclusivamente il profilo aperto, senza sovrascrivere sensori o gli altri profili della stessa unità.
- Aggiunta validazione server-side di modalità, target, isteresi, soglie, ventola e I-Demand.
- Corretta la strategia I-Demand Smart: D1 non viene più richiesto durante il raffrescamento, perché arresta il compressore.
- Con richiesta Cool elevata Smart usa piena capacità; avvicinandosi al target introduce D3 e quindi D2. D1 resta disponibile solo come scelta manuale esplicita.

## 0.7.0

### Storico persistente e navigazione temporale

- I grafici dettagliati leggono ora i dati persistenti già conservati da Home Assistant Recorder, invece di limitarsi alla sessione corrente del pannello.
- Aggiunti intervalli selezionabili di 6 ore, 24 ore, 3 giorni, 7 giorni e 30 giorni.
- Aggiunta la navigazione temporale avanti/indietro e il ritorno rapido al periodo corrente.
- Temperatura ambiente, target del climate, temperatura esterna e umidità vengono ricostruiti dalle entità configurate e aggregati quando sono presenti più sensori ambiente.
- I payload storici vengono campionati fino a un massimo di 720 punti per mantenere fluido il pannello.
- Separata la logica Recorder in `panel_history.py` e la UI dei grafici in `frontend/panel_history.js`, migliorando modularità e manutenzione.

## 0.6.0

### Pagina Profili e modalità automatiche configurabili

- Aggiunta una pagina laterale dedicata ai profili con stato per unità, schede Giorno/Notte/Assente, regole correnti e comandi di attivazione.
- Inclusa documentazione operativa completa su target, margine di riaccensione, priorità manuale, sensori, ventola, Quiet, compensazione esterna e I-Demand.
- Introdotta la strategia `Auto profilo`: l’integrazione può scegliere dinamicamente Cool, Heat o Dry in base a temperatura, umidità, target e soglie.
- Ogni profilo può autorizzare separatamente Cool, Heat e Dry; una modalità non selezionata non viene comandata neppure dalle soglie Min/Max.
- Le strategie Solo Cool, Solo Heat e Solo Dry restano disponibili per un comportamento stagionale fisso e prevedibile.

## 0.5.6

### Target profilo e falsi override manuali

- I pulsanti temperatura del pannello usano ora il servizio `climate.set_temperature`, invece di scrivere direttamente il solo registro `SetDeciTem`.
- Cambiando temperatura con un profilo attivo viene aggiornato anche il target persistente del profilo; il nuovo setpoint effettivo viene inviato alla macchina e resta quindi visibile sul comando a muro.
- Rafforzato il riconoscimento degli spegnimenti manuali: gli echi MQTT ritardati entro 45 secondi da un comando dell’integrazione non vengono più scambiati per interventi esterni.
- Aggiunti log espliciti per distinguere eco ritardato, vero cambio di alimentazione esterno e aggiornamento del target profilo.

## 0.5.5

### Isteresi corretta e sensore umidità esterna

- Corretto il controllo Smart: in raffrescamento l’unità resta accesa fino al raggiungimento del target e si riaccende solo sopra `target + margine`; in riscaldamento applica la logica inversa.
- Rinominata l’isteresi in “Margine riaccensione” per rendere esplicito il suo significato operativo.
- Resa sempre disponibile la selezione del sensore Home Assistant di umidità esterna, sia nelle opzioni dell’integrazione sia nel pannello.
- Preparato un valore target affidabile per i grafici usando anche la temperatura target dell’entità climate.

## 0.5.4

### Grafici leggibili e interattivi

- I grafici della pagina dedicata sono ora molto più grandi e possono essere espansi quasi a schermo intero.
- Aggiunte scala temperatura a sinistra, scala umidità a destra e asse temporale con ora iniziale, centrale e finale.
- Disegnati i punti di campionamento su tutte le serie; toccando o passando su un punto vengono mostrati valore, unità, data e ora.
- Il grafico compatto resta disponibile nella pagina Controllo senza sovraccaricare la scheda.

## 0.5.3

### Verifica reale I-Demand

- Aggiunta la verifica continua fra livello I-Demand richiesto dal profilo Smart e valore DRED realmente restituito dalla macchina.
- Il pannello mostra ora `richiesto · applicato` con conferma ✓ o avviso ⚠, evitando di confondere una decisione software con l’attuazione hardware.
- Chiarito il significato dei livelli: D1 esclude il compressore, D2 limita al 50% e D3 limita al 75%.
- Verificato sulla macchina zona notte il passaggio reale D2 → D3: il controller ha confermato `DRED=3` e l’entità I-Demand riporta D3.

## 0.5.2

### Correzione override, profili rapidi e pagina Grafici

- Rimosso il falso override Off assegnato automaticamente a ogni unità spenta durante il riavvio.
- Un override viene ora ripristinato solo se deriva davvero da un comando manuale esplicito; selezionare nuovamente un profilo lo azzera immediatamente.
- Distinti gli echi MQTT dei comandi Smart dalle variazioni provenienti dal controller o da altri client.
- Cambio profilo accelerato raggruppando accensione, modalità, target, ventola, Quiet e I-Demand in un unico comando cloud.
- Corrette le etichette localizzate `Smart (profilo)` e `Invariato` che potevano impedire l’applicazione effettiva di ventola e I-Demand.
- Aggiunta nella navigazione la pagina dedicata **Grafici**, con una scheda dettagliata per ogni unità; il grafico compatto resta anche nella pagina Controllo.
- Il pulsante del profilo mostra subito lo stato di applicazione e aggiorna la UI senza l’attesa fissa precedente.

## 0.5.1

### Protezione dello spegnimento al riavvio

- Se Home Assistant ripristina un profilo automatico mentre l’unità è spenta, lo stato viene interpretato come override manuale Off e il regolatore non può riaccenderla.
- Per riabilitare l’automazione è necessario selezionare esplicitamente un profilo, evitando riaccensioni inattese dopo aggiornamenti o riavvii.

## 0.5.0

### Profili disattivabili, override visivi, grafici e I-Demand Smart

- Aggiunto il profilo Manuale e un interruttore generale per disattivare completamente il regolatore automatico per ogni unità.
- Lo spegnimento o l’accensione manuale sono evidenziati direttamente nella scheda dell’unità e restano prioritari.
- Aggiunti grafici live che mettono in relazione temperatura interna, target, temperatura esterna, umidità interna ed esterna.
- Aggiunta la selezione del sensore di umidità esterna.
- I-Demand Smart varia dinamicamente fra Off, D1, D2 e D3 in base alla domanda termica e all’umidità, usando D3 in mantenimento e rimuovendo il limite quando serve massima resa.
- I valori Smart correnti di ventola, I-Demand e decisione del regolatore sono esposti nella UI e negli attributi climate.

## 0.4.1

### Override manuale dal controller a parete

- Estesa la priorità manuale anche alle variazioni On/Off ricevute dal controller cablato, dall’app Gree o da altri client, non solo ai comandi Home Assistant.
- Le variazioni originate internamente dal regolatore Smart vengono distinte dai comandi esterni per evitare falsi override.

## 0.4.0

### Ventilazione Smart e priorità manuale

- Aggiunta ai profili la gestione ventilatore **Smart**: la velocità passa progressivamente da Bassa ad Alta in base alla distanza della temperatura ambiente dal target.
- L’opzione **Auto** resta disponibile e continua a delegare integralmente la ventilazione al controller Gree; sono disponibili anche tutte le velocità fisse.
- In deumidificazione Smart viene usata la velocità Bassa per favorire la rimozione dell’umidità e limitare rumore e raffreddamento eccessivo.
- I comandi manuali di accensione e spegnimento hanno sempre priorità: il profilo resta selezionato e monitora l’ambiente, ma non annulla immediatamente la scelta dell’utente.
- L’override manuale viene riarmato solo dopo il rientro/uscita dalla banda di comfort oppure riselezionando il profilo.
- Il pannello usa ora i servizi climate per On/Off, così anche i comandi impartiti dall’interfaccia vengono riconosciuti come scelta manuale.
- Aggiunti agli attributi diagnostici la velocità Smart scelta e lo stato dell’override manuale.

## 0.3.2

### Accesso al pannello da smartphone

- Corretta l’autenticazione delle API del pannello negli iframe della Companion App e nei browser mobili con storage separato.
- Il pannello cerca ora la sessione sia nel proprio contesto sia nel frontend Home Assistant e include le credenziali same-origin nelle richieste.
- Un problema di sessione o rete viene mostrato esplicitamente e non viene più confuso con l’assenza di dispositivi configurati.

## 0.3.1

### Attivazione iniziale dei profili Smart

- Se dopo l’aggiornamento non esiste ancora un profilo precedentemente memorizzato, viene attivato automaticamente Giorno quando è abilitato.
- La regolazione Smart inizia quindi subito dopo il riavvio senza richiedere il primo clic manuale sul profilo.

## 0.3.0

### Profili climatici Smart

- I profili Giorno, Notte e Assente diventano regolatori attivi: leggono continuamente le medie dei sensori ambiente e decidono se raffrescare, riscaldare, deumidificare o spegnere.
- Aggiunte strategia Auto/Freddo/Caldo/Deumidifica, isteresi configurabile e intervallo minimo di 3 minuti tra i comandi per evitare cicli rapidi.
- Le soglie minima e massima diventano limiti di sicurezza: sotto la minima viene richiesto calore, sopra la massima raffrescamento.
- La soglia umidità attiva Dry quando necessario invece di spegnere erroneamente l’unità.
- La compensazione esterna modifica dolcemente il target durante caldo o freddo estremi, riducendo shock termico e domanda del compressore.
- Ogni profilo può configurare ventola e modalità silenziosa; il profilo Notte propone Quiet come impostazione predefinita.
- Il profilo attivo viene ripristinato dopo il riavvio di Home Assistant e rivalutato ogni due minuti e a ogni variazione dei sensori.
- Esposti sull’entità climate target smart effettivo e ultima decisione del regolatore per consentire diagnosi e automazioni.
- Corretta anche la leggibilità delle opzioni native nelle select su sfondo scuro.

## 0.2.21

### Correzioni pannello e nuova configurazione

- Corretti i nomi `undefined` nella selezione dei modelli: il pannello usa ora il campo `name` realmente presente nel catalogo.
- Centrata geometricamente l’icona del marchio nel quadrato Gree Control.
- Lo stato `2 devices online` non appare più come testo principale evidenziato: `Gree Cloud` è la voce primaria e lo stato è un dettaglio secondario.
- Riprogettata completamente la configurazione nello stesso design Operations del pannello, con intestazione, sensore esterno, schede per unità e tabella profili.
- Aggiunto un unico comando `Salva configurazione` per applicare in sequenza sensori e profili di tutte le unità.
- Migliorata la configurazione responsive, che diventa a schermo intero sugli smartphone.

## 0.2.20

### Posizionamento fisso dell’intestazione laterale

- Corretto un conflitto con le precedenti regole desktop che trasformava la barra laterale in una riga centrata verticalmente.
- `Gree Control` resta ora fissato nella parte alta della colonna, mentre stato cloud e azioni rimangono ancorati in basso.
- Applicate regole esplicite di larghezza, altezza, margini, direzione e allineamento anche quando la classe viewport `desktop` è attiva.

## 0.2.19

### Coerenza grafica della barra laterale

- Sostituiti i caratteri Unicode della navigazione con un set SVG coerente per dimensione, tratto e allineamento.
- Uniformati spaziature, peso del testo, stato attivo e area cliccabile delle quattro voci principali.
- Ridisegnata l’area inferiore con stato cloud, intervallo dati e due azioni visivamente coerenti.
- Migliorati la modalità compatta per tablet e il passaggio alla navigazione orizzontale su smartphone.
- Aggiunti stati focus visibili e un indicatore connessione che segue lo stato reale delle unità.

## 0.2.18

### Nuova interfaccia Gree Control

- Applicata al pannello reale la direzione grafica `Operations` scelta dal mockup Gree Control.
- Aggiunta una barra laterale compatta con navigazione Controllo, Manuale, Diagnostica e Sistema.
- Inserito un riepilogo impianto con unità online, temperatura e umidità medie, potenza stimata e temperatura esterna.
- Ogni unità usa ora un layout operativo a tre aree: condizioni ambiente, controlli principali e telemetria.
- Accensione, target, modalità e profili rimangono immediatamente accessibili; ventilatore, oscillazione, funzioni e I-Demand sono raccolti nei controlli avanzati.
- Migliorato il comportamento responsive per tablet e smartphone senza rimuovere alcuna funzione esistente.

## 0.2.17

### Nuovo pannello di controllo semplificato

- Riorganizzata ogni unità in sezioni leggibili: riepilogo ambiente, accensione/modalità, profili, comfort, I-Demand e dettagli tecnici richiudibili.
- Il riepilogo mostra subito temperatura e umidità medie HA, target, stato, temperatura esterna e profilo attivo.
- Le sonde grezze e la diagnostica non occupano più la schermata principale e sono raccolte in `Dettagli tecnici`.
- I profili abilitati compaiono come pulsanti Giorno, Notte e Assente direttamente nella scheda del condizionatore.
- Il pulsante impostazioni è ora esplicito: `⚙ Configura`, e apre sensori e profili.
- La navigazione principale è stata ridotta a Controllo, Manuale, Diagnostica e Sistema.

## 0.2.16

### Valori medi nel pannello e configurazione profili

- Il pannello ora mostra in `Media aria interna HA` la media reale dei sensori temperatura selezionati per ciascuna unità e usa la media dei sensori umidità selezionati.
- Il tooltip indica quanti sensori HA contribuiscono alla media; i valori non disponibili vengono esclusi.
- Nella finestra ⚙ Sensori ambiente sono ora configurabili anche i profili Giorno, Notte e Assente.
- Ogni profilo espone abilitazione, target, spegnimento automatico, limiti min/max, soglia umidità e I-Demand.
- Il salvataggio unico aggiorna sensori e profili e ricarica automaticamente l'integrazione.

## 0.2.15

### Selezione sensori nel pannello e medie ambiente

- Aggiunto un pulsante ⚙ direttamente nel pannello Gree AC Cloud per associare i sensori senza dipendere dalla voce Configura di Home Assistant.
- È possibile scegliere un unico sensore di temperatura esterna comune a tutto l'impianto.
- Per ogni condizionatore è possibile selezionare più sensori interni di temperatura e più sensori di umidità.
- L'integrazione calcola separatamente la media aritmetica dei soli valori validi/disponibili per temperatura e umidità; se nessun sensore temperatura interno è valido resta il fallback `TemSen`.
- Le opzioni native dell'integrazione supportano le stesse selezioni multiple.

## 0.2.14

### Sensori ambiente HA e preset climatici

- Le opzioni dell'integrazione permettono di associare a ogni condizionatore un sensore di temperatura e uno di umidità già presenti in Home Assistant.
- La temperatura esterna scelta sostituisce `TemSen` come `current_temperature` dell'entità climate; `TemSen` resta il fallback. L'umidità scelta viene esposta come `current_humidity`.
- Aggiunti preset configurabili Giorno, Notte e Assente, ciascuno abilitabile separatamente.
- Ogni preset può definire temperatura target, temperatura di spegnimento automatico, soglie minima/massima ambiente, soglia umidità e livello I-Demand.
- I preset abilitati compaiono direttamente nell'entità climate e reagiscono agli aggiornamenti in tempo reale dei sensori HA associati.
- Le soglie sono opzionali: se lasciate vuote non producono alcuna azione automatica.

## 0.2.13

### Preferenze per l'accensione successiva

- Aggiunto il selettore HA persistente `I-Demand all'accensione` con `No action`, `Off`, `D1`, `D2` e `D3` per ogni unità compatibile.
- La preferenza viene applicata a ogni nuova accensione in modalità Cool, sia quando parte da Home Assistant sia quando parte dal monitor/comando a muro.
- `No action` conserva il comportamento del dispositivo; `Off` azzera esplicitamente DRED, mentre D1/D2/D3 applicano il relativo limite.
- La preferenza resta memorizzata dopo riavvii e aggiornamenti di Home Assistant ed è disponibile anche a condizionatore spento.
- Aggiunti gli stessi controlli al pannello personalizzato dell'integrazione.

## 0.2.12

### Controlli I-Demand in Home Assistant

- Il selettore `Livello I-Demand / DRED` è ora abilitato per impostazione predefinita e compare nei controlli del dispositivo in Home Assistant.
- Le entità create dalle versioni precedenti come disabilitate dall'integrazione vengono abilitate automaticamente durante l'aggiornamento; le scelte di disabilitazione effettuate manualmente dall'utente vengono rispettate.
- Il controllo può essere usato dalla UI, dalle dashboard, dalle automazioni e tramite il servizio `select.select_option` con `Off`, `D1`, `D2` o `D3`.
- Rimangono applicate le condizioni verificate del dispositivo: il controllo è disponibile quando l'unità supporta DRED, è accesa ed è in modalità raffrescamento.

## 0.2.11

### Allineamento I-Demand nel pannello

- Il backend espone ora al pannello il livello DRED effettivo già normalizzato, incluso il firmware della zona giorno che riporta D1 come `DRED=0, Idemand=1`.
- Il pannello evidenzia D1 e mostra esplicitamente `Stato effettivo: D1 attivo (I-Demand)` in questo caso.
- La pagina del pannello usa intestazioni `no-cache` e un URL legato alla versione per impedire che Home Assistant conservi il vecchio JavaScript dopo un aggiornamento.
- La normalizzazione accetta valori numerici e stringhe restituiti dai diversi firmware.

## 0.2.10

### DRED, logs, temperatures and estimates

- Normalized the two verified D1 representations (`DRED=1` and `Idemand=1,DRED=0`); the panel shows the separate I-Demand flag and highlights D1 correctly.
- Added D1/D2/D3 descriptions and DRED-aware estimated power.
- Restored the panel's live log capture.
- Reclassified unidentified `InTem`/`OutTem` values as raw diagnostic probes instead of room/outdoor ambient temperatures.
- Climate current temperature now uses only documented `TemSen`; the panel shows unavailable when it is absent and keeps raw probes separate.
- The panel uses persistent backend estimated power/energy and labels both explicitly as estimates rather than relying on a browser-only counter.

## 0.2.9

### U-Match verification

- Added a disabled-by-default `I-Demand / DRED Level` select with Off, D1, D2 and D3.
- Confirmed on both XE7A wired controllers that all levels are available. Firmware can report D1 as either `DRED=1` or `Idemand=1,DRED=0`; both forms are now normalized to D1.
- Added descriptions: D1 disables the compressor, D2 caps demand at 50%, and D3 caps demand at 75%. These are ceilings, not power measurements.
- Confirmed that selecting a DRED level cancels Quiet and that the control is available only while the unit is on in Cool mode.
- Restored panel log capture after reload by setting the component logger level and avoiding duplicate in-memory handlers.
- Renamed `InTem`/`OutTem` as unverified raw IDU/ODU probes; they are no longer presented as actual room/outdoor ambient temperatures.
- Climate current temperature now uses only documented `TemSen` (`raw - 40 °C`) and remains unavailable when the device does not provide it.
- Reworked estimated power to avoid using unidentified temperature probes and to account for DRED demand ceilings. Energy entities are explicitly marked as estimates, not meters.

## 0.2.8

### Security

- All panel data and command APIs now require Home Assistant authentication; mutating endpoints require an administrator.
- Device keys are redacted in the Info tab and API responses.
- Added command, MAC, model, and device-name validation and hardened dynamic panel rendering.
- Enabled certificate verification for the Cloud API and MQTT broker.
- Kept dependency ranges compatible with Home Assistant's Python environment.

### U-Match documentation

- Analysed the supplied XE7A-24/HC and U-Match 6 manuals and added an U-Match feature matrix to the custom panel.
- Corrected `Blo` to X-Fan/coil drying and `Air` to optional fresh-air control.
- Added documented external-static-pressure P30 tables as read-only installer reference.
- Corrected nominal energy-estimation data for GUD35, GUD50 and GUD85.
- Added read-only diagnostics for error code/type, refrigerant warnings, system status, Auto Clean status and filter counters when reported by the device.
- The Devices panel now displays available U-Match diagnostic values without enabling unverified write commands.

### Fixed

- Fixed the coordinator forward annotation that could prevent the integration from importing.
- Fixed invalid `await` calls on `async_set_updated_data()`.
- Added MQTT reconnect with exponential backoff and accurate connection status.
- Command publishing no longer counts as a real device response for staleness tracking.
- Staleness timeout now follows the configurable poll interval and no longer forces an unconfirmed OFF state.
- Energy integration uses a monotonic session clock and no longer counts Home Assistant downtime.
- Panel registration and coordinator data now support multiple config entries safely.
- Device discovery now includes all homes in the Gree account.

## 0.2.7 (2026-07-29)

### Fixed

- **send_command updates staleness timer** — `send_command()` now updates `_last_seen` on successful publish, preventing the coordinator staleness check from immediately reverting `Pow=0` after a turn-on command or extra parameter toggle.
- **Staleness only resets Pow if previously ON** — The coordinator now only sets `Pow=0` on stale data when the device was previously ON (`Pow=1`). If already OFF, stale data is left untouched.
- **Cipher reset on key update** — `GreeDevice._cipher` is reset when the device key is updated via re-authentication, ensuring new keys are used immediately.

### Added

- **Re-authenticate & Update Keys** — New button in the 🔧 Info tab that re-fetches device keys from the Gree Cloud API, updates running devices, and shows old → new key changes in a table.

## 0.2.6 (2026-07-29)

### Fixed

- **Assume OFF on stale data** — When a device stops responding to MQTT polls (common when off), the coordinator no longer raises `UpdateFailed` (entities become `unavailable`). Instead it sets `Pow=0` and returns data normally, so HA shows the device as OFF. When the device responds again, the real state is restored.
- **Panel footer** — Version and cloud server host now display dynamically from manifest.json and config entry instead of being hardcoded.

## 0.2.4 (2026-07-29)

### Added

- **Auto version in panel** — Footer version number now reads from `manifest.json` dynamically instead of being hardcoded. Bump the version in one place and the panel reflects it automatically.

## 0.2.3 (2026-07-29)

### Fixed

- **Stale device state** — Devices that stop responding to MQTT polls (e.g. WiFi module idle when off) now correctly become `unavailable` instead of showing the last cached state forever. `coordinator.py` checks `seconds_since_last_seen()` against `STALE_AFTER_SECONDS` (60s) and raises `UpdateFailed` if exceeded.

### Added

- **gree_mqtt.py** — Tracks `_last_seen[mac]` timestamp on every real MQTT response; exposes `seconds_since_last_seen(mac)` for staleness checks.
- **const.py** — `STALE_AFTER_SECONDS = UPDATE_INTERVAL * 4` (60s).

## 0.2.2 (2026-07-29)

### Added

- **hacs.json** — Added HACS configuration file for automatic update notifications via HACS.

### Fixed

- **manifest.json documentation URL** — Fixed to point to correct GitHub repo `newton21890/Gree_AC_Cloud`.

## 0.2.1 (2026-07-29)

### Added

- **LCD icon reference in Wiki** — Enhanced HA Entities table with LCD display icon mappings (Table 3.1 from XE7A-24/HC manual). New "ICONE Display" section documenting all 33 LCD symbols with HA entity correlations.
- **TemSen sensor** — Added `TemSen` (local controller temperature sensor) as a sensor entity. Confirmed not available via cloud API (always `None`).

### Changed

- **Wiki parameter tables** — Added descriptions, practical examples, and range info for all C00-C23 monitor codes and P01-P87 settings parameters.

## 0.2.0 (2026-07-18)

### Changed

- **MQTT driver rewritten with aiomqtt** — Replaced paho-mqtt (threaded) with aiomqtt (async). Eliminates paho v2 auto-reconnect bugs in threaded HA environments. Connection is now fully async and integrates natively with the HA event loop.
- **Fire-and-forget polling** — `poll_device_sync()` removed. Poll requests are fire-and-forget; responses arrive via the async listener. No more blocking sleep-loops, `_data_seq`, or response queues.
- **Async MQTT callbacks** — `_on_data` is now called from the event loop directly. Removed all `asyncio.run_coroutine_threadsafe` and `async_add_executor_job` wrappers for MQTT operations.
- **Panel Info tab** — New "🔧 Info" tab showing device keys, MACs, MQTT topics, firmware versions, and a "Re-discover from Cloud" button to re-fetch device info from the Gree API.

### Fixed

- **Wrong device key in docs** — Corrected CLAUDE.md: device `REDACTED_DEVICE_IDENTIFIER` uses key `REDACTED_DEVICE_KEY` (not `REDACTED_DEVICE_KEY`).

## 0.1.0 (2026-07-10)

- Initial release: cloud API authentication, MQTT polling, HA entities (climate, sensors, switches, binary sensors), energy estimation, panel UI.
