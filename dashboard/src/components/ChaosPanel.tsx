/**
 * dashboard/src/components/ChaosPanel.tsx
 * =========================================
 *
 * Developer / demo tool for toggling failure simulation modes on mock
 * services without touching config files.
 *
 * What it does
 * ------------
 * Renders four toggle groups (ERP Timeout, Bank Write Failure, Low Forecast
 * Confidence, Infeasible Optimiser).  Each toggle calls
 * ``POST /api/chaos`` which proxies to the relevant mock service.
 *
 * Design note
 * -----------
 * This page is useful during a scripted failure-scenario demo.  It is
 * accessible at ``/chaos`` but not linked in the main nav to avoid
 * confusion for end users.  Demo operators know the route.
 */

import React, { useState } from "react";
import type { ChaosMode, ChaosService } from "../types";

interface ChaosToggle {
  label: string;
  description: string;
  service: ChaosService;
  mode: ChaosMode;
}

const CHAOS_TOGGLES: ChaosToggle[] = [
  {
    label: "ERP Timeout",
    description: "Makes the ERP mock return a 504 timeout on all requests.",
    service: "erp-mock",
    mode: "timeout",
  },
  {
    label: "Bank Auth Failure",
    description: "Makes the Bank mock return 401 Unauthorized on API calls.",
    service: "bank-mock",
    mode: "auth_failure",
  },
  {
    label: "Bank Write Failure",
    description: "Makes payment initiation calls return 500 Internal Server Error.",
    service: "bank-mock",
    mode: "write_failure",
  },
];

interface ToggleState {
  active: boolean;
  status: "idle" | "loading" | "success" | "error";
  message: string;
}

type TogglesState = Record<string, ToggleState>;

const initialState = (): TogglesState =>
  Object.fromEntries(
    CHAOS_TOGGLES.map((t) => [
      t.label,
      { active: false, status: "idle", message: "" },
    ])
  );

export const ChaosPanel: React.FC = () => {
  const [toggles, setToggles] = useState<TogglesState>(initialState);

  const handleToggle = async (toggle: ChaosToggle) => {
    const current = toggles[toggle.label];
    const newActive = !current.active;
    const newMode: ChaosMode = newActive ? toggle.mode : "none";

    setToggles((prev) => ({
      ...prev,
      [toggle.label]: { ...prev[toggle.label], status: "loading", message: "" },
    }));

    try {
      const resp = await fetch("/api/chaos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service: toggle.service, mode: newMode }),
      });
      const data = await resp.json();

      setToggles((prev) => ({
        ...prev,
        [toggle.label]: {
          active: newActive,
          status: "success",
          message:
            data.upstream_status === 503
              ? "⚠ Service not reachable — mode noted."
              : `✓ ${newActive ? "Enabled" : "Disabled"}`,
        },
      }));
    } catch {
      setToggles((prev) => ({
        ...prev,
        [toggle.label]: {
          ...prev[toggle.label],
          status: "error",
          message: "Network error",
        },
      }));
    }
  };

  const resetAll = async () => {
    for (const toggle of CHAOS_TOGGLES) {
      await fetch("/api/chaos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service: toggle.service, mode: "none" }),
      }).catch(() => {});
    }
    setToggles(initialState());
  };

  return (
    <div className="chaos-panel">
      <div className="chaos-panel__header">
        <div>
          <h1 className="chaos-panel__title">⚡ Chaos Panel</h1>
          <p className="chaos-panel__subtitle">
            Developer / demo tool — toggle failure modes on mock services to
            run scripted failure scenarios without editing config files.
          </p>
        </div>
        <button className="btn btn--ghost" onClick={resetAll}>
          Reset All
        </button>
      </div>

      <div className="chaos-panel__warning" role="alert">
        ⚠ These toggles directly affect the mock services. Do not enable during
        live demos unless scripted.
      </div>

      <div className="chaos-panel__grid">
        {CHAOS_TOGGLES.map((toggle) => {
          const state = toggles[toggle.label];
          return (
            <div
              key={toggle.label}
              className={`chaos-card ${state.active ? "chaos-card--active" : ""}`}
            >
              <div className="chaos-card__top">
                <div>
                  <h3 className="chaos-card__label">{toggle.label}</h3>
                  <p className="chaos-card__description">{toggle.description}</p>
                  <p className="chaos-card__service">
                    Target: <code>{toggle.service}</code> → mode:{" "}
                    <code>{toggle.mode}</code>
                  </p>
                </div>
                <button
                  id={`chaos-toggle-${toggle.label.replace(/\s/g, "-").toLowerCase()}`}
                  className={`chaos-toggle ${state.active ? "chaos-toggle--on" : ""}`}
                  onClick={() => handleToggle(toggle)}
                  disabled={state.status === "loading"}
                  aria-pressed={state.active}
                  aria-label={`Toggle ${toggle.label}`}
                >
                  <span className="chaos-toggle__thumb" />
                </button>
              </div>

              {state.message && (
                <p
                  className={`chaos-card__feedback chaos-card__feedback--${state.status}`}
                >
                  {state.message}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="chaos-panel__table-wrap">
        <h2 className="chaos-panel__table-title">Reference: All Failure Modes</h2>
        <table className="chaos-table">
          <thead>
            <tr>
              <th>Toggle</th>
              <th>Service</th>
              <th>Mode</th>
              <th>Effect</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>ERP Timeout</td>
              <td><code>erp-mock</code></td>
              <td><code>timeout</code></td>
              <td>Agent receives stale ERP data, surfaces STALE_DATA flag</td>
            </tr>
            <tr>
              <td>Bank Auth Failure</td>
              <td><code>bank-mock</code></td>
              <td><code>auth_failure</code></td>
              <td>Bank balance fetch fails; agent may block execution</td>
            </tr>
            <tr>
              <td>Bank Write Failure</td>
              <td><code>bank-mock</code></td>
              <td><code>write_failure</code></td>
              <td>Payment initiation fails; audit log records FAILED status</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};
