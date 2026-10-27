import Link from "next/link";

const links = [
  ["/", "Overview"],
  ["/live", "Live Market"],
  ["/trades", "Trades"],
  ["/agents", "Agents"],
  ["/experiments", "Experiments"],
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brandMark">TC</div>
        <div>
          <strong>TriConsensus</strong>
          <span>AI Trading Research</span>
        </div>
      </div>
      <nav>
        {links.map(([href, label]) => (
          <Link href={href} key={href} className="navItem">
            <span className="navDot" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="safetyBadge">
        <span className="pulseDot" />
        PAPER ONLY
        <small>Live trading disabled</small>
      </div>
    </aside>
  );
}
