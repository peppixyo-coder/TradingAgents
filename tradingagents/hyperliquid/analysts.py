"""Grafo agenti Combo-1: pannello analisti -> dibattito bull/bear -> decisione JSON.

ponytail: grafo lineare a 3 chiamate via HTTP raw (pattern provato dal test
di connettivita': il router incapsula il JSON in text/event-stream con padding,
che l'SDK openai non digerisce sempre). L'integrazione nel motore graph/ di
TradingAgents (avvio subagenti, memoria condivisa, reporter) e' la
generalizzazione post-spike.
"""
import json
import urllib.request

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


def _post(base_url, key, payload, timeout=300):
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    # Il router manda text/event-stream con padding whitespace prima del JSON:
    # si taglia al primo '{' (stesso workaround del test di connettivita').
    obj, _ = json.JSONDecoder().raw_decode(body[body.index("{"):])
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
    return resp["choices"][0]["message"]["content"]


def run_graph(cfg, ctx):
    """ctx: stringa di contesto compatta prodotta dal runner.

    Ritorna dict {panel, debate, decision}; side e' firmato dall'LLM,
    conviction resta meccanico a valle (risk.py).
    """
    panel = _chat(cfg, PANEL_SYS, ctx)
    debate = _chat(cfg, DEBATE_SYS, f"Contesto:\n{ctx}\n\nPannello analisti:\n{panel}")
    raw = _chat(cfg, DECIDE_SYS + "\n" + LEV_SYS,
                f"Contesto:\n{ctx}\n\nPannello analisti:\n{panel}\n\nDibattito:\n{debate}")
    try:
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except ValueError:
        raise RuntimeError(f"decisione non-JSON dal modello: {raw[:300]}")
    if j.get("side") not in ("long", "short", "flat"):
        raise RuntimeError(f"side invalido nella decisione: {raw[:300]}")
    lev = j.get("leverage")
    if isinstance(lev, bool) or not isinstance(lev, (int, float)) or float(lev) < 1:
        j["leverage"] = None  # il loop skippa con NO_LEVERAGE: nessun default nel codice
    conf = j.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= float(conf) <= 1:
        j["confidence"] = None  # advisory: registrato ma non usato per il sizing
    return {"panel": panel, "debate": debate, "decision": j}
