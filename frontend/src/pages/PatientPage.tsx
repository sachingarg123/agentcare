import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { FileDrop } from "../components/FileDrop";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";

type Appointment = {
  id: string;
  status: string;
  reason?: string | null;
  doctor_name?: string | null;
  start_time?: string | null;
  end_time?: string | null;
};

function formatWhen(start?: string | null, end?: string | null): string {
  if (!start) return "—";
  const s = new Date(start);
  const e = end ? new Date(end) : null;
  const date = s.toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const startT = s.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const endT = e
    ? e.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : null;
  return endT ? `${date} · ${startT}–${endT}` : `${date} · ${startT}`;
}
type Document = { id: string; document_type: string; file_path: string };
type Reminder = {
  id: string;
  reminder_type: string;
  scheduled_at?: string | null;
  status: string;
};

export function PatientPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [rawRequest, setRawRequest] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");

  const appts = useQuery({
    queryKey: ["appointments"],
    queryFn: () => api<Appointment[]>("/appointments"),
  });
  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: () => api<Document[]>("/documents"),
  });
  const rems = useQuery({
    queryKey: ["reminders"],
    queryFn: () => api<Reminder[]>("/reminders"),
  });

  const submit = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("raw_request", rawRequest);
      for (const f of files) fd.append("files", f);
      const token = localStorage.getItem("agentcare_token");
      const res = await fetch("/api/v1/requests", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      return body as { workflow_run_id: string };
    },
    onSuccess: (body) => {
      toast.push("Request started", "success");
      navigate(`/patient/workflows/${body.workflow_run_id}`);
    },
    onError: (err: Error) => setError(err.message),
  });

  const cancelAppt = useMutation({
    mutationFn: (id: string) =>
      api(`/appointments/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      toast.push("Appointment cancelled", "success");
      qc.invalidateQueries({ queryKey: ["appointments"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  const uploadDoc = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const token = localStorage.getItem("agentcare_token");
      const res = await fetch("/api/v1/documents/upload", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      return body;
    },
    onSuccess: () => {
      toast.push("Document uploaded", "success");
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    submit.mutate();
  }

  return (
    <>
      <header className="page-intro">
        <h1>Your care desk</h1>
        <p className="muted">
          Submit an administrative request, track appointments, and manage documents.
        </p>
      </header>

      <section className="panel panel-primary">
        <h2>Start a request</h2>
        <p className="muted">
          Describe booking, documents, or hospital navigation in plain language. Clinical questions go
          to staff — this system does not diagnose or prescribe.
        </p>
        <form className="stack" style={{ marginTop: "1rem" }} onSubmit={onSubmit}>
          <label>
            What do you need?
            <textarea
              required
              value={rawRequest}
              onChange={(e) => setRawRequest(e.target.value)}
              placeholder="Example: I need a cardiology follow-up next week and want to attach my old ECG."
            />
          </label>
          <FileDrop multiple files={files} onChange={setFiles} label="Attach files (optional)" />
          <div className="row">
            <button type="submit" disabled={submit.isPending}>
              {submit.isPending && <span className="spinner" />}
              Submit request
            </button>
          </div>
          {error && <p className="err">{error}</p>}
        </form>
      </section>

      <section className="panel">
        <h2>Appointments</h2>
        {appts.isLoading && <div className="skeleton" />}
        {appts.isError && <p className="err">{(appts.error as Error).message}</p>}
        {appts.data &&
          (appts.data.length === 0 ? (
            <p className="empty">No appointments yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Doctor</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {appts.data.map((a) => (
                  <tr key={a.id}>
                    <td>{formatWhen(a.start_time, a.end_time)}</td>
                    <td>{a.doctor_name || "—"}</td>
                    <td>
                      <StatusBadge status={a.status} />
                    </td>
                    <td>{a.reason || "—"}</td>
                    <td>
                      {a.status !== "CANCELLED" && (
                        <button
                          type="button"
                          className="secondary"
                          disabled={cancelAppt.isPending}
                          onClick={() => {
                            if (confirm("Cancel this appointment?")) cancelAppt.mutate(a.id);
                          }}
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section className="panel">
        <h2>Documents</h2>
        <div className="row" style={{ marginBottom: "0.75rem" }}>
          <input
            type="file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadDoc.mutate(f);
              e.target.value = "";
            }}
          />
          {uploadDoc.isPending && <span className="muted">Uploading…</span>}
        </div>
        {docs.isLoading && <div className="skeleton" />}
        {docs.data &&
          (docs.data.length === 0 ? (
            <p className="empty">No documents yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>File</th>
                </tr>
              </thead>
              <tbody>
                {docs.data.map((d) => (
                  <tr key={d.id}>
                    <td>{d.document_type}</td>
                    <td className="muted">{d.file_path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section className="panel">
        <h2>Reminders</h2>
        {rems.isLoading && <div className="skeleton" />}
        {rems.data &&
          (rems.data.length === 0 ? (
            <p className="empty">No reminders yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>When</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rems.data.map((r) => (
                  <tr key={r.id}>
                    <td>{r.reminder_type}</td>
                    <td>{r.scheduled_at || "—"}</td>
                    <td>
                      <StatusBadge status={r.status} />
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
