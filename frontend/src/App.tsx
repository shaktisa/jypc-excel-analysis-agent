import { useCallback, useEffect, useState } from "react";

import {
  api,
  type Analysis,
  type Preview,
  type ToolInfo,
  type WorkbookMeta,
} from "./api";
import AnalysisPanel from "./components/AnalysisPanel";
import ChartBuilder from "./components/ChartBuilder";
import ChatPanel from "./components/ChatPanel";
import PreviewTable from "./components/PreviewTable";
import UploadPanel from "./components/UploadPanel";
import VersionList from "./components/VersionList";

type Tab = "preview" | "analysis" | "chart";

export default function App() {
  const [meta, setMeta] = useState<WorkbookMeta | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [sheet, setSheet] = useState<string>("");
  const [tab, setTab] = useState<Tab>("preview");
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .tools()
      .then((response) => setTools(response.tools))
      .catch(() => setTools([]));
  }, []);

  const loadPreview = useCallback(
    async (fileId: string, target?: string) => {
      try {
        const data = await api.preview(fileId, target);
        setPreview(data);
        setSheet(data.sheet);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [],
  );

  const loadAnalysis = useCallback(async (fileId: string) => {
    try {
      setAnalysis(await api.analysis(fileId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refreshMeta = useCallback(async () => {
    if (!meta) return;
    try {
      setMeta(await api.workbook(meta.file_id));
      await loadPreview(meta.file_id, sheet);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [meta, sheet, loadPreview]);

  function onUploaded(uploaded: WorkbookMeta) {
    setError(null);
    setMeta(uploaded);
    setAnalysis(null);
    setPreview(null);
    setTab("preview");
    void loadPreview(uploaded.file_id);
    void loadAnalysis(uploaded.file_id);
  }

  return (
    <>
      <header className="app-header">
        <h1>Excel Analysis Agent</h1>
        <span className="tag">P0 · analyse &amp; chart</span>
        <div className="spacer" />
        <span style={{ fontSize: 12, opacity: 0.75 }}>
          {tools.length} tools available to the agent
        </span>
      </header>

      <main>
        <div>
          <UploadPanel onUploaded={onUploaded} onError={setError} />

          {error && <div className="alert error">{error}</div>}

          {meta && (
            <section className="panel">
              <h2>
                2 · {meta.filename} <span className="pill">v{meta.latest_version}</span>
              </h2>
              <div className="tabs">
                <button
                  className={tab === "preview" ? "active" : ""}
                  onClick={() => setTab("preview")}
                >
                  Preview
                </button>
                <button
                  className={tab === "analysis" ? "active" : ""}
                  onClick={() => setTab("analysis")}
                >
                  Analysis
                </button>
                <button className={tab === "chart" ? "active" : ""} onClick={() => setTab("chart")}>
                  Chart
                </button>
              </div>

              {tab === "preview" && (
                <PreviewTable
                  preview={preview}
                  onSheetChange={(name) => void loadPreview(meta.file_id, name)}
                />
              )}
              {tab === "analysis" && <AnalysisPanel analysis={analysis} />}
              {tab === "chart" && (
                <ChartBuilder
                  fileId={meta.file_id}
                  sheet={sheet}
                  onVersionCreated={() => void refreshMeta()}
                  onError={setError}
                />
              )}
            </section>
          )}
        </div>

        <div>
          {meta ? (
            <>
              <ChatPanel fileId={meta.file_id} onVersionCreated={() => void refreshMeta()} />
              <VersionList meta={meta} />
            </>
          ) : (
            <section className="panel">
              <h2>How it works</h2>
              <p className="hint">
                Upload a spreadsheet to start. The backend parses it deterministically, profiles
                every column, and exposes a small set of audited tools. The language model can only
                reach your data by calling those tools — it never sees or edits the file directly.
              </p>
              <ul style={{ fontSize: 13, color: "#475569", paddingLeft: 18, lineHeight: 1.7 }}>
                <li>Immutable version history with download at any point</li>
                <li>Native Excel charts plus a browser PNG preview</li>
                <li>Keyless Azure auth — managed identity, no secrets anywhere</li>
              </ul>
            </section>
          )}
        </div>
      </main>
    </>
  );
}
