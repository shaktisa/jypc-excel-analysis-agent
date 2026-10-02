/**
 * Thin typed wrapper around the backend API.
 *
 * Every request carries an anonymous, browser-local user id so the telemetry
 * workbook can report distinct users without collecting any personal data.
 */

export interface VersionInfo {
  version: number;
  created_at: string;
  note: string;
  size_bytes: number;
}

export interface WorkbookMeta {
  file_id: string;
  filename: string;
  created_at: string;
  updated_at: string;
  latest_version: number;
  versions: VersionInfo[];
}

export interface SheetInfo {
  name: string;
  rows: number;
  columns: number;
  dimensions: string;
  charts: number;
}

export interface Structure {
  sheets: SheetInfo[];
  active: string;
}

export interface Preview {
  sheet: string;
  range: string;
  columns: string[];
  start_row: number;
  values: (string | number | boolean | null)[][];
  truncated: boolean;
  sheets: string[];
}

export interface ColumnProfile {
  name: string;
  inferred_type: "numeric" | "text";
  non_null: number;
  nulls: number;
  distinct: number;
  stats?: { min: number; max: number; mean: number; median: number; sum: number };
  top_values?: { value: string; count: number }[];
}

export interface SheetProfile {
  sheet: string;
  row_count: number;
  column_count?: number;
  columns: ColumnProfile[];
}

export interface Analysis {
  file_id: string;
  structure: Structure;
  profiles: SheetProfile[];
}

export interface ChartResponse {
  chart: Record<string, unknown>;
  chart_image: string;
  workbook: WorkbookMeta;
}

export interface ToolCall {
  name: string;
  ok: boolean;
  error?: string;
  arguments?: Record<string, unknown>;
}

export interface ChatResponse {
  reply: string;
  tool_calls: ToolCall[];
  charts: string[];
  new_version: number | null;
}

export interface ToolInfo {
  name: string;
  tier: string;
  description: string;
  mutates: boolean;
}

const USER_KEY = "excel-agent-user-id";

export function userId(): string {
  let id = localStorage.getItem(USER_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(USER_KEY, id);
  }
  return id;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "X-User-Id": userId(), ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status-line fallback */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  upload(file: File): Promise<WorkbookMeta & { structure: Structure }> {
    const form = new FormData();
    form.append("file", file);
    return request("/api/workbooks", { method: "POST", body: form });
  },

  listWorkbooks(): Promise<{ workbooks: WorkbookMeta[] }> {
    return request("/api/workbooks");
  },

  workbook(fileId: string): Promise<WorkbookMeta> {
    return request(`/api/workbooks/${fileId}`);
  },

  preview(fileId: string, sheet?: string): Promise<Preview> {
    const query = sheet ? `?sheet=${encodeURIComponent(sheet)}` : "";
    return request(`/api/workbooks/${fileId}/preview${query}`);
  },

  analysis(fileId: string): Promise<Analysis> {
    return request(`/api/workbooks/${fileId}/analysis`);
  },

  chart(fileId: string, body: Record<string, unknown>): Promise<ChartResponse> {
    return request(`/api/workbooks/${fileId}/charts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  chat(
    fileId: string,
    message: string,
    history: { role: string; content: string }[],
  ): Promise<ChatResponse> {
    return request("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: fileId, message, history }),
    });
  },

  tools(): Promise<{ write_tools_enabled: boolean; tools: ToolInfo[] }> {
    return request("/api/tools");
  },

  downloadUrl(fileId: string, version?: number): string {
    const query = version ? `?version=${version}` : "";
    return `/api/workbooks/${fileId}/download${query}`;
  },
};
