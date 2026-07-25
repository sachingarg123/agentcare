import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";

type Department = {
  id: string;
  name: string;
  description?: string | null;
  active: boolean;
};
type Doctor = { id: string; department_id: string; name: string; active: boolean };
type Slot = {
  id: string;
  doctor_id: string;
  start_time: string;
  end_time: string;
  status: string;
};

function formatSlotRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const day = s.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const st = s.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const et = e.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${day} · ${st}–${et}`;
}

export function AdminPage() {
  const toast = useToast();
  const qc = useQueryClient();

  const [deptName, setDeptName] = useState("");
  const [deptDesc, setDeptDesc] = useState("");
  const [docName, setDocName] = useState("");
  const [docDept, setDocDept] = useState("");
  const [slotDoctor, setSlotDoctor] = useState("");
  const [slotStart, setSlotStart] = useState("");
  const [slotEnd, setSlotEnd] = useState("");

  const depts = useQuery({
    queryKey: ["departments"],
    queryFn: () => api<Department[]>("/staff/departments"),
  });
  const doctors = useQuery({
    queryKey: ["doctors"],
    queryFn: () => api<Doctor[]>("/staff/doctors"),
  });
  const slots = useQuery({
    queryKey: ["slots"],
    queryFn: () => api<Slot[]>("/staff/slots?limit=500"),
  });

  const slotsByDoctor = useMemo(() => {
    const list = slots.data || [];
    const docList = doctors.data || [];
    const deptList = depts.data || [];
    const byId = new Map(docList.map((d) => [d.id, d]));

    const groups = new Map<
      string,
      {
        doctorId: string;
        doctorName: string;
        departmentName: string;
        active: boolean;
        slots: Slot[];
        available: number;
        booked: number;
      }
    >();

    for (const s of list) {
      const doc = byId.get(s.doctor_id);
      const key = s.doctor_id;
      if (!groups.has(key)) {
        groups.set(key, {
          doctorId: key,
          doctorName: doc?.name || `Doctor ${key.slice(0, 8)}…`,
          departmentName:
            deptList.find((d) => d.id === doc?.department_id)?.name || "—",
          active: doc?.active ?? true,
          slots: [],
          available: 0,
          booked: 0,
        });
      }
      const g = groups.get(key)!;
      g.slots.push(s);
      if (s.status === "AVAILABLE") g.available += 1;
      else if (s.status === "BOOKED") g.booked += 1;
    }

    // Include doctors with zero slots so admin can see empty schedules
    for (const d of docList) {
      if (!groups.has(d.id)) {
        groups.set(d.id, {
          doctorId: d.id,
          doctorName: d.name,
          departmentName: deptList.find((x) => x.id === d.department_id)?.name || "—",
          active: d.active,
          slots: [],
          available: 0,
          booked: 0,
        });
      }
    }

    const ordered = Array.from(groups.values()).sort((a, b) =>
      a.doctorName.localeCompare(b.doctorName),
    );
    for (const g of ordered) {
      g.slots.sort(
        (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
      );
    }
    return ordered;
  }, [slots.data, doctors.data, depts.data]);

  const createDept = useMutation({
    mutationFn: () =>
      api("/staff/departments", {
        method: "POST",
        json: { name: deptName.trim(), description: deptDesc.trim() || undefined },
      }),
    onSuccess: () => {
      toast.push("Department created", "success");
      setDeptName("");
      setDeptDesc("");
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  const createDoctor = useMutation({
    mutationFn: () =>
      api("/staff/doctors", {
        method: "POST",
        json: { name: docName.trim(), department_id: docDept },
      }),
    onSuccess: () => {
      toast.push("Doctor created", "success");
      setDocName("");
      qc.invalidateQueries({ queryKey: ["doctors"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  const createSlot = useMutation({
    mutationFn: () =>
      api("/staff/slots", {
        method: "POST",
        json: {
          doctor_id: slotDoctor,
          start_time: new Date(slotStart).toISOString(),
          end_time: new Date(slotEnd).toISOString(),
          status: "AVAILABLE",
        },
      }),
    onSuccess: () => {
      toast.push("Slot created", "success");
      setSlotStart("");
      setSlotEnd("");
      qc.invalidateQueries({ queryKey: ["slots"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  const toggleDept = useMutation({
    mutationFn: (d: Department) =>
      api(`/staff/departments/${d.id}`, {
        method: "PATCH",
        json: { active: !d.active },
      }),
    onSuccess: () => {
      toast.push("Department updated", "success");
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
    onError: (err: Error) => toast.push(err.message, "error"),
  });

  function onDept(e: FormEvent) {
    e.preventDefault();
    createDept.mutate();
  }
  function onDoc(e: FormEvent) {
    e.preventDefault();
    createDoctor.mutate();
  }
  function onSlot(e: FormEvent) {
    e.preventDefault();
    createSlot.mutate();
  }

  return (
    <>
      <header className="page-intro">
        <h1>Admin</h1>
        <p className="muted">
          Manage hospital reference data: departments → doctors → slots. (Login accounts for STAFF
          users are seeded; this page does not create staff logins.)
        </p>
        <nav className="row" style={{ marginTop: "0.75rem", gap: "1rem" }}>
          <a href="#departments">Departments</a>
          <a href="#doctors">Doctors</a>
          <a href="#slots">Slots</a>
        </nav>
      </header>

      {(depts.isError || doctors.isError || slots.isError) && (
        <p className="err">
          {(depts.error as Error | null)?.message ||
            (doctors.error as Error | null)?.message ||
            (slots.error as Error | null)?.message}
        </p>
      )}

      <section id="departments" className="panel panel-primary">
        <h2>Departments</h2>
        <form className="stack" onSubmit={onDept} style={{ marginBottom: "1rem" }}>
          <div className="row">
            <label style={{ flex: 1 }}>
              Name
              <input required value={deptName} onChange={(e) => setDeptName(e.target.value)} />
            </label>
            <label style={{ flex: 1 }}>
              Description
              <input value={deptDesc} onChange={(e) => setDeptDesc(e.target.value)} />
            </label>
          </div>
          <button type="submit" disabled={createDept.isPending}>
            {createDept.isPending && <span className="spinner" />}
            Add department
          </button>
        </form>
        {depts.isLoading && <div className="skeleton" />}
        {depts.data &&
          (depts.data.length === 0 ? (
            <p className="empty">No departments.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Active</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {depts.data.map((d) => (
                  <tr key={d.id}>
                    <td>
                      {d.name}
                      {d.description && <div className="muted">{d.description}</div>}
                    </td>
                    <td>
                      <StatusBadge status={d.active ? "ACTIVE" : "INACTIVE"} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        disabled={toggleDept.isPending}
                        onClick={() => toggleDept.mutate(d)}
                      >
                        {d.active ? "Deactivate" : "Activate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section id="doctors" className="panel">
        <h2>Doctors</h2>
        <p className="muted">
          Hospital doctors used for booking (not PulseDesk STAFF login accounts). Choose a department
          below, then add.
        </p>
        <form className="stack" onSubmit={onDoc} style={{ margin: "0.75rem 0 1rem" }}>
          <div className="row">
            <label style={{ flex: 1 }}>
              Name
              <input required value={docName} onChange={(e) => setDocName(e.target.value)} />
            </label>
            <label style={{ flex: 1 }}>
              Department
              <select required value={docDept} onChange={(e) => setDocDept(e.target.value)}>
                <option value="">Select…</option>
                {(depts.data || [])
                  .filter((d) => d.active)
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <button
            type="submit"
            disabled={createDoctor.isPending || !(depts.data || []).some((d) => d.active)}
          >
            {createDoctor.isPending && <span className="spinner" />}
            Add doctor
          </button>
        </form>
        {doctors.isLoading && <div className="skeleton" />}
        {doctors.data &&
          (doctors.data.length === 0 ? (
            <p className="empty">No doctors.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Department</th>
                  <th>Active</th>
                </tr>
              </thead>
              <tbody>
                {doctors.data.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td className="muted">
                      {depts.data?.find((x) => x.id === d.department_id)?.name || d.department_id.slice(0, 8)}
                    </td>
                    <td>
                      <StatusBadge status={d.active ? "ACTIVE" : "INACTIVE"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>

      <section id="slots" className="panel">
        <h2>Slots by doctor</h2>
        <p className="muted">
          Grouped per doctor, earliest slot first. Use availability to see open vs booked times.
        </p>
        <form className="stack" onSubmit={onSlot} style={{ margin: "0.75rem 0 1.25rem" }}>
          <label>
            Doctor
            <select required value={slotDoctor} onChange={(e) => setSlotDoctor(e.target.value)}>
              <option value="">Select…</option>
              {(doctors.data || []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <label style={{ flex: 1 }}>
              Start
              <input
                type="datetime-local"
                required
                value={slotStart}
                onChange={(e) => setSlotStart(e.target.value)}
              />
            </label>
            <label style={{ flex: 1 }}>
              End
              <input
                type="datetime-local"
                required
                value={slotEnd}
                onChange={(e) => setSlotEnd(e.target.value)}
              />
            </label>
          </div>
          <button type="submit" disabled={createSlot.isPending}>
            {createSlot.isPending && <span className="spinner" />}
            Add slot
          </button>
        </form>

        {slots.isLoading && <div className="skeleton" />}
        {slots.isError && <p className="err">{(slots.error as Error).message}</p>}
        {!slots.isLoading && slotsByDoctor.length === 0 && (
          <p className="empty">No doctors or slots yet.</p>
        )}

        <div className="slot-groups">
          {slotsByDoctor.map((g) => (
            <div key={g.doctorId} className="slot-group">
              <div className="slot-group-head">
                <div>
                  <strong>{g.doctorName}</strong>
                  <span className="muted">
                    {" "}
                    · {g.departmentName}
                    {!g.active && " · inactive"}
                  </span>
                </div>
                <div className="slot-group-counts">
                  <span className="slot-count ok">{g.available} available</span>
                  <span className="slot-count warn">{g.booked} booked</span>
                  <span className="slot-count muted">{g.slots.length} total</span>
                </div>
              </div>
              {g.slots.length === 0 ? (
                <p className="empty" style={{ margin: "0.5rem 0 0" }}>
                  No slots for this doctor yet.
                </p>
              ) : (
                <table className="table slot-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Availability</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.slots.map((s) => (
                      <tr key={s.id}>
                        <td>{formatSlotRange(s.start_time, s.end_time)}</td>
                        <td>
                          <StatusBadge status={s.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
