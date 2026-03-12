import os
import logging
import requests
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hyperliquid.xyz"

# Coins TradFi — tradés via les marchés spot @N sur HL
TRADFI_COINS = {"SILVER", "GOLD", "CL", "XYZ100", "USA500", "EUR"}

# Noms des tokens spot HL pour chaque coin TradFi
TRADFI_TOKEN_ALIASES = {
    "SILVER": ["SLV", "SILVER"],
    "GOLD":   ["GLD", "GOLD"],
    "XYZ100": ["NQ", "XYZ100", "NASDAQ"],
    "USA500": ["ES", "USA500", "SPY", "SP500"],
    "CL":     ["CL", "OIL", "CRUDE"],
    "EUR":    ["EUR"],
}

# Cache coin → @N
_spot_market_cache: dict = {}

# Stockage OIDs SL/TP en mémoire
open_orders: dict = {}


def _clients():
    pk       = os.environ["HL_PRIVATE_KEY"]
    account  = eth_account.Account.from_key(pk)
    exchange = Exchange(account, base_url=BASE_URL)
    info     = Info(base_url=BASE_URL, skip_ws=True)
    main_address = os.environ.get("HL_WALLET_ADDRESS", account.address)
    return exchange, info, main_address


def get_balance() -> float:
    _, info, address = _clients()
    spot = info.spot_user_state(address)
    for b in spot.get("balances", []):
        if b["coin"] == "USDC":
            return float(b["total"])
    return 0.0


def get_positions() -> list:
    _, info, address = _clients()
    state = info.user_state(address)
    return [p for p in state["assetPositions"] if float(p["position"]["szi"]) != 0]


def _extract_oid(order_result: dict):
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


# ════════════════════════════════════════════════════════════
# SPOT MARKET HELPERS (TradFi)
# ════════════════════════════════════════════════════════════

def _find_spot_market_id(coin: str) -> str:
    """Retourne l'identifiant @N du marché spot HL pour un coin TradFi."""
    if coin in _spot_market_cache:
        return _spot_market_cache[coin]

    resp = requests.post(
        f"{BASE_URL}/info",
        json={"type": "spotMetaAndAssetCtxs"},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    data     = resp.json()
    tokens   = data[0].get("tokens", [])
    universe = data[0].get("universe", [])

    idx_to_name  = {t["index"]: t["name"] for t in tokens}
    target_names = set(TRADFI_TOKEN_ALIASES.get(coin, [coin]))

    for market in universe:
        mkt_name = market.get("name", "")
        if not mkt_name.startswith("@"):
            continue
        for tok_idx in market.get("tokens", []):
            if idx_to_name.get(tok_idx, "") in target_names:
                _spot_market_cache[coin] = mkt_name
                logger.info(f"TradFi spot: {coin} → {mkt_name} (token: {idx_to_name.get(tok_idx)})")
                return mkt_name

    raise KeyError(f"Spot market introuvable pour {coin} (cherchait: {target_names})")


def _spot_mid_price(market_id: str, info) -> float:
    mids = info.all_mids()
    if market_id in mids:
        return float(mids[market_id])
    raise KeyError(f"Prix introuvable pour {market_id}")


def _market_close_spot(exchange, info, market_id: str, is_long: bool, size: float) -> dict:
    mid      = _spot_mid_price(market_id, info)
    limit_px = round(mid * 0.98 if is_long else mid * 1.02, 4)
    return exchange.order(
        market_id, not is_long, size, limit_px,
        order_type={"limit": {"tif": "Ioc"}},
        reduce_only=True,
    )


# ════════════════════════════════════════════════════════════
# OPEN TRADE
# ════════════════════════════════════════════════════════════

def open_trade(coin: str, is_long: bool, size: float, leverage: int,
               sl_price: float, tp_price: float, entry_price: float = 0.0) -> dict:
    if coin in TRADFI_COINS:
        return _open_trade_spot(coin, is_long, size, leverage, entry_price, sl_price, tp_price)
    return _open_trade_perp(coin, is_long, size, leverage, sl_price, tp_price)


def _open_trade_perp(coin: str, is_long: bool, size: float, leverage: int,
                     sl_price: float, tp_price: float) -> dict:
    exchange, _, _ = _clients()

    lev_result = exchange.update_leverage(leverage, coin, is_cross=False)
    logger.info(f"Levier {coin}: {lev_result}")

    market_result = exchange.market_open(coin, is_long, size, slippage=0.01)
    logger.info(f"Market open {coin}: {market_result}")

    if not market_result or market_result.get("status") != "ok":
        return {"success": False, "error": str(market_result)}

    fill_price = _extract_fill_price(market_result, sl_price)

    sl_result = exchange.order(
        coin, not is_long, size, sl_price,
        order_type={"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    sl_oid = _extract_oid(sl_result)
    logger.info(f"SL {coin} oid={sl_oid}: {sl_result}")

    tp_result = exchange.order(
        coin, not is_long, size, tp_price,
        order_type={"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}},
        reduce_only=True,
    )
    tp_oid = _extract_oid(tp_result)
    logger.info(f"TP {coin} oid={tp_oid}: {tp_result}")

    open_orders[coin] = {
        "sl_oid": sl_oid, "tp_oid": tp_oid,
        "entry": fill_price, "is_long": is_long,
        "size": size, "tp": tp_price, "sl": sl_price,
        "is_spot": False,
    }
    return {"success": True, "fill_price": fill_price, "sl_oid": sl_oid, "tp_oid": tp_oid}


def _open_trade_spot(coin: str, is_long: bool, size: float, leverage: int,
                     entry_price: float, sl_price: float, tp_price: float) -> dict:
    exchange, info, _ = _clients()

    market_id = _find_spot_market_id(coin)

    lev_result = exchange.update_leverage(leverage, market_id, is_cross=False)
    logger.info(f"Levier spot {coin} ({market_id}): {lev_result}")

    if not entry_price or entry_price <= 0:
        raise ValueError(f"Prix introuvable pour {coin} — spécifie le paramètre entry manuellement")
    mid      = entry_price
    limit_px = round(mid * 1.002 if is_long else mid * 0.998, 2)

    market_result = exchange.order(
        market_id, is_long, size, limit_px,
        order_type={"limit": {"tif": "Ioc"}},
        reduce_only=False,
    )
    logger.info(f"Market open spot {coin} ({market_id}): {market_result}")

    if not market_result or market_result.get("status") != "ok":
        return {"success": False, "error": str(market_result)}

    fill_price = _extract_fill_price(market_result, entry_price or mid)

    sl_result = exchange.order(
        market_id, not is_long, size, sl_price,
        order_type={"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    sl_oid = _extract_oid(sl_result)
    logger.info(f"SL spot {coin} oid={sl_oid}: {sl_result}")

    tp_result = exchange.order(
        market_id, not is_long, size, tp_price,
        order_type={"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}},
        reduce_only=True,
    )
    tp_oid = _extract_oid(tp_result)
    logger.info(f"TP spot {coin} oid={tp_oid}: {tp_result}")

    open_orders[coin] = {
        "sl_oid": sl_oid, "tp_oid": tp_oid,
        "entry": fill_price, "is_long": is_long,
        "size": size, "tp": tp_price, "sl": sl_price,
        "market_id": market_id,
        "is_spot": True,
    }
    return {"success": True, "fill_price": fill_price, "sl_oid": sl_oid, "tp_oid": tp_oid}


# ════════════════════════════════════════════════════════════
# MOVE SL TO BE
# ════════════════════════════════════════════════════════════

def move_sl_to_be(coin: str) -> dict:
    exchange, _, _ = _clients()
    trade = open_orders.get(coin)

    if not trade:
        return {"success": False, "error": f"Aucune donnée locale pour {coin}"}

    entry    = trade["entry"]
    is_long  = trade["is_long"]
    size     = trade["size"]
    sl_oid   = trade["sl_oid"]
    coin_key = trade.get("market_id", coin)

    if sl_oid:
        cancel_result = exchange.cancel(coin_key, sl_oid)
        logger.info(f"Cancel SL {coin} oid={sl_oid}: {cancel_result}")

    new_sl = exchange.order(
        coin_key, not is_long, size, entry,
        order_type={"trigger": {"triggerPx": entry, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    new_oid = _extract_oid(new_sl)
    logger.info(f"Nouveau SL BE {coin} oid={new_oid}: {new_sl}")

    open_orders[coin]["sl_oid"] = new_oid
    open_orders[coin]["sl"]     = entry

    return {"success": True, "be_price": entry, "new_sl_oid": new_oid}


# ════════════════════════════════════════════════════════════
# CLOSE POSITION
# ════════════════════════════════════════════════════════════

def close_position(coin: str) -> dict:
    exchange, info, _ = _clients()
    trade    = open_orders.get(coin)
    is_spot  = trade.get("is_spot", False) if trade else False
    coin_key = trade.get("market_id", coin) if trade else coin

    if trade:
        for oid_key in ("sl_oid", "tp_oid"):
            oid = trade.get(oid_key)
            if oid:
                try:
                    exchange.cancel(coin_key, oid)
                except Exception as e:
                    logger.warning(f"Impossible d'annuler {oid_key} {oid}: {e}")

    if is_spot and trade:
        result = _market_close_spot(exchange, info, coin_key, trade["is_long"], trade["size"])
    else:
        result = exchange.market_close(coin)

    logger.info(f"Close {coin}: {result}")

    if result and result.get("status") == "ok":
        open_orders.pop(coin, None)
        return {"success": True}

    try:
        positions = get_positions()
        still_open = any(
            p["position"]["coin"] == coin and float(p["position"]["szi"]) != 0
            for p in positions
        )
        if not still_open:
            open_orders.pop(coin, None)
            return {"success": True}
    except Exception as e:
        logger.warning(f"Impossible de vérifier position après close: {e}")

    return {"success": False, "error": str(result)}


# ════════════════════════════════════════════════════════════
# GET MID PRICE (pour /trade manuel)
# ════════════════════════════════════════════════════════════

def get_mid_price(coin: str) -> float:
    _, info, _ = _clients()
    mids = info.all_mids()

    # Crypto perps
    if coin in mids:
        return float(mids[coin])

    # TradFi — prix doit être fourni manuellement
    if coin in TRADFI_COINS:
        raise KeyError(f"Coin '{coin}' est TradFi — spécifie le prix entry manuellement")
    raise KeyError(f"Coin '{coin}' introuvable sur Hyperliquid")
