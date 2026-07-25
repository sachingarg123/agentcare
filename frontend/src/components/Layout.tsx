import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { roleHome, useAuth } from "../auth/AuthContext";
import { BrandWordmark } from "./Brand";
import { ThemeToggle } from "./ThemeToggle";

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  return (
    <div className="shell shell-alive">
      <div className="topbar">
        <Link className="brand-link" to={roleHome(user.role)} aria-label="PulseDesk home">
          <BrandWordmark size="sm" />
        </Link>
        <div className="nav-links">
          {user.role === "PATIENT" && (
            <NavLink to="/patient" className={({ isActive }) => (isActive ? "active" : undefined)}>
              Dashboard
            </NavLink>
          )}
          {(user.role === "STAFF" || user.role === "ADMIN") && (
            <NavLink
              to="/staff"
              end
              className={({ isActive }) => (isActive ? "active" : undefined)}
            >
              Staff
            </NavLink>
          )}
          {user.role === "ADMIN" && (
            <NavLink
              to="/staff/admin"
              className={({ isActive }) => (isActive ? "active" : undefined)}
            >
              Admin
            </NavLink>
          )}
          <span className="muted">
            {user.name} · {user.role}
          </span>
          <ThemeToggle compact />
          <button
            type="button"
            className="secondary"
            style={{ padding: "0.35rem 0.7rem" }}
            onClick={() => {
              logout();
              navigate("/", { replace: true });
            }}
          >
            Log out
          </button>
        </div>
      </div>
      <Outlet />
    </div>
  );
}
