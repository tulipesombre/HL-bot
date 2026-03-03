require('dotenv').config();

module.exports = {
  hyperliquid: {
    apiKey: process.env.HYPERLIQUID_API_KEY,
    privateKey: process.env.HYPERLIQUID_PRIVATE_KEY,
    testnet: process.env.HYPERLIQUID_TESTNET === 'true',
    baseUrl: process.env.HYPERLIQUID_TESTNET === 'true' 
      ? 'https://api.hyperliquid-testnet.xyz'
      : 'https://api.hyperliquid.xyz'
  },
  discord: {
    webhookUrl: process.env.DISCORD_WEBHOOK_URL || ''
  },
  trading: {
    riskPercent: 5,
    tpMultiplier: 2,
    checkIntervalMs: 60000,
    setupDeadlineMin: 121
  }
};
