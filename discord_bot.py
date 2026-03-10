import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

# Partagé avec main.py et webhook.py pour run_coroutine_threadsafe
bot_loop: asyncio.AbstractEventLoop | None = None

# Channel par défaut (trades ouverts, notifs bot)
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_DEFAULT", os.environ.get("DISCORD_CHANNEL_ID", 0)))

# Channels par asset pour les SETUP_ARMED (même layout que tes channels actuels)
ASSET_CHANNELS = {
    "BTC":  int(os.environ.get("DISCORD_CHANNEL_BTC",  0)),
    "ETH":  int(os.environ.get("DISCORD_CHANNEL_ETH",  0)),
    "SOL":  int(os.environ.get("DISCORD_CHANNEL_SOL",  0)),
    "HYPE": int(os.environ.get("DISCORD_CHANNEL_HYPE", 0)),
}

def get_channel_for_ticker(ticker: str):
    """Retourne le channel Discord pour un ticker donné, ou le channel par défaut."""
    from config_manager import load
    cfg = load()
    # Channels stockés dans config.json (ajoutés via /add_asset)
    asset_channels_cfg = cfg.get("asset_channels", {})
    ticker_upper = ticker.upper()
    # Cherche dans les channels de la config
    for coin, ch_id in asset_channels_cfg.items():
        if coin in ticker_upper and ch_id:
            return bot.get_channel(int(ch_id))
    # Cherche dans les variables d environnement
    for coin, ch_id in ASSET_CHANNELS.items():
        if coin in ticker_upper and ch_id:
            return bot.get_channel(ch_id)
    return bot.get_channel(CHANNEL_ID)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ════════════════════════════════════════════════════════════
# EVENTS
# ════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global bot_loop
    logger.info(f"Discord connecté : {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"{len(synced)} commandes slash synchronisées")
    except Exception as e:
        logger.error(f"Erreur sync slash: {e}")

# ════════════════════════════════════════════════════════════
# BOUTONS INTERACTIFS — TRADE
# ════════════════════════════════════════════════════════════

class TradeView(discord.ui.View):
    def __init__(self, coin: str):
        super().__init__(timeout=None)
        self.coin = coin

    @discord.ui.button(label="🛡️ SL au BE", style=discord.ButtonStyle.primary)
    async def sl_to_be(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            import hyperliquid_client as hl
            result = hl.move_sl_to_be(self.coin)
            if result["success"]:
                be = result["be_price"]
                await interaction.followup.send(
                    f"✅ SL déplacé au BE — **{self.coin}** @ `{be:,.4f}`", ephemeral=True
                )
                button.disabled = True
                await interaction.message.edit(view=self)
            else:
                await interaction.followup.send(
                    f"❌ Erreur : {result.get('error')}", ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Exception : {e}", ephemeral=True)

    @discord.ui.button(label="❌ Fermer position", style=discord.ButtonStyle.danger)
    async def close_pos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            import hyperliquid_client as hl
            result = hl.close_position(self.coin)
            if result["success"]:
                await interaction.followup.send(
                    f"✅ Position **{self.coin}** fermée au marché", ephemeral=True
                )
                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
            else:
                await interaction.followup.send(
                    f"❌ Erreur : {result.get('error')}", ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Exception : {e}", ephemeral=True)

# ════════════════════════════════════════════════════════════
# FONCTIONS D'ENVOI (appelées depuis webhook.py via threadsafe)
# ════════════════════════════════════════════════════════════

async def send_setup_armed(payload: dict, ticker: str = ""):
    """Forward le payload SETUP_ARMED vers le channel Discord dédié à l asset."""
    channel = get_channel_for_ticker(ticker) if ticker else bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error(f"Channel introuvable pour ticker={ticker}")
        return
    try:
        data = payload["embeds"][0]
        embed = discord.Embed(title=data["title"], color=data["color"])
        for f in data.get("fields", []):
            embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", True))
        embed.set_footer(text=data["footer"]["text"])
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Erreur send_setup_armed: {e}")

async def send_trade_opened(trade: dict, pos: dict, calc: dict):
    """Notification position ouverte avec boutons interactifs."""
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    coin    = trade["coin"]
    is_long = pos["is_long"]
    dir_txt = "LONG 🟢" if is_long else "SHORT 🔴"
    emoji   = "📈" if is_long else "📉"
    color   = 0x00e676 if is_long else 0xff1744

    embed = discord.Embed(
        title=f"{emoji}  Position ouverte — {coin}  [{trade['setup']}]",
        color=color,
    )
    embed.add_field(name="Asset",       value=coin,                              inline=True)
    embed.add_field(name="Direction",   value=dir_txt,                           inline=True)
    embed.add_field(name="Setup",       value=trade["setup"],                    inline=True)
    embed.add_field(name="Entry",       value=f"`{pos['fill_price']:,.4f}`",     inline=True)
    embed.add_field(name="Stop-Loss",   value=f"`{calc['sl']:,.4f}`",            inline=True)
    embed.add_field(name="Take-Profit", value=f"`{calc['tp']:,.4f}`",            inline=True)
    embed.add_field(name="Size",        value=f"{calc['size_raw']:.5f} {coin}",  inline=True)
    embed.add_field(name="Pos. USD",    value=f"${calc['position_usd']:,.2f}",   inline=True)
    embed.add_field(name="Risque",      value=f"${calc['risk_usd']:,.2f}",       inline=True)
    embed.add_field(name="Levier",      value=f"{calc['leverage']}x",            inline=True)
    embed.add_field(name="R cible",     value=f"{calc['r_target']}R",            inline=True)
    embed.add_field(name="SL %",        value=f"{calc['sl_pct']}%",              inline=True)

    view = TradeView(coin)
    await channel.send(embed=embed, view=view)

async def send_trade_blocked(reason: str, ticker: str, setup: str, direction: str):
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(
        title=f"⛔  Trade bloqué — {ticker}",
        description=reason,
        color=0x757575,
    )
    embed.add_field(name="Setup",     value=setup,     inline=True)
    embed.add_field(name="Direction", value=direction, inline=True)
    await channel.send(embed=embed)

async def send_error(message: str):
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(title="⚠️  Erreur bot", description=message, color=0xff9800)
    await channel.send(embed=embed)

# ════════════════════════════════════════════════════════════
# SLASH COMMANDS — CONFIG
# ════════════════════════════════════════════════════════════

@bot.tree.command(name="config", description="Voir la configuration du bot")
async def config_show(interaction: discord.Interaction):
    from config_manager import load
    cfg = load()
    assets = " | ".join([f"{'✅' if v else '❌'} {k}" for k, v in cfg["assets"].items()])
    embed = discord.Embed(title="⚙️  Configuration du bot", color=0x7289da)
    embed.add_field(name="💰 Capital",     value=f"${cfg['capital']:,.0f} USDC",              inline=True)
    embed.add_field(name="📊 Risque",      value=f"{cfg['risk_pct']}% par trade",             inline=True)
    embed.add_field(name="🎯 R cible",     value=f"{cfg['r_target']}R",                       inline=True)
    embed.add_field(name="🛡️ Type SL",    value=cfg["sl_type"],                               inline=True)
    embed.add_field(name="📋 Setups",      value=cfg["setups"],                                inline=True)
    embed.add_field(name="🔺 DR Filter",   value=cfg["dr_filter"],                             inline=True)
    embed.add_field(name="⚡ Levier max",  value=f"{cfg.get('max_leverage', 40)}x",            inline=True)
    embed.add_field(name="🎯 Entry mode",  value=cfg.get("entry_mode", "touch"),              inline=True)
    embed.add_field(name="🔛 Bot",         value="✅ Actif" if cfg["bot_active"] else "⏸️ Pause", inline=True)
    embed.add_field(name="📡 Assets",      value=assets,                                       inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="set", description="Modifier un paramètre de configuration")
@app_commands.describe(param="Paramètre", value="Nouvelle valeur")
@app_commands.choices(param=[
    app_commands.Choice(name="capital (USDC)",                value="capital"),
    app_commands.Choice(name="risk_pct (% risqué/trade)",     value="risk_pct"),
    app_commands.Choice(name="r_target (ex: 2)",              value="r_target"),
    app_commands.Choice(name="sl_type (structural / chod)",   value="sl_type"),
    app_commands.Choice(name="setups (S1 / S2 / both)",       value="setups"),
    app_commands.Choice(name="dr_filter (off/soft/strict)",   value="dr_filter"),
    app_commands.Choice(name="max_leverage",                  value="max_leverage"),
    app_commands.Choice(name="entry_mode (touch / close)",   value="entry_mode"),
])
async def set_param(interaction: discord.Interaction, param: str, value: str):
    from config_manager import set_val
    NUMERIC = {"capital", "risk_pct", "r_target", "max_leverage"}
    VALID   = {
        "sl_type":   ["structural", "chod"],
        "setups":    ["S1", "S2", "both"],
        "dr_filter": ["off", "soft", "strict"],
        "entry_mode": ["touch", "close"],
    }
    try:
        if param in NUMERIC:
            set_val(param, float(value))
        elif param in VALID:
            if value not in VALID[param]:
                await interaction.response.send_message(
                    f"❌ Valeurs valides pour `{param}` : `{'` | `'.join(VALID[param])}`",
                    ephemeral=True
                )
                return
            set_val(param, value)
        await interaction.response.send_message(
            f"✅ `{param}` mis à jour → `{value}`", ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

@bot.tree.command(name="toggle_asset", description="Activer / désactiver un asset")
@app_commands.choices(asset=[
    app_commands.Choice(name="BTC",  value="BTC"),
    app_commands.Choice(name="ETH",  value="ETH"),
    app_commands.Choice(name="SOL",  value="SOL"),
    app_commands.Choice(name="HYPE", value="HYPE"),
])
async def toggle_asset(interaction: discord.Interaction, asset: str):
    from config_manager import load, save
    cfg = load()
    cfg["assets"][asset] = not cfg["assets"].get(asset, True)
    save(cfg)
    state = "✅ activé" if cfg["assets"][asset] else "❌ désactivé"
    await interaction.response.send_message(f"**{asset}** {state}", ephemeral=True)

@bot.tree.command(name="pause", description="Mettre le bot en pause (aucun trade)")
async def pause_bot(interaction: discord.Interaction):
    from config_manager import set_val
    set_val("bot_active", False)
    await interaction.response.send_message("⏸️ Bot en **pause** — aucun trade ne sera pris")


@bot.tree.command(name="resume", description="Reprendre le bot")
async def resume_bot(interaction: discord.Interaction):
    from config_manager import set_val
    set_val("bot_active", True)
    await interaction.response.send_message("▶️ Bot **actif** — les trades reprennent")

@bot.tree.command(name="positions", description="Afficher les positions ouvertes")
async def show_positions(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        import hyperliquid_client as hl
        loop = asyncio.get_running_loop()
        positions = await asyncio.wait_for(loop.run_in_executor(None, hl.get_positions), timeout=8.0)
        balance   = await asyncio.wait_for(loop.run_in_executor(None, hl.get_balance),   timeout=8.0)

        if not positions:
            await interaction.followup.send("📭 Aucune position ouverte", ephemeral=True)
            return

        embed = discord.Embed(title="📊  Positions ouvertes", color=0x7289da)
        embed.add_field(name="💰 Balance", value=f"${balance:,.2f} USDC", inline=False)
        for p in positions:
            pos     = p["position"]
            coin    = pos["coin"]
            szi     = float(pos["szi"])
            is_long = szi > 0
            entry   = float(pos["entryPx"])
            upnl    = float(pos["unrealizedPnl"])
            lev     = pos.get("leverage", {}).get("value", "?")
            dir_txt = "LONG 🟢" if is_long else "SHORT 🔴"
            pnl_ico = "✅" if upnl >= 0 else "🔴"
            embed.add_field(
                name=f"{coin}  {dir_txt}",
                value=f"Entry : `{entry:,.4f}`\nSize  : `{abs(szi)}`\nPnL   : {pnl_ico} `${upnl:,.2f}`\nLevier: `{lev}x`",
                inline=True,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {e}", ephemeral=True)

@bot.tree.command(name="balance", description="Afficher le solde du compte Hyperliquid")
async def show_balance(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        import hyperliquid_client as hl
        loop = asyncio.get_running_loop()
        balance = await asyncio.wait_for(
            loop.run_in_executor(None, hl.get_balance),
            timeout=10.0
        )
        await interaction.followup.send(f"💰 Balance : **${balance:,.2f} USDC**", ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Timeout — Hyperliquid ne répond pas", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {e}", ephemeral=True)
        
@bot.tree.command(name="add_asset", description="Ajouter un asset à trader")
@app_commands.describe(
    ticker="Ticker TradingView (ex: SOLUSDT.P)",
    channel="Mention du channel Discord dédié (ex: #sol)"
)
async def add_asset_cmd(interaction: discord.Interaction, ticker: str, channel: discord.TextChannel = None):
    from risk_manager import add_asset
    ch_id = channel.id if channel else 0
    result = add_asset(ticker.upper(), ch_id)
    coin = result["coin"]
    ch_txt = channel.mention if channel else "channel par défaut"
    embed = discord.Embed(title="✅ Asset ajouté", color=0x00e676)
    embed.add_field(name="Ticker",   value=ticker.upper(), inline=True)
    embed.add_field(name="Coin",     value=coin,           inline=True)
    embed.add_field(name="Channel",  value=ch_txt,         inline=True)
    embed.set_footer(text="Utilise /toggle_asset pour activer/désactiver")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="remove_asset", description="Supprimer un asset de la liste")
@app_commands.describe(coin="Nom du coin (ex: SOL)")
async def remove_asset_cmd(interaction: discord.Interaction, coin: str):
    from risk_manager import remove_asset
    remove_asset(coin.upper())
    await interaction.response.send_message(
        f"🗑️ **{coin.upper()}** supprimé de la liste des assets", ephemeral=True
    )


@bot.tree.command(name="assets", description="Voir tous les assets configurés")
async def list_assets(interaction: discord.Interaction):
    from config_manager import load
    cfg = load()
    assets   = cfg.get("assets", {})
    channels = cfg.get("asset_channels", {})
    if not assets:
        await interaction.response.send_message("📭 Aucun asset configuré", ephemeral=True)
        return
    embed = discord.Embed(title="📡  Assets configurés", color=0x7289da)
    for coin, active in assets.items():
        ch_id  = channels.get(coin, 0) or ASSET_CHANNELS.get(coin, 0)
        ch_txt = f"<#{ch_id}>" if ch_id else "canal par défaut"
        embed.add_field(
            name=f"{'✅' if active else '❌'} {coin}",
            value=ch_txt,
            inline=True
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)
