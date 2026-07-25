import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StatusBadge } from "../components/StatusBadge";

type Escalation = {
  id: string;
  workflow_run_id: string;
  reason: string;
  status: string;
  created_at?: string | null;
  patient_name?: string | null;
  raw_request_preview?: string | null;
  hitl_source?: string | null;
};

type Workflow = {
  id: string;
  patient_id: string;
  status: string;
  current_step?: string | null;
  hitl_required?: boolean | null;
  hitl_reason?: string | null;
};

type Audit = {
  id: string;
  actor_id: string;
  action: string;
  entity_type: string;
  entity_id?: string | null;
  created_at?: string | null;
};

export function StaffPage() {
  const { user } = useAuth();
  const [pendingOnly, setPendingOnly] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");

  const escalations = useQuery({
    queryKey: ["escalations", pendingOnly],
    queryFn: () =>
      api<Escalation[]>(`/staff/escalations?pending_only=${pendingOnly}`),
  });

  const requests = useQuery({
    queryKey: ["staff-requests", statusFilter],
    queryFn: () => {
      const q = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
      return api<Workflow[]>(`/staff/requests${q}`);
    },
  });

  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<Audit[]>("/staff/audit?limit=40"),
  });

  return (
    <>
      <header className="page-intro">
        <h1>Staff desk</h1>
        <p className="muted">Review escalations, browse workflow runs, and check the audit trail.</p>
      </header>

      {user?.role === "ADMIN" && (
        <section className="panel panel-primary">
          <h2>Reference data</h2>
          <p className="muted">
            Add departments, doctors, and appointment slots on the Admin page (not here).
          </p>
          <p style={{ marginTop: "0.75rem" }}>
            <Link to="/staff/admin">Go to Admin →</Link>
          </p>
        </section>
      )}

      <section className="panel panel-primary">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Escalations</h2>
          <label className="row" style={{ margin: 0, color: "inherit" }}>
            <input
              type="checkbox"
              checked={pendingOnly}
              onChange={(e) => setPendingOnly(e.target.checked)}
              style={{ width: "auto" }}
            />
            Pending only
          </label>
        </div>
        {escalations.isLoading && <div className="skeleton" />}
        {escalations.isError && <p className="err">{(escalations.error as Error).message}</p>}
        {escalations.data &&
          (escalations.data.length === 0 ? (
            <p className="empty">No escalations in this filter.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Patient / request</th>
                  <th>Why paused</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {escalations.data.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <StatusBadge status={e.status} />
                      {e.hitl_source && (
                        <div className="muted" style={{ fontSize: "0.8rem" }}>
                          {e.hitl_source}
                        </div>
                      )}
                    </td>
                    <td>
                      <div>{e.patient_name || "Patient"}</div>
                      <div className="muted" style={{ fontSize: "0.88rem" }}>
                        {e.raw_request_preview || "—"}
                      </div>
                    </td>
                    <td>{e.reason}</td>
                    <td className="muted">{e.created_at || "—"}</td>
                    <td>
                      <Link to={`/staff/escalations/${e.id}`}>Review</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Workflow runs</h2>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{ width: "auto", minWidth: 160 }}
          >
            <option value="">All statuses</option>
            <option value="RUNNING">RUNNING</option>
            <option value="WAITING_HITL">WAITING_HITL</option>
            <option value="COMPLETED">COMPLETED</option>
            <option value="FAILED">FAILED</option>
          </select>
        </div>
        {requests.isLoading && <div className="skeleton" />}
        {requests.isError && <p className="err">{(requests.error as Error).message}</p>}
        {requests.data &&
          (requests.data.length === 0 ? (
            <p className="empty">No workflow runs.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Step</th>
                  <th>HITL</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {requests.data.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <code>{w.id.slice(0, 8)}…</code>
                    </td>
                    <td>
                      <StatusBadge status={w.status} />
                    </td>
                    <td>{w.current_step || "—"}</td>
                    <td>{w.hitl_required ? w.hitl_reason || "yes" : "—"}</td>
                    <td>
                      <Link to={`/patient/workflows/${w.id}`}>Open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section className="panel">
        <h2>Audit trail</h2>
        {audit.isLoading && <div className="skeleton" />}
        {audit.data &&
          (audit.data.length === 0 ? (
            <p className="empty">No audit events yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Action</th>
                  <th>Entity</th>
                </tr>
              </thead>
              <tbody>
                {audit.data.map((a) => (
                  <tr key={a.id}>
                    <td className="muted">{a.created_at || "—"}</td>
                    <td>{a.action}</td>
                    <td>
                      {a.entity_type}
                      {a.entity_id ? (
                        <>
                          {" "}
                          <code>{a.entity_id.slice(0, 8)}…</code>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>
    </>
  );
}
