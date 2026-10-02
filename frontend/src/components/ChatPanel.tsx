import { useEffect, useRef, useState } from "react";

import { api, type ToolCall } from "../api";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
  charts?: string[];
}

interface Props {
  fileId: string;
  onVersionCreated: () => void;
}

const SUGGESTIONS = [
  "Summarise this workbook",
  "Which column drives most of the total?",
  "Chart revenue by region",
];

export default function ChatPanel({ fileId, onVersionCreated }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    setError(null);
  }, [fileId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const response = await api.chat(fileId, message, history);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.reply,
          toolCalls: response.tool_calls,
          charts: response.charts,
        },
      ]);
      if (response.new_version) onVersionCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>Ask the agent</h2>
      <p className="hint">
        The model never touches the file directly — it can only call the audited tools listed
        below, and every call is shown in the trace.
      </p>

      {error && <div className="alert error">{error}</div>}

      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && !busy && (
          <div className="alert info">
            Try one of these: {SUGGESTIONS.map((s) => `“${s}”`).join(" · ")}
          </div>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`bubble ${message.role}`}>
            {message.content}
            {message.toolCalls && message.toolCalls.length > 0 && (
              <div className="tool-trace">
                {message.toolCalls.map((call, callIndex) => (
                  <div key={callIndex}>
                    <span className={call.ok ? "ok" : "bad"}>{call.ok ? "✓" : "✗"}</span>{" "}
                    {call.name}
                    {call.error ? ` — ${call.error}` : ""}
                  </div>
                ))}
              </div>
            )}
            {message.charts?.map((chart, chartIndex) => (
              <img key={chartIndex} className="chart-image" src={chart} alt="Agent chart" />
            ))}
          </div>
        ))}
        {busy && <div className="spinner">Thinking…</div>}
      </div>

      <div className="row">
        <div>
          <input
            placeholder="Ask about this spreadsheet…"
            value={draft}
            disabled={busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void send(draft);
            }}
          />
        </div>
        <div style={{ flex: "none" }}>
          <button onClick={() => void send(draft)} disabled={busy || !draft.trim()}>
            Send
          </button>
        </div>
      </div>
    </section>
  );
}
