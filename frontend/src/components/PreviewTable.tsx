import type { Preview } from "../api";

interface Props {
  preview: Preview | null;
  onSheetChange: (sheet: string) => void;
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export default function PreviewTable({ preview, onSheetChange }: Props) {
  if (!preview) return <p className="spinner">Loading preview…</p>;

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <div style={{ maxWidth: 240 }}>
          <label htmlFor="sheet-select">Sheet</label>
          <select
            id="sheet-select"
            value={preview.sheet}
            onChange={(event) => onSheetChange(event.target.value)}
          >
            {preview.sheets.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <div style={{ flex: "none", paddingBottom: 6 }}>
          <span className="pill muted">{preview.range}</span>{" "}
          {preview.truncated && <span className="pill">preview truncated</span>}
        </div>
      </div>

      <div className="grid-scroll">
        <table>
          <thead>
            <tr>
              <th className="rownum" />
              {preview.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.values.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <td className="rownum">{preview.start_row + rowIndex}</td>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex}>{render(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
