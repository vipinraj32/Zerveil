import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { Logo } from "./Logo";
import { useStreamConnected } from "../live";
import { Icon } from "../pages/landing/parts";
import { useMe, useSession } from "../session";
import { Address, Avatar } from "./ui";

const NAV = {
  advertiser: [
    { to: "/advertiser", label: "Dashboard", icon: "home", end: true },
    { to: "/advertiser/campaigns", label: "Campaigns", icon: "ledger" },
    { to: "/advertiser/new", label: "New campaign", icon: "plus" },
    { to: "/advertiser/creators", label: "Creators", icon: "users" },
    { to: "/advertiser/agent", label: "My agent", icon: "bot" },
    { to: "/advertiser/payments", label: "Payments", icon: "wallet" },
    { to: "/receipts", label: "Receipts", icon: "ledger" },
  ],
  influencer: [
    { to: "/influencer", label: "Overview", icon: "home", end: true },
    { to: "/influencer/deals", label: "My deals", icon: "ledger" },
    { to: "/influencer/agent", label: "My agent", icon: "bot" },
    { to: "/influencer/profile", label: "My profile", icon: "users" },
    { to: "/influencer/payments", label: "Payments", icon: "wallet" },
    { to: "/receipts", label: "Receipts", icon: "ledger" },
  ],
};

export function Layout() {
  const { user, is_admin } = useMe();
  const { signOut, status } = useSession();
  const live = useStreamConnected();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const home = user.role === "advertiser" ? "/advertiser" : "/influencer";

  // close the mobile drawer whenever the page changes
  useEffect(() => {
    setOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={`app ${open ? "app-open" : ""}`}>
      <aside className="sidebar" id="app-sidebar">
        <NavLink to={home} className="sidebar-brand" aria-label="Zerveil dashboard">
          <Logo size={30} />
        </NavLink>
        <nav className="sidebar-nav" aria-label="Main">
          <p className="sidebar-label">{user.role === "advertiser" ? "Advertiser" : "Creator"}</p>
          {NAV[user.role].map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <Icon name={n.icon} size={20} />
              {n.label}
            </NavLink>
          ))}
          {is_admin && (
            <>
              <p className="sidebar-label">Operator</p>
              <NavLink to="/admin/payments">
                <Icon name="shield" size={20} />
                Payments (read-only)
              </NavLink>
            </>
          )}
          <p className="sidebar-label">Settings</p>
          <NavLink to="/account">
            <Icon name="user" size={20} />
            Account
          </NavLink>
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-live">
            <span className={`live-dot ${live ? "on" : "off"}`} />
            {live ? "Live updates on" : "Reconnecting…"}
          </div>
          <div className="sidebar-user">
            <Avatar src={user.picture} name={user.name} size={36} />
            <div>
              <strong>{user.name}</strong>
              {user.zcash_address ? <Address value={user.zcash_address} /> : <span className="muted small">No Zcash address yet</span>}
            </div>
          </div>
          <button
            type="button"
            className="sidebar-signout"
            onClick={async () => {
              await signOut();
              navigate("/");
            }}
          >
            <Icon name="logout" size={18} />
            Sign out
          </button>
        </div>
      </aside>
      <button type="button" className="sidebar-scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setOpen(false)} />

      <div className="app-main">
        <header className="mobilebar">
          <button type="button" className="mobilebar-btn" aria-expanded={open} aria-controls="app-sidebar" onClick={() => setOpen(!open)}>
            <Icon name={open ? "close" : "menu"} size={22} />
            <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
          </button>
          <NavLink to={home} aria-label="Zerveil dashboard">
            <Logo size={26} />
          </NavLink>
          <Avatar src={user.picture} name={user.name} size={30} />
        </header>
        <div className="netbar" role="status">
          {status?.zcash.testnet_notice ? (
            <span className="testnet-chip testnet-notice" title={status.zcash.testnet_notice}>
              Prices in USD · Zcash testnet · no real monetary value
            </span>
          ) : (
            <span className="testnet-chip">Prices in USD · Zcash {status?.zcash.network ?? "testnet"}</span>
          )}
          <span className="testnet-chip">Direct payments · Zerveil holds no funds</span>
          {status?.payments_simulated && <span className="testnet-chip warn">Simulated — no real payments</span>}
        </div>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
