import os
import logging
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hyperliquid.xyz"

# Stockage en mémoire des OIDs SL/TP par coin
# { coin: { "sl_oid": int, "tp_oid": int, "entry": float, "is_long": bool, "size": float, "tp": float } }
open_orders: dict = {}


def _clients():
    pk = os.environ["HL_PRIVATE_KEY"]
    account = eth_account.Account.from_key(pk)
    exchange = Exchange(account, base_url=BASE_URL)
    info = Info(base_url=BASE_URL, skip_ws=True)
    return exchange, info, account.address


def get_balance() -> float:
    _, info, address = _clients()
    state = info.user_state(address)
    # Unified account → crossMarginSummary, sinon marginSummary
    summary = state.get("crossMarginSummary") or state.get("marginSummary", {})
    return float(summary.get("accountValue", 0))


def get_positions() -> list:
    _, info, address = _clients()
    state = info.user_state(address)
    return [p for p in state["assetPositions"] if float(p["position"]["szi"]) != 0]


def _extract_oid(order_result: dict) -> int | None:
    """Extrait l'OID d'une réponse d'ordre HL."""
    try:
        status = order_result["response"]["data"]["statuses"][0]
        if "resting" in status:
            return status["resting"]["oid"]
        if "filled" in status:
            return status["filled"].get("oid")
    except Exception:
        pass
    return None


def _extract_fill_price(order_result: dict, fallback: float) -> float:
    try:
        status = order_result["response"]["data"]["statuses"][0]
        if "filled" in status:
            return float(status["filled"]["avgPx"])
    except Exception:
        pass
    return fallback


def open_trade(coin: str, is_long: bool, size: float, leverage: int,
               sl_price: float, tp_price: float) -> dict:
    exchange, _, _ = _clients()

    # 1. Levier isolé
    lev_result = exchange.update_leverage(leverage, coin, is_cross=False)
    logger.info(f"Levier {coin}: {lev_result}")

    # 2. Ordre market
    market_result = exchange.market_open(coin, is_long, size, slippage=0.01)
    logger.info(f"Market open {coin}: {market_result}")

    if market_result.get("status") != "ok":
        return {"success": False, "error": market_result}

    fill_price = _extract_fill_price(market_result, sl_price)

    # 3. Stop-Loss (trigger market, reduce only)
    sl_result = exchange.order(
        coin,
        not is_long,
        size,
        sl_price,
        order_type={"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    sl_oid = _extract_oid(sl_result)
    logger.info(f"SL order {coin} oid={sl_oid}: {sl_result}")

    # 4. Take-Profit (trigger limit, reduce only)
    tp_result = exchange.order(
        coin,
        not is_long,
        size,
        tp_price,
        order_type={"trigger": {"triggerPx": tp_price, "isMarket": False, "tpsl": "tp"}},
        reduce_only=True,
    )
    tp_oid = _extract_oid(tp_result)
    logger.info(f"TP order {coin} oid={tp_oid}: {tp_result}")

    # Stockage local pour les actions Discord
    open_orders[coin] = {
        "sl_oid":   sl_oid,
        "tp_oid":   tp_oid,
        "entry":    fill_price,
        "is_long":  is_long,
        "size":     size,
        "tp":       tp_price,
        "sl":       sl_price,
    }

    return {
        "success":     True,
        "fill_price":  fill_price,
        "sl_oid":      sl_oid,
        "tp_oid":      tp_oid,
    }


def move_sl_to_be(coin: str) -> dict:
    """Déplace le SL au prix d'entrée (Break-Even)."""
    exchange, _, _ = _clients()
    trade = open_orders.get(coin)

    if not trade:
        return {"success": False, "error": f"Aucune donnée locale pour {coin}"}

    entry    = trade["entry"]
    is_long  = trade["is_long"]
    size     = trade["size"]
    sl_oid   = trade["sl_oid"]

    # Annuler le SL existant
    if sl_oid:
        cancel_result = exchange.cancel(coin, sl_oid)
        logger.info(f"Cancel SL {coin} oid={sl_oid}: {cancel_result}")

    # Replacer le SL au BE
    new_sl = exchange.order(
        coin,
        not is_long,
        size,
        entry,
        order_type={"trigger": {"triggerPx": entry, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    new_oid = _extract_oid(new_sl)
    logger.info(f"Nouveau SL BE {coin} oid={new_oid}: {new_sl}")

    open_orders[coin]["sl_oid"] = new_oid
    open_orders[coin]["sl"]     = entry

    return {"success": True, "be_price": entry, "new_sl_oid": new_oid}


def close_position(coin: str) -> dict:
    """Ferme la position en market et annule SL/TP."""
    exchange, _, address = _clients()
    trade = open_orders.get(coin)

    # Annuler SL et TP si on a les OIDs
    if trade:
        for oid_key in ("sl_oid", "tp_oid"):
            oid = trade.get(oid_key)
            if oid:
                try:
                    exchange.cancel(coin, oid)
                except Exception as e:
                    logger.warning(f"Impossible d'annuler {oid_key} {oid}: {e}")

    # Fermer en market
    result = exchange.market_close(coin)
    logger.info(f"Market close {coin}: {result}")

    if result.get("status") == "ok":
        open_orders.pop(coin, None)
        return {"success": True}
    return {"success": False, "error": result}
