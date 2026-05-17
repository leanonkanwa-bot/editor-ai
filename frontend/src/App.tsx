import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Onboarding from "./pages/Onboarding";
import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import VerifyEmail from "./pages/VerifyEmail";
import Admin from "./pages/Admin";

function Guard({ children }: { children: React.ReactNode }) {
  return localStorage.getItem("ll_token") ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/onboarding" element={<Guard><Onboarding /></Guard>} />
        <Route path="/dashboard" element={<Guard><Dashboard /></Guard>} />
        <Route path="/settings" element={<Guard><Settings /></Guard>} />
        <Route path="/verify-email" element={<VerifyEmail />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
