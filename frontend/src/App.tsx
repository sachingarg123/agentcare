import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./auth/AuthContext";
import { AppLayout } from "./components/Layout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { ToastProvider } from "./components/Toast";
import { AdminPage } from "./pages/AdminPage";
import { EscalationPage } from "./pages/EscalationPage";
import { LoginPage } from "./pages/LoginPage";
import { PatientPage } from "./pages/PatientPage";
import { StaffPage } from "./pages/StaffPage";
import { WorkflowPage } from "./pages/WorkflowPage";
import { ThemeProvider } from "./theme/ThemeContext";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <BrowserRouter>
              <Routes>
                <Route path="/" element={<LoginPage />} />

                <Route element={<ProtectedRoute roles={["PATIENT"]} />}>
                  <Route element={<AppLayout />}>
                    <Route path="/patient" element={<PatientPage />} />
                  </Route>
                </Route>

                <Route element={<ProtectedRoute roles={["PATIENT", "STAFF", "ADMIN"]} />}>
                  <Route element={<AppLayout />}>
                    <Route path="/patient/workflows/:workflowId" element={<WorkflowPage />} />
                  </Route>
                </Route>

                <Route element={<ProtectedRoute roles={["STAFF", "ADMIN"]} />}>
                  <Route element={<AppLayout />}>
                    <Route path="/staff" element={<StaffPage />} />
                    <Route path="/staff/escalations/:escalationId" element={<EscalationPage />} />
                  </Route>
                </Route>

                <Route element={<ProtectedRoute roles={["ADMIN"]} />}>
                  <Route element={<AppLayout />}>
                    <Route path="/staff/admin" element={<AdminPage />} />
                  </Route>
                </Route>

                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </BrowserRouter>
          </ToastProvider>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
