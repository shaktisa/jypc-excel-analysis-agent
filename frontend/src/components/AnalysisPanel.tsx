import type { Analysis } from "../api";

interface Props {
  analysis: Analysis | null;
}

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

export default function AnalysisPanel({ analysis }: Props) {
  if (!analysis) return <p className="spinner">Profiling the workbook…</p>;

  return (
    <>
      <p className="hint">
        Computed deterministically with pandas — no model involved, so these numbers are
        reproducible and always available even if the LLM endpoint is down.
      </p>
      {analysis.profiles.map((profile) => (
        <div key={profile.sheet} style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>
            {profile.sheet}{" "}
            <span className="pill muted">
              {number.format(profile.row_count)} rows × {profile.columns.length} cols
            </span>
          </h3>
          {profile.columns.length === 0 ? (
            <p className="hint">This sheet has no tabular data.</p>
          ) : (
            <div className="stat-grid">
              {profile.columns.map((column) => (
                <div className="stat" key={column.name}>
                  <div className="name" title={column.name}>
                    {column.name}
                  </div>
                  <div className="meta">
                    <span className="pill muted">{column.inferred_type}</span>
                    <br />
                    {number.format(column.non_null)} values · {number.format(column.nulls)} blank ·{" "}
                    {number.format(column.distinct)} distinct
                    {column.stats && (
                      <>
                        <br />
                        sum {number.format(column.stats.sum)}
                        <br />
                        mean {number.format(column.stats.mean)} · median{" "}
                        {number.format(column.stats.median)}
                        <br />
                        min {number.format(column.stats.min)} · max{" "}
                        {number.format(column.stats.max)}
                      </>
                    )}
                    {column.top_values && column.top_values.length > 0 && (
                      <>
                        <br />
                        top: {column.top_values.map((t) => `${t.value} (${t.count})`).join(", ")}
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  );
}
