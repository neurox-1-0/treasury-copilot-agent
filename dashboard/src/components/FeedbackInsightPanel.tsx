/**
 * dashboard/src/components/FeedbackInsightPanel.tsx
 * ===================================================
 *
 * Sidebar / drawer showing the agent's adaptive feedback loop in action.
 *
 * Purpose
 * -------
 * This is the most compelling UI element for a B2B demo.  It makes the
 * agent's learning visible: the analyst can see that their past rejections
 * are shaping future proposals.
 *
 * Data source
 * -----------
 * Fetches from ``GET /api/feedback/insights`` on mount and every 60 s.
 *
 * What it shows
 * -------------
 * - A bar chart (recharts ``BarChart``) of approved / rejected / modified
 *   counts over the last 30 days.
 * - Detected rejection patterns as plain-language chips.
 * - Approval rate as a headline number.
 *
 * Props
 * -----
 * onClose:  Called when the user dismisses the panel.
 */

import React, { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FeedbackInsights } from "../types";

interface FeedbackInsightPanelProps {
  onClose: () => void;
}

export const FeedbackInsightPanel: React.FC<FeedbackInsightPanelProps> = ({
  onClose,
}) => {
  const [insights, setInsights] = useState<FeedbackInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchInsights = async () => {
      try {
        const resp = await fetch("/api/feedback/insights");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: FeedbackInsights = await resp.json();
        if (!cancelled) setInsights(data);
      } catch {
        if (!cancelled) setError("Could not load feedback insights.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchInsights();
    const interval = setInterval(fetchInsights, 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const chartData = insights
    ? [
        { name: "Approved", value: insights.last_30_days.approved, fill: "#10b981" },
        { name: "Modified", value: insights.last_30_days.modified, fill: "#f59e0b" },
        { name: "Rejected", value: insights.last_30_days.rejected, fill: "#ef4444" },
      ]
    : [];

  const approvalPct = insights
    ? Math.round(insights.last_30_days.approval_rate * 100)
    : 0;

  return (
    <aside
      className="feedback-panel"
      aria-label="Feedback insights"
      role="complementary"
    >
      {/* Header */}
      <div className="feedback-panel__header">
        <div>
          <h2 className="feedback-panel__title">Feedback Loop</h2>
          <p className="feedback-panel__subtitle">Last 30 days · Agent is learning</p>
        </div>
        <button
          className="feedback-panel__close"
          onClick={onClose}
          aria-label="Close feedback panel"
        >
          ✕
        </button>
      </div>

      {loading && (
        <div className="feedback-panel__loading">Loading insights…</div>
      )}

      {error && (
        <div className="feedback-panel__error" role="alert">{error}</div>
      )}

      {insights && !loading && (
        <>
          {/* Approval rate headline */}
          <div className="feedback-panel__headline">
            <span className="headline__rate">{approvalPct}%</span>
            <span className="headline__label">approval rate</span>
            <span className="headline__total">
              ({insights.last_30_days.total_proposals} proposals)
            </span>
          </div>

          {/* Bar chart */}
          <div className="feedback-panel__chart" aria-label="Decision breakdown chart">
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={chartData} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                <XAxis
                  dataKey="name"
                  tick={{ fill: "#94a3b8", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fill: "#94a3b8", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 8,
                  }}
                  labelStyle={{ color: "#f8fafc" }}
                  itemStyle={{ color: "#cbd5e1" }}
                />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell key={index} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Rejection patterns */}
          {insights.rejection_patterns.length > 0 && (
            <div className="feedback-panel__patterns">
              <h3 className="patterns__title">Detected Patterns</h3>
              {insights.rejection_patterns.map((p, i) => (
                <div key={i} className="pattern-card">
                  <p className="pattern-card__pattern">
                    <span className="pattern-card__icon">📊</span>
                    {p.pattern}
                  </p>
                  <p className="pattern-card__adaptation">
                    <span className="pattern-card__icon">🤖</span>
                    {p.agent_adaptation}
                  </p>
                </div>
              ))}
            </div>
          )}

          {insights.rejection_patterns.length === 0 && (
            <p className="feedback-panel__no-patterns">
              No significant patterns detected yet. Keep reviewing proposals to
              help the agent learn your preferences.
            </p>
          )}

          {/* B2B coming soon hints */}
          <div className="feedback-panel__coming-soon">
            <h4 className="coming-soon__title">Coming Soon</h4>
            <ul className="coming-soon__list">
              <li>Role-based approval (CFO &gt; LKR 5M)</li>
              <li>Multi-approver workflow</li>
              <li>Regulatory calendar overlay</li>
            </ul>
          </div>
        </>
      )}
    </aside>
  );
};
