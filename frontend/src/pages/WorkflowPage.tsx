import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, wsUrl } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StatusBadge } from "../components/StatusBadge";

type WorkflowSummary = {
  id: string;
  status: string;
  current_step?: string | null;
  confirmation?: Record<string, unknown> | null;
};

type Step = { label: string; detail: string; at: string };

export function WorkflowPage() {
  const { workflowId = "" } = useParams();
  const { user } = useAuth();
  const [steps, setSteps] = useState<Step[]>([]);

  const q = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api<WorkflowSummary>(`/requests/${workflowId}`),
    enabled: !!workflowId,
    refetchInterval: 8000,
  });

  useEffect(() => {
    if (!workflowId) return;
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl(workflowId));
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as {
          type?: string;
          current_step?: string;
          status?: string;
        };
        if (msg.type === "ping") return;
        setSteps((prev) => [
          ...prev,
          {
            label: msg.type || "event",
            detail: msg.current_step || msg.status || "",
            at: new Date().toLocaleTimeString(),
          },
        ]);
        if (["completed", "interrupted", "snapshot", "started"].includes(msg.type || "")) {
          q.refetch();
        }
      };
    } catch {
      /* ignore */
    }
    return () => ws?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  useEffect(() => {
    if (q.data && steps.length === 0) {
      setSteps([
        {
          label: "Loaded",
          detail: `status=${q.data.status}`,
          at: new Date().toLocaleTimeString(),
        },
      ]);
    }
  }, [q.data, steps.length]);

  const back = user?.role === "PATIENT" ? "/patient" : "/staff";
  const conf = q.data?.confirmation as { ok?: boolean } | null | undefined;
  const confOk = Boolean(conf && (conf.ok === true || conf.ok === undefined));

  return (
    <>
      <header className="page-intro">
        <h1>Workflow</h1>
        <p className="muted">
          <code>{workflowId}</code>
          {q.data && (
            <>
              {" "}
              · <StatusBadge status={q.data.status} /> · step{" "}
              <strong>{q.data.current_step || "—"}</strong>
            </>
          )}
        </p>
      </header>

      <section className="panel">
        <h2>Progress</h2>
        <ul className="timeline">
          {steps.length === 0 ? (
            <li className="empty">Waiting for events…</li>
          ) : (
            steps.map((s, i) => (
              <li key={`${s.at}-${i}`}>
                <strong>{s.label}</strong> <span className="muted">{s.at}</span>
                {s.detail && <div className="muted">{s.detail}</div>}
              </li>
            ))
          )}
        </ul>
      </section>

      <section className="panel">
        <h2>Confirmation</h2>
        {!conf ? (
          <p className="empty">No confirmation yet.</p>
        ) : (
          <>
            <p className={conf.ok === false ? "err" : "ok"}>
              {conf.ok === false ? "Not completed" : confOk ? "Confirmation payload" : "Confirmation"}
            </p>
            <pre className="json">{JSON.stringify(conf, null, 2)}</pre>
          </>
        )}
      </section>

      <p>
        <Link to={back}>← Back</Link>
      </p>
    </>
  );
}
