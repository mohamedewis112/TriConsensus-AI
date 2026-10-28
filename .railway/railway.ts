import {
  defineRailway,
  github,
  preserve,
  project,
  service,
} from "railway/iac";

export default defineRailway(() => {
  const collector = service("v6-shadow-collector", {
    source: github("mohamedewis112/TriConsensus-AI", {
      branch: "feat/v1-foundation",
    }),
    start: "python -m triconsensus.v6_shadow_collector",
    replicas: 1,
    env: {
      PYTHONPATH: "/app/backend",
      RAILWAY_DOCKERFILE_PATH: "deploy/v6-shadow-collector.Dockerfile",
      TRADING_MODE: "paper",
      V6_MARL_OBSERVE: "true",
      V6_MARL_ENABLED: "false",
      V6_MARL_SHADOW_MODE: "true",
      V6_IV_POLL_SECONDS: "300",
      SUPABASE_URL: "https://mhrrbnufkiveynbasyiy.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: preserve(),
      BYBIT_BASE_URL: "https://api.bybit.com",
      BYBIT_WS_URL: "wss://stream.bybit.com/v5/public/linear",
      BINANCE_WS_URL: "wss://fstream.binance.com/public/ws",
      V6_MICROSTRUCTURE_PERSIST_INTERVAL_MS: "60000",
      V6_MICROSTRUCTURE_LATEST_INTERVAL_MS: "1000",
    },
  });

  return project("TriConsensus-AI", {
    resources: [collector],
  });
});
