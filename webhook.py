import asyncio
import logging
import re
import json as json_module
from flask import Flask, request, jsonify

logger = logging.getLogger(__name__)
app = Flask(__name__)

# Assets TradFi (contiennent "!") auto-tradés sur Hyperliquid
HL_TRADFI_EXCEPTIONS = {"6E1!", "6S1!"}


# ════════════════════════════════════════════════════════════
# HELPERS — PARSING PAYLOAD TRADINGVIEW
# ════════════════════════════════════════════════════════════

def _parse_footer(payload: dict) -> dict:
    try:
        footer = payload["embeds"][0]["footer"]["text"]
        parts  = [p.strip() for p in footer.split("|")]
        result = {}
        for part in parts:
            if "event:" in part:
                result["event"] = part.replace("event:", "").strip()
            elif part in ("S1", "S2"):
                result["setup"] = part
            elif part:
                result["ticker"] = part
        return result
    except Exception:
        return {}


def _get_field(payload: dict, name: str) -> str:
    try:
        for f in payload["embeds"][0]["fields"]:
            if f["name"] == name:
                return f["value"]
    except Exception:
        pass
    return ""


def _execute_trade(payload, meta, db):
    from risk_manager import should_trade, calc_position, round_size, get_coin
    from hyperliquid_client import get_balance, open_trade
    from config_manager import load

    setup     = meta.get("setup", "")
    ticker    = meta.get("ticker", "")
    direction = _get_field(payload, "Direction")
    is_long   = direction == "LONG"
    dr_detail = _get_field(payload, "DR Detail")
    cfg       = load()

    if cfg["sl_type"] == "structural":
        sl_raw = _get_field(payload, "SL_Struct")
    else:
        sl_raw = _get_field(payload, "SL_Chod") or _get_field(payload, "SL_Struct")

    entry_raw   = _get_field(payload, "Entry") or _get_field(payload, "Niveau")
    entry_price = float(entry_raw)
    sl_price    = float(sl_raw)

    ok, reason = should_trade(setup, ticker, dr_detail)
    if not ok:
        logger.info(f"Trade bloqué : {reason}")
        if db.bot_loop:
            asyncio.run_coroutine_threadsafe(
                db.send_trade_blocked(reason, ticker, setup, direction),
                db.bot_loop
            )
        return jsonify({"status": "blocked", "reason": reason}), 200

    coin    = get_coin(ticker)
    balance = get_balance()
    calc    = calc_position(entry_price, sl_price, balance)
    size    = round_size(coin, calc["size_raw"])

    logger.info(
        f"Trade {coin} {direction} | entry={entry_price} sl={sl_price} "
        f"tp={calc['tp']:.4f} size={size} lev={calc['leverage']}x"
    )

    result = open_trade(
        coin, is_long, size,
        calc["leverage"], sl_price, calc["tp"]
    )

    if result["success"]:
        pos = {"fill_price": result["fill_price"], "is_long": is_long}
        trade_info = {"coin": coin, "setup": setup, "ticker": ticker}
        if db.bot_loop:
            asyncio.run_coroutine_threadsafe(
                db.send_trade_opened(trade_info, pos, calc),
                db.bot_loop
            )
        return jsonify({"status": "executed", "coin": coin}), 200
    else:
        msg = f"Trade échoué sur {coin} : {result.get('error')}"
        logger.error(msg)
        if db.bot_loop:
            asyncio.run_coroutine_threadsafe(
                db.send_error(msg), db.bot_loop
            )
        return jsonify({"status": "error", "error": msg}), 500


# ════════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    import discord_bot as db
    from config_manager import load

    try:
        payload = request.get_json(force=True, silent=True)
        if not payload:
            raw = request.get_data(as_text=True)
            logger.error(f"JSON invalide reçu: {raw[:500]}")
            fixed = re.sub(r'(\d),(\d)', r'\1.\2', raw)
            try:
                payload = json_module.loads(fixed)
            except Exception:
                return jsonify({"error": "payload JSON invalide"}), 400
        if not payload:
            return jsonify({"error": "payload vide"}), 400

        meta   = _parse_footer(payload)
        event  = meta.get("event", "")
        logger.info(f"Webhook reçu — event: {event} | meta: {meta}")

        # ── SETUP_ARMED : forward Discord tel quel ──────────────────
        if event == "SETUP_ARMED":
            ticker = meta.get("ticker", "")
            if db.bot_loop:
                asyncio.run_coroutine_threadsafe(
                    db.send_setup_armed(payload, ticker), db.bot_loop
                )
            return jsonify({"status": "forwarded"}), 200

        # ── CHOD_TOUCH ───────────────────────────────────────────────
        elif event == "CHOD_TOUCH":
            cfg         = load()
            entry_mode  = cfg.get("entry_mode", "touch")
            ticker      = meta.get("ticker", "")
            # HL asset = pas de "!" OU exception explicite (futures CME tradés sur HL)
            is_hl_asset = "!" not in ticker or ticker in HL_TRADFI_EXCEPTIONS

            if not is_hl_asset:
                logger.info(f"CHOD_TOUCH TradFi {ticker} → Discord uniquement")
                if db.bot_loop:
                    asyncio.run_coroutine_threadsafe(
                        db.send_setup_armed(payload, ticker), db.bot_loop
                    )
                return jsonify({"status": "forwarded_tradfi"}), 200

            if entry_mode == "touch":
                logger.info("CHOD_TOUCH reçu — entry_mode=touch → exécution trade")
                return _execute_trade(payload, meta, db)
            else:
                logger.info("CHOD_TOUCH reçu — entry_mode=close → forwarding Discord uniquement")
                if db.bot_loop:
                    asyncio.run_coroutine_threadsafe(
                        db.send_setup_armed(payload, ticker), db.bot_loop
                    )
                return jsonify({"status": "forwarded_touch_info"}), 200

        # ── ENTRY_CLOSE ──────────────────────────────────────────────
        elif event == "ENTRY_CLOSE":
            cfg        = load()
            entry_mode = cfg.get("entry_mode", "touch")

            if entry_mode == "close":
                logger.info("ENTRY_CLOSE reçu — entry_mode=close → exécution trade")
                return _execute_trade(payload, meta, db)
            else:
                logger.info("ENTRY_CLOSE reçu — entry_mode=touch → ignoré")
                return jsonify({"status": "ignored_wrong_mode"}), 200

        else:
            logger.warning(f"Événement inconnu : '{event}'")
            return jsonify({"status": "unknown_event"}), 200

    except Exception as e:
        logger.exception("Erreur non gérée dans /webhook")
        if "db" in dir():
            asyncio.run_coroutine_threadsafe(
                db.send_error(f"Exception webhook : {e}"), db.bot_loop
            )
        return jsonify({"error": str(e)}), 500
