import { useState } from "react";

import { api, type ChartResponse } from "../api";

interface Props {
  fileId: string;
  sheet: string;
  onVersionCreated: () => void;
  onError: (message: string) => void;
}

const CHART_TYPES = ["column", "bar", "line", "area", "pie", "scatter"];

export default function ChartBuilder({ fileId, sheet, onVersionCreated, onError }: Props) {
  const [chartType, setChartType] = useState("column");
  const [dataRange, setDataRange] = useState("");
  const [categories, setCategories] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ChartResponse | null>(null);

  async function build() {
    if (!dataRange.trim()) {
      onError("Enter a data range such as C1:C9 (include the header row).");
      return;
    }
    setBusy(true);
    try {
      const response = await api.chart(fileId, {
        sheet,
        chart_type: chartType,
        data_range: dataRange.trim(),
        categories_range: categories.trim() || null,
        title: title.trim() || null,
      });
      setResult(response);
      onVersionCreated();
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="hint">
        Writes a native Excel chart into a <strong>new version</strong> of the workbook and returns
        a matching PNG preview rendered from the same cells.
      </p>
      <div className="row">
        <div style={{ maxWidth: 130 }}>
          <label htmlFor="chart-type">Type</label>
          <select
            id="chart-type"
            value={chartType}
            onChange={(event) => setChartType(event.target.value)}
          >
            {CHART_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="data-range">Data range</label>
          <input
            id="data-range"
            placeholder="C1:C9"
            value={dataRange}
            onChange={(event) => setDataRange(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="cat-range">Categories (optional)</label>
          <input
            id="cat-range"
            placeholder="A2:A9"
            value={categories}
            onChange={(event) => setCategories(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="chart-title">Title (optional)</label>
          <input
            id="chart-title"
            placeholder="Revenue by region"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div style={{ flex: "none" }}>
          <button onClick={() => void build()} disabled={busy}>
            {busy ? "Building…" : "Generate chart"}
          </button>
        </div>
      </div>

      {result && (
        <>
          <img className="chart-image" src={result.chart_image} alt="Chart preview" />
          <p className="hint" style={{ marginTop: 8 }}>
            Saved as version {result.workbook.latest_version}.{" "}
            <a className="download" href={api.downloadUrl(fileId)}>
              Download the updated workbook
            </a>
          </p>
        </>
      )}
    </>
  );
}
