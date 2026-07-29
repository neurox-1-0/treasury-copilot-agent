/**
 * dashboard/src/main.tsx
 * =======================
 *
 * Vite entry point for the HITL Approval Dashboard.
 *
 * Wraps ``<App>`` in ``<BrowserRouter>`` (React Router v6) and renders into
 * ``#root``.  ``StrictMode`` is enabled for development-time warnings.
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
