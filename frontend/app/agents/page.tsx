const agents = [
  ["01", "Market Intelligence", "Regime · structure · momentum · volatility · liquidity"],
  ["02", "Trade Strategist", "LONG / SHORT / WAIT using deterministic price levels only"],
  ["03", "Risk & Devil's Advocate", "Attempts to veto every weak or conflicting setup"],
];

export default function Agents() {
  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">LANGGRAPH ORCHESTRATION</p><h1>Agents</h1></div>
      </header>
      <section className="agentGrid">
        {agents.map(([id, name, desc]) => (
          <article className="panel agentCard" key={id}>
            <span className="agentId">{id}</span>
            <h2>{name}</h2>
            <p>{desc}</p>
            <div className="agentRule">Failure → NO TRADE</div>
          </article>
        ))}
      </section>
    </div>
  );
}
