/**
 * dashboard/src/App.tsx
 * =======================
 *
 * Root application component.
 *
 * Routing
 * -------
 * Uses React Router v6.  Routes:
 * - ``/``       → Pending Approvals (main HITL screen)
 * - ``/audit``  → Audit Trail
 * - ``/chaos``  → Chaos Panel (developer/demo tool)
 *
 * Navigation
 * ----------
 * Top nav bar with:
 * - Product name + logo icon
 * - Nav links (Approvals, Audit Trail)
 * - Live pending proposal count badge
 * - Chaos Panel link (labelled as a developer tool)
 *
 * The pending count is shown in the nav so the analyst can see new proposals
 * from any page without navigating away.
 */

import React, { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ChaosPanel } from "./components/ChaosPanel";
import { AuditTrail } from "./pages/AuditTrail";
import { Pending } from "./pages/Pending";

function usePendingCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const fetchCount = async () => {
      try {
        const resp = await fetch("/api/proposals?status=PENDING");
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled) setCount(data.proposals?.length ?? 0);
      } catch {
        // Silently ignore — nav badge is non-critical
      }
    };

    fetchCount();
    const interval = setInterval(fetchCount, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return count;
}

export const App: React.FC = () => {
  const pendingCount = usePendingCount();
  const location = useLocation();

  return (
    <div className="app">
      {/* ── Top navigation ──────────────────────────────────── */}
      <header className="app-nav" role="banner">
        <Link to="/" className="app-nav__brand">
          <span className="app-nav__logo">🏦</span>
          <span className="app-nav__name">Treasury Copilot</span>
          <span className="app-nav__tag">HITL Dashboard</span>
        </Link>

        <nav className="app-nav__links" aria-label="Main navigation">
          <NavLink
            to="/"
            className={({ isActive }) =>
              `app-nav__link ${isActive ? "app-nav__link--active" : ""}`
            }
            end
          >
            Approvals
            {pendingCount > 0 && (
              <span className="nav-badge" aria-label={`${pendingCount} pending`}>
                {pendingCount}
              </span>
            )}
          </NavLink>

          <NavLink
            to="/audit"
            className={({ isActive }) =>
              `app-nav__link ${isActive ? "app-nav__link--active" : ""}`
            }
          >
            Audit Trail
          </NavLink>

          <NavLink
            to="/chaos"
            className={({ isActive }) =>
              `app-nav__link app-nav__link--dev ${isActive ? "app-nav__link--active" : ""}`
            }
            title="Developer / demo tool"
          >
            ⚡ Chaos
          </NavLink>
        </nav>
      </header>

      {/* ── Page content ─────────────────────────────────────── */}
      <main className="app-content" id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Pending />} />
          <Route path="/audit" element={<AuditTrail />} />
          <Route path="/chaos" element={<ChaosPanel />} />
        </Routes>
      </main>
    </div>
  );
};
