# Webapp Integration Notes

The React/Vite dashboard is imported from the uploaded paper-trading build.

- The root Python runtime (intraday_bot/) remains authoritative for trading, risk, paper execution and EOD learning.
- The React app is isolated under webapp/ to avoid replacing the tested Python runtime with an incompatible TypeScript server.
- Live Dhan order submission remains disabled.
- The intended deployment target is GitHub Actions for scheduled paper cycles plus a free cloud-hosted web frontend/PWA.
- Direct dashboard-to-Python API integration is a separate engineering step.

## Mandatory EOD learning

After each market session, identify profitable moves across the eligible universe, compare them with decision-time evidence, classify the miss reason, and aggregate repeated cross-symbol failure patterns. Single-stock events are observations only and cannot create stock-specific strategy rules. Protective capital, liquidity, risk and capacity gates remain protected. Analysis Error Score and Opportunity Miss Score remain separate.
