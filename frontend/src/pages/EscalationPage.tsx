import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";

type Department = { id: string; name: string; active: boolean };

type EscalationDetail = {
  id: string;
  workflow_run_id: string;
  reason: string;
  status: string;
  reviewed_by?: string | null;
  created_at?: string | null;
  patient_id?: string | null;
  patient_name?: string | null;
  patient_email?: string | null;
  workflow_status?: string | null;
  current_step?: string | null;
  raw_request?: string | null;
  hitl_source?: string | null;
  hitl_reason?: string | null;
  administrative_intents?: string[] | null;
  safety_result?: Record<string, unknown> | null;
  routing_result?: Record<string, unknown> | null;
  appointment_result?: Record<string, unknown> | null;
  uploaded_files?: Array<{ filename?: string; mime_type?: string; size?: number }> | null;
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ marginBottom: "0.85rem" }}>
      <div className="muted" style={{ fontSize: "0.8rem", fontWeight: 600, marginBottom: "0.25rem" }}>
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}

export function EscalationPage() {
  const { escalationId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [error, setError] = useState("");

  const detail = useQuery({
    queryKey: ["escalation", escalationId],
    queryFn: () => api<EscalationDetail>(`/staff/escalations/${escalationId}`),
    enabled: !!escalationId,
  });

  const depts = useQuery({
    queryKey: ["departments"],
    queryFn: () => api<Department[]>("/staff/departments"),
  });

  const esc = detail.data;

  useEffect(() => {
    if (!esc) return;
    const suggested =
      (esc.routing_result?.department_id as string | undefined) ||
      (typeof esc.routing_result?.department_name === "string"
        ? depts.data?.find((d) => d.name === esc.routing_result?.department_name)?.id
        : undefined);
    if (suggested && !departmentId) setDepartmentId(suggested);
  }, [esc, depts.data, departmentId]);

  const resolve = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      api(`/staff/escalations/${escalationId}/resolve`, {
        method: "POST",
        json: {
          decision,
          note: note.trim() || undefined,
          department_id: departmentId || undefined,
          department_name: depts.data?.find((d) => d.id === departmentId)?.name,
        },
      }),
    onSuccess: (_data, decision) => {
      toast.push(`Escalation ${decision}d`, "success");
      qc.invalidateQueries({ queryKey: ["escalations"] });
      qc.invalidateQueries({ queryKey: ["escalation", escalationId] });
      qc.invalidateQueries({ queryKey: ["staff-requests"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
      navigate("/staff");
    },
    onError: (err: Error) => setError(err.message),
  });

  function onSubmit(e: FormEvent, decision: "approve" | "reject") {
    e.preventDefault();
    setError("");
    if (decision === "approve" && esc?.hitl_source === "routing" && !departmentId) {
      setError("Pick a department before approving a routing escalation.");
      return;
    }
    resolve.mutate(decision);
  }

  if (detail.isLoading) {
    return (
      <>
        <div className="skeleton" style={{ width: "40%" }} />
        <div className="skeleton" style={{ width: "80%" }} />
        <div className="skeleton" style={{ width: "60%" }} />
      </>
    );
  }

  if (detail.isError || !esc) {
    return (
      <>
        <p className="err">
          {(detail.error as Error | null)?.message || "Escalation not found."}
        </p>
        <Link to="/staff">← Back to staff</Link>
      </>
    );
  }

  const pending = esc.status === "PENDING";
  const routing = esc.routing_result || {};
  const safety = esc.safety_result || {};

  return (
    <>
      <header className="page-intro">
        <h1>Review escalation</h1>
        <p className="muted">
          <StatusBadge status={esc.status} />
          {esc.hitl_source && (
            <>
              {" "}
              · source <strong>{esc.hitl_source}</strong>
            </>
          )}
          {" · "}
          workflow{" "}
          <Link to={`/patient/workflows/${esc.workflow_run_id}`}>
            <code>{esc.workflow_run_id.slice(0, 8)}…</code>
          </Link>
        </p>
      </header>

      <section className="panel panel-primary">
        <h2>Patient request</h2>
        <Field label="Patient">
          {esc.patient_name || "—"}
          {esc.patient_email && (
            <span className="muted">
              {" "}
              · {esc.patient_email}
            </span>
          )}
        </Field>
        <Field label="What they entered">
          {esc.raw_request ? (
            <blockquote className="request-quote">{esc.raw_request}</blockquote>
          ) : (
            <p className="empty">
              No request text stored on this older run. Ask the patient to resubmit, or open the
              workflow timeline.
            </p>
          )}
        </Field>
        {esc.uploaded_files && esc.uploaded_files.length > 0 && (
          <Field label="Attachments">
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {esc.uploaded_files.map((f, i) => (
                <li key={`${f.filename}-${i}`}>
                  {f.filename || "file"}
                  {f.mime_type ? ` (${f.mime_type})` : ""}
                </li>
              ))}
            </ul>
          </Field>
        )}
      </section>

      <section className="panel">
        <h2>Why it paused</h2>
        <Field label="Escalation reason">
          <p style={{ margin: 0 }}>{esc.hitl_reason || esc.reason}</p>
        </Field>
        {esc.workflow_status && (
          <Field label="Workflow">
            <StatusBadge status={esc.workflow_status} /> · step {esc.current_step || "—"}
          </Field>
        )}
        {esc.administrative_intents && esc.administrative_intents.length > 0 && (
          <Field label="Detected intents">
            {esc.administrative_intents.join(", ")}
          </Field>
        )}
        {Object.keys(routing).length > 0 && (
          <Field label="Routing">
            <ul className="muted" style={{ margin: 0, paddingLeft: "1.1rem" }}>
              <li>Department: {(routing.department_name as string) || "—"}</li>
              <li>Confidence: {String(routing.confidence ?? "—")}</li>
              <li>Reason: {(routing.reason as string) || "—"}</li>
            </ul>
          </Field>
        )}
        {Object.keys(safety).length > 0 && (
          <Field label="Safety">
            <ul className="muted" style={{ margin: 0, paddingLeft: "1.1rem" }}>
              <li>Safe: {String(safety.safe ?? "—")}</li>
              <li>Blocked: {String(safety.blocked ?? "—")}</li>
              <li>
                {(safety.reason as string) ||
                  (safety.message as string) ||
                  (safety.safe_alternative as string) ||
                  "—"}
              </li>
            </ul>
          </Field>
        )}
        {esc.appointment_result && (
          <Field label="Appointment attempt">
            <pre className="json">{JSON.stringify(esc.appointment_result, null, 2)}</pre>
          </Field>
        )}
        <p className="muted">Created {esc.created_at || "—"}</p>
        {esc.reviewed_by && <p className="muted">Reviewed by {esc.reviewed_by}</p>}
      </section>

      {pending ? (
        <section className="panel panel-primary">
          <h2>Decision</h2>
          <p className="muted">
            Use the patient text and routing hints above. For low-confidence routing, choose the
            correct department then approve.
          </p>
          <form className="stack" style={{ marginTop: "0.75rem" }} onSubmit={(e) => onSubmit(e, "approve")}>
            <label>
              Note <span className="optional">(optional)</span>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Context for the resume decision…"
              />
            </label>
            <label>
              Department{" "}
              {esc.hitl_source === "routing" ? (
                <span className="optional">(required to approve routing)</span>
              ) : (
                <span className="optional">(optional override)</span>
              )}
              <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">— select department —</option>
                {(depts.data || [])
                  .filter((d) => d.active)
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
              </select>
            </label>
            <div className="row">
              <button type="submit" disabled={resolve.isPending}>
                {resolve.isPending && <span className="spinner" />}
                Approve & resume
              </button>
              <button
                type="button"
                className="danger"
                disabled={resolve.isPending}
                onClick={(e) => onSubmit(e, "reject")}
              >
                Reject & resume
              </button>
              <Link to="/staff" className="muted">
                Cancel
              </Link>
            </div>
            {error && <p className="err">{error}</p>}
          </form>
        </section>
      ) : (
        <p className="empty">This escalation is already resolved.</p>
      )}

      <p>
        <Link to="/staff">← Back to staff</Link>
      </p>
    </>
  );
}
