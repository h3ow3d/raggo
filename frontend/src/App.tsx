import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import AddLog from "./pages/AddLog";
import VectorSearch from "./pages/VectorSearch";
import AgentChat from "./pages/AgentChat";

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">rag-flight-lab</div>
        <div className="brand-sub">flight-log investigation</div>
        <nav className="nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/logs/new">Add Flight Log</NavLink>
          <NavLink to="/search">Vector Search</NavLink>
          <NavLink to="/chat">Agent Chat</NavLink>
        </nav>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/logs/new" element={<AddLog />} />
          <Route path="/search" element={<VectorSearch />} />
          <Route path="/chat" element={<AgentChat />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
