import { useRef, useState } from "react";

import { api, type Structure, type WorkbookMeta } from "../api";

interface Props {
  onUploaded: (meta: WorkbookMeta, structure: Structure) => void;
  onError: (message: string) => void;
}

const ACCEPT = ".xlsx,.xlsm,.csv,.tsv";

export default function UploadPanel({ onUploaded, onError }: Props) {
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function send(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const result = await api.upload(file);
      const { structure, ...meta } = result;
      onUploaded(meta, structure);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <section className="panel">
      <h2>1 · Upload a spreadsheet</h2>
      <p className="hint">
        .xlsx, .xlsm, .csv or .tsv up to 25&nbsp;MB. Every upload is stored as an immutable
        version&nbsp;1 so nothing is ever overwritten.
      </p>
      <div
        className={`dropzone${dragging ? " active" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void send(event.dataTransfer.files[0]);
        }}
      >
        {busy ? "Uploading and parsing…" : "Drop a spreadsheet here, or click to browse"}
      </div>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(event) => void send(event.target.files?.[0])}
      />
    </section>
  );
}
