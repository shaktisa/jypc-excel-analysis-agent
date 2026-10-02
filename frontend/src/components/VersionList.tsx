import { api, type WorkbookMeta } from "../api";

interface Props {
  meta: WorkbookMeta;
}

export default function VersionList({ meta }: Props) {
  const versions = [...meta.versions].sort((a, b) => b.version - a.version);
  return (
    <section className="panel">
      <h2>Version history</h2>
      <p className="hint">
        Versions are immutable: every chart or edit appends a new one, so you can always download
        an earlier state of <strong>{meta.filename}</strong>.
      </p>
      <ul className="versions">
        {versions.map((version) => (
          <li key={version.version}>
            <span className="pill">v{version.version}</span>
            <span className="note">
              {version.note} · {new Date(version.created_at).toLocaleString()} ·{" "}
              {Math.round(version.size_bytes / 1024)} KB
            </span>
            <a className="download" href={api.downloadUrl(meta.file_id, version.version)}>
              Download
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
