import { useRef, useState, type DragEvent, type ChangeEvent } from "react";

type Props = {
  multiple?: boolean;
  files: File[];
  onChange: (files: File[]) => void;
  label?: string;
};

export function FileDrop({ multiple = false, files, onChange, label }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);

  function applyList(list: FileList | null) {
    if (!list) return;
    const next = Array.from(list);
    onChange(multiple ? [...files, ...next] : next.slice(0, 1));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setActive(false);
    applyList(e.dataTransfer.files);
  }

  function onDragOver(e: DragEvent) {
    e.preventDefault();
    setActive(true);
  }

  return (
    <div>
      {label && <div className="muted" style={{ marginBottom: "0.35rem" }}>{label}</div>}
      <div
        className={`file-drop ${active ? "active" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={() => setActive(false)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <strong>Drop files here</strong> or click to browse
        <input
          ref={inputRef}
          type="file"
          multiple={multiple}
          hidden
          onChange={(e: ChangeEvent<HTMLInputElement>) => applyList(e.target.files)}
        />
      </div>
      {files.length > 0 && (
        <ul className="muted" style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem" }}>
          {files.map((f) => (
            <li key={f.name + f.size}>{f.name}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
