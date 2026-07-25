import { Navigate, Outlet } from "react-router-dom";
import { roleHome, useAuth } from "../auth/AuthContext";

export function ProtectedRoute({ roles }: { roles?: Array<"PATIENT" | "STAFF" | "ADMIN"> }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="shell">
        <div className="skeleton" style={{ width: "40%" }} />
        <div className="skeleton" style={{ width: "80%" }} />
        <div className="skeleton" style={{ width: "60%" }} />
      </div>
    );
  }

  if (!user) return <Navigate to="/" replace />;
  if (roles && !roles.includes(user.role)) {
    return <Navigate to={roleHome(user.role)} replace />;
  }
  return <Outlet />;
}
