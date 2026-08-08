# Analisi funzionale Gree U-Match per `gree_ac_cloud`

## Scopo

Questa analisi confronta i manuali presenti in `doc/` con le funzionalità attuali
dell'integrazione e con le proprietà MQTT realmente osservate su un impianto U-Match.

> I manuali descrivono il comportamento dell'unità e del comando a filo, ma non la
> codifica MQTT. Una funzione documentata può essere comandata da Home Assistant solo
> dopo averne confermato proprietà, valori accettati e risposta del dispositivo.

## Documenti analizzati

- `manual for wired controller XE7A-24H_rev01.pdf`
- `manual for wired controller XE7A-24H_rev02.pdf`
- `U-MATCH_6_SERVICE MANUAL_2024.pdf`
- `U-Match_canalizzabili_hsp_scheda_prodotto_2026.pdf`
- `UNITA_ESTERNE-completo_scheda_prodotto.pdf`
- `Ducted_inst_manual_it_ok_rev04.pdf`
- `Installation & user manual new Umatch all models.pdf`

Le fonti più utili per l'integrazione sono:

- manuale XE7A, PDF pp. 39-58: funzioni utente;
- manuale XE7A, PDF pp. 29-38: parametri installatore;
- manuale XE7A, PDF pp. 68-78: errori e stati U-Match;
- service manual U-Match 6, pp. 17-25: filtro, dry 12 °C, sensori e pressione statica;
- catalogo U-Match, pp. 14-17: Quiet, Sleep, I-Feel, I-Demand, Absence, X-Fan;
- schede tecniche dei modelli: assorbimenti, portate e pressione statica.

## Funzioni già presenti, ma da correggere

| Proprietà | Implementazione attuale | Interpretazione documentale | Intervento |
|---|---|---|---|
| `Air` | Air Direction | Funzione Air/ricambio aria; lo swing usa già `SwUpDn`/`SwingLfRig` | Rinominare in Fresh Air/Ricambio aria |
| `Blo` | Blow | X-Fan: asciugatura evaporatore dopo cool/dry | Rinominare in X-Fan/Asciugatura batteria |
| `SvSt` | Energy Saving | Risparmio energetico con limiti di setpoint | Tenerlo separato da I-Demand |
| `SlpMod` | switch Sleep | Probabile modalità Sleep enumerata; esistono più modalità | Trasformare in select dopo verifica valori |
| `SwhSlp` | solo polling | Probabile abilitazione Sleep | Verificare relazione con `SlpMod` |
| `Filter` | binary sensor | Non osservato; il protocollo espone campi `Clean*`/`FCl*` | Sostituire con diagnostica reale |
| `Err` | binary sensor | Non osservato; il protocollo espone `Errcode` e `ErrType` | Sostituire con sensore codici errore |
| ventola | Auto + 5 livelli | U-Match documenta Auto + 4 velocità | Rendere le velocità dipendenti dal modello/capability |

## Funzioni U-Match candidate

### Alta priorità

1. **I-Demand / DRED**
   - Manuale: funzione di risparmio disponibile solo in raffreddamento; documenta inoltre gli stati operativi DRED `d1`, `d2` e `d3` senza specificarne le percentuali.
   - Verifica sui due comandi XE7A: Off → `DRED=0`; D2 → `DRED=2`; D3 → `DRED=3`. D1 viene riportato, a seconda del controller/firmware, come `DRED=1` oppure `Idemand=1,DRED=0`; entrambe le forme vanno normalizzate a D1. `DREDEn=1` indica la capability. L'attivazione annulla Quiet.
   - Significato DRED: D1 disabilita il compressore (la ventola interna può continuare), D2 limita la domanda a non oltre il 50%, D3 a non oltre il 75%. Sono limiti massimi e non misure istantanee.
   - Entità implementata: select Off/D1/D2/D3, disabilitata per default e disponibile solo con unità accesa in Cool.

2. **Absence / antigelo 8 °C**
   - Manuale: funzionamento minimo in assenza, anti-gelo a 8 °C; solo riscaldamento.
   - Proprietà osservata: `GoOut`.
   - Entità proposta: switch, disponibile solo in modalità Heat.

3. **X-Fan**
   - Manuale: asciuga la batteria dopo lo spegnimento in Cool/Dry per limitare muffe.
   - Proprietà probabile: `Blo`.
   - Entità proposta: switch con nome e descrizione corretti.

4. **Errori e stati operativi**
   - Proprietà osservate: `Errcode`, `ErrType`, `RefLeak`, `MSysStatus`.
   - Entità proposte: sensore codice errore, binary sensor problema, binary sensor
     avviso refrigerante, sensore stato sistema.
   - Stati documentati: `CL` pulizia automatica, `Fo` recupero refrigerante,
     `H1` sbrinamento, `d1`-`d3` DRED.

5. **Manutenzione filtro**
   - Manuale: reminder configurabile su livelli 10-39 e reset dell'accumulo.
   - Proprietà osservate: `CleanEn`, `CleanTime`, `CleanDataFlag`, `FClTime`, `FClRes`.
   - Entità proposte: stato reminder, ore/tempo accumulato, livello reminder e pulsante
     di reset. I significati numerici devono essere verificati prima di inviare comandi.

6. **Auto Clean**
   - Manuale: avviabile a unità spenta; ciclo di circa 30 minuti.
   - Proprietà osservate: `AutoClean`, `CleanState`.
   - Entità proposte: button `Avvia pulizia automatica` e sensore stato; non uno switch.

### Priorità media

7. **Dry a bassa temperatura (12 °C)**
   - Manuale: disponibile in Dry, entra impostando 12 °C; uscita aumentando il setpoint
     o cambiando modalità.
   - Proprietà osservata: `LowDeHumi`.
   - L'attuale limite globale di 16 °C impedisce questa funzione.
   - Proposta: comando dedicato disponibile solo in Dry, dopo prova controllata.

8. **Deumidificazione con target umidità**
   - Manuale: 45-75%, passi da 5%, default 65%, solo modelli compatibili.
   - Proprietà osservate: `HumiEnable`, `SetCoolHumi`, `InHumiEn`, `InHumi`.
   - Entità proposta: number per target umidità solo se la capability è attiva.

9. **Fresh Air / ricambio aria**
   - Manuale: funzione Air opzionale con livelli 1-10.
   - Proprietà osservate: `Air`, `AirLevel`.
   - Entità proposte: switch Air e select/number livello 1-10, solo sui modelli supportati.

10. **Sleep**
    - Manuale: Sleep 1, Sleep 2, Sleep 3 e modalità notte personalizzata.
    - Proprietà osservate: `SwhSlp`, `SlpMod`.
    - Entità proposta: select Off/Sleep 1/Sleep 2/Sleep 3 dopo verifica dei valori MQTT.

11. **Diagnostica avanzata**
    - `DRED`, `DREDEn`, `AidHeat`, `TemBody`, `UaeEn`, `VoiceGate`, `AppTimer`,
      `PlProg` sono presenti nel protocollo osservato.
    - Devono rimanere read-only finché il significato e i valori non sono verificati.

## Parametri installatore: non esporre come normali controlli

Il comando XE7A e il service manual documentano parametri che possono cambiare il
comportamento dell'impianto:

- P20: sorgente temperatura ambiente (ripresa, controller o mista);
- P22: compensazione sensore in riscaldamento (-15..15);
- P30: pressione statica/curva del ventilatore;
- P37/P38: setpoint Auto cool/heat;
- P43: priorità di funzionamento;
- P46: reset filtro;
- P71-P74: ripristino e limiti;
- P78: prevenzione aria fredda;
- P82: selezione sensore in spegnimento termostatico;
- P83/P84: massima differenza dei setpoint;
- P85: soglia umidità;
- P86: modo controllo Dry;
- P87: passo setpoint 0,5/1 °C.

Non è stata osservata una corrispondenza MQTT affidabile per i codici Pxx. Questi
parametri non devono essere scritti tentando nomi come `P30`: una scrittura errata può
alterare la taratura dell'impianto. Se in futuro verranno identificati, dovranno essere
presentati in una sezione amministratore/installer, disabilitata per default, con lettura,
conferma esplicita e rollback.

## Pressione statica P30 per canalizzabili

Il service manual fornisce la relazione tra livello e pressione esterna:

| Modello | P1 | P2 | P3 | P4 | P5 (default) | P6 | P7 | P8 | P9 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GUD35/50 | — | — | 0 | 15 | 25 | 50 | 80 | — | — |
| GUD71 | 0 | 10 | 15 | 20 | 25 | 50 | 75 | 100 | 160 |
| GUD85 | 0 | 10 | 15 | 20 | 37 | 50 | 75 | 100 | 160 |
| GUD100 | 0 | 10 | 15 | 25 | 37 | 50 | 75 | 100 | 160 |
| GUD125 | 0 | 10 | 25 | 37 | 50 | 75 | 100 | 125 | 160 |
| GUD140/160 | 0 | 10 | 25 | 37 | 50 | 75 | 100 | 150 | 200 |

Questa tabella è utile nel pannello come riferimento installatore. Non va trasformata in
un controllo finché non viene identificato il comando cloud corretto. Le schede di anni
diversi non sono perfettamente concordi sul massimo del GUD140; serve il modello completo
e la revisione dell'unità.

## Correzione database modelli/energia

I valori nominali per i canalizzabili riportati nel catalogo sono:

| Modello | Cool kW | Heat kW | Max kW |
|---|---:|---:|---:|
| GUD35 | 1,03 | 1,00 | 1,30 |
| GUD50 | 1,51 | 1,42 | 1,90 |
| GUD71 | 1,92 | 2,00 | 2,80 |
| GUD85 | 2,50 | 2,25 | 3,30 |
| GUD100 | 3,00 | 2,80 | 4,70 monofase / 4,40 trifase |
| GUD140 | 4,60 | 4,70 | 5,60 |
| GUD160 | 5,40 | 4,70 | 6,80 |

Il database corrente contiene valori non esatti per GUD35, GUD50 e GUD85 e non distingue
le varianti monofase/trifase. La stima resta indicativa: non sostituisce un misuratore di
energia.

## Migliorie del pannello

Mantenere tutte le tab esistenti e aggiungere/migliorare:

1. **Devices**
   - raggruppare controlli in Clima, Comfort, Qualità aria, Energia, Manutenzione;
   - nascondere i controlli non supportati dal singolo dispositivo;
   - mostrare vincoli contestuali (I-Demand solo Cool, Absence solo Heat, ecc.);
   - conferma per Auto Clean e reset filtro.

2. **Nuova tab U-Match**
   - profilo modello completo;
   - portate, assorbimenti, refrigerante e intervallo ESP;
   - tabella P30 specifica del modello;
   - matrice capability: disponibile, non supportata, da verificare.

3. **Info/diagnostica**
   - codici errore tradotti e stato operativo;
   - stato filtro e pulizia;
   - proprietà avanzate read-only;
   - evitare chiavi e token, già redatti dalla versione 0.2.8.

4. **Energia**
   - usare i sensori backend persistenti invece del contatore JavaScript;
   - correggere i dati dei modelli;
   - indicare chiaramente `stimato`, modello e margine di incertezza.

## Piano di implementazione sicuro

1. Correggere nomi, database modelli ed energia senza nuovi comandi.
2. Aggiungere capability registry e diagnostica read-only (`Errcode`, filtro, clean,
   stati sistema).
3. Aggiungere entità `button`, `number` e `select` con disponibilità dinamica.
4. Verificare sul dispositivo, una funzione per volta, `GoOut`, `Blo`,
   `AutoClean`, `LowDeHumi`, `AirLevel`, `SwhSlp`/`SlpMod`.
5. Solo dopo conferma del read-back, attivare i controlli nel pannello.
6. Lasciare P20/P22/P30 e gli altri parametri installatore in sola documentazione finché
   non esiste una mappatura cloud dimostrata.
