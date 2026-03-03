const express = require('express');
const PositionBot = require('./positionBot');
const config = require('./config');

const app = express();
app.use(express.json());

const bot = new PositionBot();

// Recevoir les webhooks Discord
app.post('/webhook/tradingview', (req, res) => {
  const payload = req.body;
  
  if (payload.embeds && payload.embeds.length > 0) {
    const embed = payload.embeds[0];
    
    if (embed.footer?.text?.includes('SETUP_ARMED')) {
      bot.handleDiscordAlert(embed);
    }
  }
  
  res.json({ ok: true });
});

// Status endpoint
app.get('/status', (req, res) => {
  res.json({
    waiting_trades: bot.waitingTrades.length,
    active_trades: Object.keys(bot.activeTrades).length,
    timestamp: new Date()
  });
});

// Lancer les checks
setInterval(() => bot.checkForChod(), config.trading.checkIntervalMs);
setInterval(() => bot.monitorPositions(), 5000);

// Démarrer
const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`🚀 Bot TSR sur http://localhost:${PORT}`);
  console.log(`📨 Webhook: POST http://localhost:${PORT}/webhook/tradingview`);
  console.log(`📊 Status: GET http://localhost:${PORT}/status`);
});
