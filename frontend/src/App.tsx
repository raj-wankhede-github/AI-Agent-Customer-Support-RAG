import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AuthProvider, homePathFor, useAuth } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { EmptyState, FullPageSpinner } from "./components/ui";
import { ChatPage } from "./features/chat/ChatPage";
import { LoginPage } from "./pages/LoginPage";

const AdminLayout = lazy(() => import("./features/admin/AdminLayout"));
const DashboardPage = lazy(() => import("./features/admin/DashboardPage"));
const HandoffsPage = lazy(() => import("./features/admin/HandoffsPage"));
const ConversationsPage = lazy(() => import("./features/admin/ConversationsPage"));
const AdminConversationPage = lazy(() => import("./features/admin/AdminConversationPage"));
const DocumentsPage = lazy(() => import("./features/admin/DocumentsPage"));
const DocumentDetailPage = lazy(() => import("./features/admin/DocumentDetailPage"));
const RetrievalDebugPage = lazy(() => import("./features/admin/RetrievalDebugPage"));

function Home() {
  const { user, loading } = useAuth();
  if (loading) return <FullPageSpinner />;
  return <Navigate to={user ? homePathFor(user) : "/login"} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Suspense fallback={<FullPageSpinner />}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/chat" element={<RequireAuth><ChatPage /></RequireAuth>} />
            <Route path="/chat/:conversationId" element={<RequireAuth><ChatPage /></RequireAuth>} />
            <Route path="/admin" element={<RequireAuth roles={["ADMIN", "AGENT"]}><AdminLayout /></RequireAuth>}>
              <Route index element={<DashboardPage />} />
              <Route path="handoffs" element={<HandoffsPage />} />
              <Route path="conversations" element={<ConversationsPage />} />
              <Route path="conversations/:conversationId" element={<AdminConversationPage />} />
              <Route path="documents" element={<RequireAuth roles={["ADMIN"]}><DocumentsPage /></RequireAuth>} />
              <Route path="documents/:documentId" element={<RequireAuth roles={["ADMIN"]}><DocumentDetailPage /></RequireAuth>} />
              <Route path="retrieval" element={<RequireAuth roles={["ADMIN"]}><RetrievalDebugPage /></RequireAuth>} />
            </Route>
            <Route path="*" element={<EmptyState icon="search" title="Page not found" description="The page you're looking for doesn't exist." />} />
          </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  );
}
