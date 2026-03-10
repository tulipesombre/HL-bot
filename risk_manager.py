import logging
from config_manager import load, save

logger = logging.getLogger(__name__)

DEFAULT_PRECISION = 4
DEFAULT_MIN_SIZE  = 0.001

KNOWN_PRECISION = {
    "BTC":  5, "ETH": 4, "SOL": 2, "HYPE": 1, "BNB": 3,
}
KNOWN_MIN_SIZE = {
    "BTC":  0.0001, "ETH": 0.001, "SOL": 0.1, "HYPE": 1.0, "BNB": 0.01,
}


def get_coin(ticker: str) -> str:
    cfg = load()
    ticker_map = cfg.get("ticker_map", {})
    if ticker in ticker_map:
        return ticker_map[ticker]
    return ticker.replace("USDT.P", "").replace("USDT", "").replace("-PERP", "")


def add_asset(ticker: str, channel_id: int = 0) -> dict:
    """Ajoute un asset dynamiquement sans toucher au code."""
    cfg = load()
    coin = ticker.replace("USDT.P", "").replace("USDT", "").replace("-PERP", "")

    if "ticker_map" not in cfg:
        cfg["ticker_map"] = {}
    cfg["ticker_map"][ticker] = coin
    cfg["assets"][coin] = True

    if channel_id:
        if "asset_channels" not in cfg:
            cfg["asset_channels"] = {}
        cfg["asset_channels"][coin] = channel_id

    save(cfg)
    return {"coin": coin, "ticker": ticker, "channel_id": channel_id}


def remove_asset(coin: str) -> bool:
    cfg = load()
    coin = coin.upper()
    cfg.get("assets", {}).pop(coin, None)
    ticker_map = cfg.get("ticker_map", {})
    for k in [k for k, v in ticker_map.items() if v == coin]:
        del ticker_map[k]
    cfg.get("asset_channels", {}).pop(coin, None)
    save(cfg)
    return True


def get_precision(coin: str) -> int:
    return load().get("coin_precision", {}).get(coin, KNOWN_PRECISION.get(coin, DEFAULT_PRECISION))


def get_min_size(coin: str) -> float:
    return load().get("coin_min_size", {}).get(coin, KNOWN_MIN_SIZE.get(coin, DEFAULT_MIN_SIZE))


def should_trade(setup: str, ticker: str, dr_detail: str) -> tuple[bool, str]:
    cfg = load()
    if not cfg["bot_active"]:
        return False, "🔴 Bot en pause"
    coin = get_coin(ticker)
    if not cfg["assets"].get(coin, False):
        return False, f"🔴 {coin} désactivé dans la config"
    setups = cfg["setups"]
    if setups != "both" and setup != setups:
        return False, f"🔴 Setup {setup} désactivé (actif : {setups})"
    dr_filter = cfg["dr_filter"]
    if dr_filter == "soft" and "contraire" in dr_detail:
        return False, "🔴 DR contraire bloqué (mode soft)"
    if dr_filter == "strict" and "✓" not in dr_detail:
        return False, "🔴 DR non aligné bloqué (mode strict)"
    return True, "✅ OK"


def calc_max_safe_leverage(entry: float, sl: float, is_long: bool, safety: float = 0.8) -> int:
    dist_pct = (entry - sl) / entry if is_long else (sl - entry) / entry
    if dist_pct <= 0:
        return 1
    return max(1, int(safety / dist_pct))


def calc_position(entry: float, sl: float, balance: float) -> dict:
    cfg      = load()
    is_long  = sl < entry
    risk_usd = balance * (cfg["risk_pct"] / 100)
    sl_dist  = abs(entry - sl)
    sl_pct   = sl_dist / entry
    if sl_pct == 0:
        raise ValueError("Distance SL = 0, impossible de calculer la position")
    position_usd = risk_usd / sl_pct
    size_raw     = position_usd / entry
    r            = cfg["r_target"]
    tp           = entry + sl_dist * r if is_long else entry - sl_dist * r
    leverage     = max(1, min(calc_max_safe_leverage(entry, sl, is_long), cfg.get("max_leverage", 40)))
    return {
        "size_raw": size_raw, "position_usd": round(position_usd, 2),
        "risk_usd": round(risk_usd, 2), "tp": tp, "sl": sl, "entry": entry,
        "leverage": leverage, "is_long": is_long,
        "sl_pct": round(sl_pct * 100, 3), "r_target": r,
    }


def round_size(coin: str, size_raw: float) -> float:
    precision = get_precision(coin)
    min_size  = get_min_size(coin)
    size = round(size_raw, precision)
    if size < min_size:
        raise ValueError(f"Taille calculée ({size}) inférieure au minimum ({min_size}) pour {coin}")
    return size
