import { FormEvent, useState } from 'react';
import { api, QueryEvidence, QueryResponse } from '../api';

interface ChatTurn {
  question: string;
  response?: QueryResponse;
  error?: string;
  loading: boolean;
}

function EvidenceItem({ ev }: { ev: QueryEvidence }) {
  const known = new Set(['type', 'id', 'message']);
  const extras = Object.entries(ev).filter(([k]) => !known.has(k));
  return (
    <div className="result">
      <div className="result-meta">
        <span>{ev.type}</span>
        {ev.id !== undefined && ev.id !== null && <span>id: {String(ev.id)}</span>}
        {extras.map(([k, v]) => (
          <span key={k}>
            {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
          </span>
        ))}
      </div>
      {ev.message && <div className="result-message">{ev.message}</div>}
    </div>
  );
}

export default function AgentChat() {
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<ChatTurn[]>([]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q) return;
    setQuestion('');
    const idx = turns.length;
    setTurns((prev) => [...prev, { question: q, loading: true }]);
    try {
      const response = await api.query(q);
      setTurns((prev) => prev.map((t, i) => (i === idx ? { ...t, response, loading: false } : t)));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setTurns((prev) =>
        prev.map((t, i) => (i === idx ? { ...t, error: message, loading: false } : t))
      );
    }
  }

  return (
    <>
      <h1>Agent Chat</h1>
      <div className="muted" style={{ marginBottom: 12 }}>
        The agent runs entirely on the backend. The frontend never calls the local generation or
        embedding models directly.
      </div>

      <div className="chat">
        {turns.length === 0 && (
          <div className="card muted">
            Try: <em>“Were there any recent engine-related safety issues?”</em> or{' '}
            <em>“Which airports are most associated with delays?”</em>
          </div>
        )}
        {turns.map((turn, i) => (
          <div key={i}>
            <div className="chat-message user">
              <div className="chat-role">You</div>
              <div>{turn.question}</div>
            </div>
            <div className="chat-message agent">
              <div className="chat-role">Agent</div>
              {turn.loading && <div className="muted">Thinking…</div>}
              {turn.error && <div className="error">{turn.error}</div>}
              {turn.response && (
                <>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{turn.response.answer}</div>

                  <h2 style={{ fontSize: 13, marginTop: 14 }}>
                    Evidence ({turn.response.evidence.length})
                  </h2>
                  {turn.response.evidence.length === 0 ? (
                    <div className="muted">No evidence retrieved.</div>
                  ) : (
                    <div className="result-list">
                      {turn.response.evidence.map((ev, j) => (
                        <EvidenceItem key={j} ev={ev} />
                      ))}
                    </div>
                  )}

                  <h2 style={{ fontSize: 13, marginTop: 14 }}>Agent trace</h2>
                  <pre className="trace">{JSON.stringify(turn.response.agent_trace, null, 2)}</pre>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      <form className="chat-form" onSubmit={handleSubmit}>
        <input
          type="text"
          placeholder="Ask the agent a question…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={!question.trim()}>
          Ask
        </button>
      </form>
    </>
  );
}
