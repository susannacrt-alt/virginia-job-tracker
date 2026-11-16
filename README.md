# Virginia Job Tracker

Dashboard personale per monitorare opportunità professionali compatibili con il profilo di Virginia.

## Funzionalità
- Dashboard web con ricerca, filtri, preferiti e annunci valutati.
- Raccolta automatica quotidiana da board pubbliche Greenhouse e Lever configurate in `config/sources.json`.
- Esclusione di stage/tirocini e annunci esplicitamente riservati a categorie protette/L. 68/99.
- Annunci senza RAL mantenuti con etichetta `RAL da verificare`.
- Deduplicazione e archivio persistente in `data/jobs.json`.

## Avvio
1. Carica tutti i file del pacchetto nel repository, mantenendo le cartelle.
2. In GitHub vai in **Settings → Pages**; seleziona **Deploy from a branch**, branch `main`, cartella `/ (root)`, quindi Save.
3. In **Actions** abilita i workflow se richiesto e avvia `Daily job scan` con **Run workflow**.
4. Aggiungi fonti pubbliche verificate in `config/sources.json`.

## Configurare le fonti
Esempio:
```json
{
  "greenhouse": [{"name": "Nome azienda", "token": "public-board-token"}],
  "lever": [{"name": "Nome azienda", "slug": "public-company-slug"}]
}
```
Gli endpoint Greenhouse e Lever sono pubblici. Non sono utilizzati login, scraping autenticato o bypass anti-bot. Non tutte le aziende usano queste piattaforme; le fonti andranno ampliate progressivamente.

## Criteri iniziali
Aree: securities/back-office operations, custody, settlement, corporate actions, corporate governance, compliance, regulatory affairs, business/process analysis, process improvement, PMO, risk & controls e fund operations.
Geografia: Torino/Piemonte, Milano/Lombardia, Liguria e aree vicine; altre località solo se remote/remoto o compatibili.
RAL desiderata: almeno €32.000; se non pubblicata, l'annuncio resta visibile come `RAL da verificare`.
Esclusi stage, tirocini e annunci esplicitamente riservati a L. 68/99. Inclusi full-time, part-time e contratti pertinenti.

## Privacy
Il repository è pubblico: codice e annunci archiviati saranno visibili pubblicamente. Non caricare CV, recapiti, dati personali o documenti riservati. Preferiti e stato di valutazione sono salvati nel browser locale.
Il punteggio è indicativo: verificare sempre requisiti, sede, modalità di lavoro, contratto e retribuzione nell'annuncio originale.
