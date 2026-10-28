import {getMarket} from "@/lib/api";
import LiveMarketClient from "./LiveMarketClient";

export const dynamic = "force-dynamic";

export default async function LiveMarket() {
  const [btc, eth] = await Promise.all([getMarket("BTCUSDT"), getMarket("ETHUSDT")]);
  return <LiveMarketClient btc={btc} eth={eth} />;
}
