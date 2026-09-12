# T53 — Frame metrics 5s: panel/debate fuori dal WS, detail on-demand

## Contesto
Il frame `metrics` (ogni 5s) include `agents.recent` (40 cicli) e `trades` con
`panel` e `debate` completi (multi-KB ciascuno). Misura locale: frame ~1.27 MB,
~28 KB/s. Con l'accrescersi di trades/cicli il push diventa il collo di
bottiglia della pagina e della banda locale.

## Cambamenti
1. `server.py`:
   - `_light(recs)`: dict senza `panel`/`debate` (help: strip lato server).
   - `agents_stats`: `recent` = `_light(scans[-40:])`, aggiunto `lastCycle`
     = primo degli ultimi cicli (strippato) — `renderLastCycle` nel client
     lo consuma ma il server non lo spediva mai (card overview sempre vuota).
   - `snapshot_slow`: `trades` = `_light(build_trades())`.
   - Nuove route on-demand: `GET /api/trade/{id}` (record completo con
     panel/debate) e `GET /api/scan?ts&coin` (ciclo completo, salta record
     `stage`).
2. `app.js`:
   - `toggleTradeRow`: detail grid dal record light; `panel`/`debate` via
     `fetch('/api/trade/{id}')`, cache `S.tDetail[id]` (sempre viva, il merge
     in S.trades moriva col refresh 5s) + `S.tPending` anti-dup.
   - `showCycle`: idem da `/api/scan`, cache `S.scanDetail[ts|coin]` +
     `S.scanPending` anti-dup.
   - `renderTrades`: sig-skip rebuild (id:status:exit:tps) + riapertura xrow
     post-rebuild da `S._openTrade` (fix: xrow aperta collassa ogni 5s).
   - `renderAgents`: sig-skip (ts|coin|executed|reason) + select preserva
     la selezione per CHIAVE `S._scKey` quando l'utente ha scelto
     (`dataset.user`), non per indice (le nuove scan prependono).
   - `S.trades`/`S.scans` sono array freschi ogni frame: le cache detail sono
     separate e sopravvivono ai rebuild.
3. Test: `test_t53_strip.py` — `_light` strip-only, route `/api/trade/{id}`
   200/404, route `/api/scan` skip `stage`, recent/lastCycle strippati nel
   frame.

## Risultato atteso
Frame metrics ~300 KB (≈75% meno), xrow/select stabili tra refresh 5s,
overview "Ultimo ciclo" finalmente valorizzata.

## Esito
(in corso)
