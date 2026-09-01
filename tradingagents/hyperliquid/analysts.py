"""Grafo agenti Combo-1: pannello analisti -> dibattito bull/bear -> decisione JSON.

ponytail: grafo lineare a 3 chiamate via HTTP raw (pattern provato dal test
di connettivita': il router incapsula il JSON in text/event-stream con padding,
che l'SDK openai non digerisce sempre). L'integrazione nel motore graph/ di
TradingAgents (avvio subagenti, memoria condivisa, reporter) e' la
generalizzazione post-spike.
"""
import json
import urllib.request
import time

PANEL_SYS = (
    "Sei una sala analisti crypto per perpetual su Hyperliquid. Rispondi in "
    "italiano, compatto (max 200 parole), con tre sezioni: TECNICO, "
    "FONDAMENTALE-SURROGATO (funding/OI/taker flow come surrogati dei dati on-chain), "
    "SENTIMENT. Ogni sezione termina con una riga 'bias: long|short|neutral, forza 0-100'."
)
DEBATE_SYS = (
    "Sei due ricercatori avversari: un bull e un bear. Ciascuno smonta gli argomenti "
    "dell'altro in massimo 80 parole. Formato esatto:\nBULL: ...\nBEAR: ...\nVERDETTO: ..."
)
DECIDE_SYS = (
    'Sei il portfolio manager finale di un fondo prop. Rispondi SOLO con JSON compatto '
    '{"side":"long|short|flat","confidence":0..1,"leverage":<numero>=1,"rationale":"max 40 parole"}. '
    "side=flat se le evidenze contraddicono la direzione del segnale quantitativo o se "
    "il quadro e' incoerente. Non inventare campi. OBBLIGATORIO: quando side e' long "
    "o short il campo 'leverage' deve SEMPRE essere presente (un numero >= 1): una "
    "decisione senza leva viene scartata."
)
LEV_SYS = (
    "Scegli TU la leva della posizione dai dati 'Rischio' nel contesto: nessun default "
    "fisso. sigma_ann alta e funding costoso giustificano meno leva; confidence alta e "
    "stop distante possono giustificarne di piu'. Lascia margine nel budget di leva "
    "totale del portfolio per posizioni future: la tua leva non consuma tutto il cap."
)


def _post(base_url, key, payload, timeout=180, retries=2):
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    # ponytail: timeout 180s + retry con backoff: una chiamata Combo-1 appesa
    # non deve bloccare il grafo; dopo i tentativi l'errore sale e il loop
    # skippa la coin. Upgrade: dead-letter con retry budget per coin.
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode()
            break
        except Exception as e:  # timeout/rete/HTTP: riprova con backoff
            last = e
            if attempt >= retries:
                raise RuntimeError(
                    f"LLM HTTP fallita dopo {retries + 1} tentativi: {last!r}") from e
            time.sleep(2 * (attempt + 1))
    # Il router manda text/event-stream con padding whitespace prima del JSON:
    # si taglia al primo '{' (stesso workaround del test di connettivita').
    start = body.find("{")
    if start < 0:
        raise RuntimeError(f"LLM risposta senza JSON: {body[:120]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(body[start:])
    except ValueError as e:
        raise RuntimeError(f"LLM JSON malformato: {body[:120]!r}") from e
    return obj


def _chat(cfg, system, user, max_tokens=2000):
    resp = _post(cfg.router_url, cfg.api_key, {
        "model": cfg.model,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    })
    # ponytail: il router a volte risponde {"error": ...} senza choices:
    # contenuto vuoto -> il chiamante lo tratta come non-JSON e degrada
    # (retry/flat) invece di uccidere il ciclo con KeyError.
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _parse_decision(raw):
    """Estrae il primo oggetto JSON bilanciato da raw (livello 3, il piu'
    robusto): gestisce prosa prima/dopo, stringhe con graffe, markdown
    fences, concatenazione malformata. None se non c'e' nulla di valido."""
    if not raw:
        return None
    dec = json.JSONDecoder()
    for start, ch in enumerate(raw):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(raw, start)
            except ValueError:
                continue            # '{' spurio (es. in prosa): prova il prossimo
        else:
            continue
        if isinstance(obj, dict) and obj.get("side") in ("long", "short", "flat"):
            return obj
    return None


def run_graph(cfg, ctx):
    """ctx: stringa di contesto compatta prodotta dal runner.

    Ritorna dict {panel, debate, decision}; side e' firmato dall'LLM,
    conviction resta meccanico a valle (risk.py).
    """
    def _safe_chat(system, user):
        try:
            return _chat(cfg, system, user)
        except Exception:  # router giu': il grafo degrada a flat, non raise
            return ""

    panel = _safe_chat(PANEL_SYS, ctx)
    debate = _safe_chat(DEBATE_SYS, f"Contesto:\n{ctx}\n\nPannello analisti:\n{panel}")
    decide_user = f"Contesto:\n{ctx}\n\nPannello analisti:\n{panel}\n\nDibattito:\n{debate}"
    # ponytail: Combo-1 ogni tanto risponde vuoto/non-JSON sul DECIDE (vedi
    # bot.log 'decisione non-JSON dal modello: '); retry locale con backoff,
    # poi fallback flat (nessun trade) invece del RuntimeError che abortisce
    # il ciclo. Upgrade: dead-letter con retry budget per coin.
    j, raw = None, ""
    for attempt in range(3):
        try:
            raw = _chat(cfg, DECIDE_SYS + "\n" + LEV_SYS, decide_user) or ""
        except Exception:  # router giu'/timeout dopo i retry HTTP: degrada a flat
            raw = ""
        j = _parse_decision(raw)
        if j is not None:
            break
        time.sleep(1 + attempt)
    if j is None:
        j = {"side": "flat", "confidence": None, "leverage": None,
             "rationale": f"LLM decisione non-JSON dopo 3 tentativi: {raw[:120]!r}"}
    lev = j.get("leverage")
    if isinstance(lev, bool) or not isinstance(lev, (int, float)) or float(lev) < 1:
        j["leverage"] = None  # il loop skippa con NO_LEVERAGE: nessun default nel codice
    conf = j.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= float(conf) <= 1:
        j["confidence"] = None  # advisory: registrato ma non usato per il sizing
    return {"panel": panel, "debate": debate, "decision": j}
