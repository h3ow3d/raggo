import { FormEvent, useState } from 'react';
import { api, VectorSearchResponse } from '../api';

const SEVERITY_OPTIONS = ['', 'info', 'warning', 'critical'];
const SOURCE_OPTIONS = ['', 'ACARS', 'MEL', 'CrewReport', 'OPS', 'ATC', 'Maint'];

export default function VectorSearch() {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(10);
  const [severity, setSeverity] = useState('');
  const [sourceSystem, setSourceSystem] = useState('');

  const [response, setResponse] = useState<VectorSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const filters: Record<string, string> = {};
      if (severity) filters.severity = severity;
      if (sourceSystem) filters.source_system = sourceSystem;
      const result = await api.vectorSearch({
        query: query.trim(),
        top_k: topK,
        filters: Object.keys(filters).length ? filters : undefined,
      });
      setResponse(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Vector Search</h1>
      <form className="card" onSubmit={handleSubmit}>
        <label>Query</label>
        <input
          type="text"
          required
          placeholder="e.g. engine vibration during climb"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="row" style={{ marginTop: 8 }}>
          <div>
            <label>Top K</label>
            <input
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            />
          </div>
          <div>
            <label>Severity</label>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s || 'any'} value={s}>
                  {s || 'any'}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Source system</label>
            <select value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)}>
              {SOURCE_OPTIONS.map((s) => (
                <option key={s || 'any'} value={s}>
                  {s || 'any'}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && <div className="error">{error}</div>}
        <div style={{ marginTop: 12 }}>
          <button type="submit" disabled={loading || !query.trim()}>
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>
      </form>

      {response && (
        <>
          <h2>
            Results · {response.results.length} match
            {response.results.length === 1 ? '' : 'es'}
          </h2>
          {response.results.length === 0 && <div className="muted">No matches.</div>}
          <div className="result-list">
            {response.results.map((r) => {
              const m = r.metadata || {};
              const flightNumber = (m.flight_number as string | undefined) ?? null;
              const origin = (m.origin as string | undefined) ?? null;
              const destination = (m.destination as string | undefined) ?? null;
              const logTime = (m.log_time as string | undefined) ?? null;
              const sourceSystem = (m.source_system as string | undefined) ?? null;
              const severity = (m.severity as string | undefined) ?? null;
              return (
                <div key={r.id} className="result">
                  <div className="result-meta">
                    <span>#{r.id}</span>
                    {flightNumber && (
                      <span>
                        flight {flightNumber}
                        {origin && destination ? ` · ${origin} → ${destination}` : ''}
                      </span>
                    )}
                    {logTime && <span>{new Date(logTime).toLocaleString()}</span>}
                    {sourceSystem && <span>{sourceSystem}</span>}
                    {severity && <span className={`severity-${severity}`}>{severity}</span>}
                    <span>
                      similarity:{' '}
                      {r.score !== null && r.score !== undefined ? r.score.toFixed(3) : '—'}
                    </span>
                  </div>
                  <div className="result-message">{r.text}</div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
