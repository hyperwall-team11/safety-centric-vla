import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { ProposalDetail, ProposalList } from "./pages/Proposals";
import { Hazards } from "./pages/Hazards";
import { SiteWork } from "./pages/SiteWork";
import { EStop } from "./pages/EStop";
import { Logs } from "./pages/Logs";
import { StateStreamProvider } from "./hooks/useStateStream";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StateStreamProvider>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="tasks/new" element={<Navigate to="/site" replace />} />
          <Route path="proposals" element={<ProposalList />} />
          <Route path="proposals/:taskId" element={<ProposalDetail />} />
          <Route path="hazards" element={<Hazards />} />
          <Route path="site" element={<SiteWork />} />
          <Route path="estop" element={<EStop />} />
          <Route path="logs" element={<Logs />} />
        </Route>
      </Routes>
    </BrowserRouter>
    </StateStreamProvider>
  </StrictMode>,
);
