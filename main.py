import os
import asyncio
import threading
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

import hyperliquid_client  # noqa: F401 — force l'import au démarrage pour afficher BASE_URL / mode testnet


def run_flask(loop: asyncio.AbstractEventLoop):
    """Lance Flask dans un thread dédié, partage la loop asyncio avec discord_bot."""
    import discord_bot as db
    db.bot_loop = loop  # Injection de la loop AVANT que Flask commence à recevoir

    from webhook import app
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask démarré sur le port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


async def start():
    import discord_bot as db
    loop = asyncio.get_running_loop()  # ← était get_event_loop()

    flask_thread = threading.Thread(target=run_flask, args=(loop,), daemon=True)
    flask_thread.start()

    token = os.environ["DISCORD_BOT_TOKEN"]
    logger.info("Démarrage du bot Discord…")
    async with db.bot:
        await db.bot.start(token)


if __name__ == "__main__":
    asyncio.run(start())
