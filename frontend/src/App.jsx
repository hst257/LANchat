import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import AuthPage from "./pages/AuthPage";
import ChatPage from "./pages/ChatPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import GroupChatPage from "./pages/GroupChatPage";
import ProfilePage from "./pages/ProfilePage";

function LoadingScreen() {
  return (
    <main className="loading-screen">
      <div className="brand-orb"><span>✦</span></div>
      <p>Opening LAN Chat…</p>
    </main>
  );
}

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  return user ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const { user, loading } = useAuth();
  return (
    <Routes>
      <Route
        path="/login"
        element={loading ? <LoadingScreen /> : user ? <Navigate to="/connections" replace /> : <AuthPage />}
      />
      <Route path="/connections" element={<Protected><ConnectionsPage /></Protected>} />
      <Route path="/chat/:contactId" element={<Protected><ChatPage /></Protected>} />
      <Route path="/group/:groupId" element={<Protected><GroupChatPage /></Protected>} />
      <Route path="/profile/:publicId" element={<Protected><ProfilePage /></Protected>} />
      <Route path="/chat" element={<Navigate to="/connections" replace />} />
      <Route path="*" element={<Navigate to={user ? "/connections" : "/login"} replace />} />
    </Routes>
  );
}
