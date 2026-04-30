import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api, CreateLogResponse, FlightSummary } from '../api';

const LOG_TYPES = ['maintenance', 'operational', 'weather', 'safety', 'delay', 'other'];
const SOURCE_SYSTEMS = ['ACARS', 'MEL', 'CrewReport', 'OPS', 'ATC', 'Maint'];
const SEVERITIES = ['info', 'warning', 'critical'];

export default function AddLog() {
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [flights, setFlights] = useState<FlightSummary[]>([]);
  const [flightId, setFlightId] = useState<number | ''>('');

  const [logType, setLogType] = useState(LOG_TYPES[0]);
  const [sourceSystem, setSourceSystem] = useState(SOURCE_SYSTEMS[0]);
  const [severity, setSeverity] = useState('info');
  const [message, setMessage] = useState('');
  const [metadataText, setMetadataText] = useState('{}');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreateLogResponse | null>(null);

  // Debounce flight search to avoid hitting the API on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .listFlights(debouncedSearch || undefined, 25)
      .then((r) => {
        if (cancelled) return;
        setFlights(r.results);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedSearch]);

  const metadataError = useMemo(() => {
    if (!metadataText.trim()) return null;
    try {
      const parsed = JSON.parse(metadataText);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return 'Metadata must be a JSON object.';
      }
      return null;
    } catch {
      return 'Metadata is not valid JSON.';
    }
  }, [metadataText]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setCreated(null);
    if (flightId === '') {
      setError('Choose a flight first.');
      return;
    }
    if (metadataError) {
      setError(metadataError);
      return;
    }
    setSubmitting(true);
    try {
      const metadata = metadataText.trim() ? JSON.parse(metadataText) : undefined;
      const result = await api.createLog({
        flight_id: Number(flightId),
        log_type: logType,
        source_system: sourceSystem,
        severity,
        message: message.trim(),
        metadata,
      });
      setCreated(result);
      setMessage('');
      setMetadataText('{}');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <h1>Add Flight Log</h1>
      <form className="card" onSubmit={handleSubmit}>
        <label>Flight (search by number, origin, or destination)</label>
        <input
          type="text"
          placeholder="e.g. AA123 or LHR"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label style={{ marginTop: 8 }}>Flight</label>
        <select
          value={flightId === '' ? '' : String(flightId)}
          onChange={(e) => setFlightId(e.target.value === '' ? '' : Number(e.target.value))}
        >
          <option value="">— select a flight —</option>
          {flights.map((f) => (
            <option key={f.id} value={f.id}>
              #{f.id} · {f.flight_number} · {f.origin} → {f.destination} ·{' '}
              {new Date(f.scheduled_departure).toLocaleString()}
            </option>
          ))}
        </select>

        <div className="row" style={{ marginTop: 8 }}>
          <div>
            <label>Log type</label>
            <select value={logType} onChange={(e) => setLogType(e.target.value)}>
              {LOG_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Source system</label>
            <select value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)}>
              {SOURCE_SYSTEMS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Severity</label>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITIES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </div>

        <label>Message</label>
        <textarea
          rows={4}
          required
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Describe what happened, e.g. 'ENG #2 vibration noted during climb above FL200.'"
        />

        <label>Metadata (JSON object)</label>
        <textarea rows={4} value={metadataText} onChange={(e) => setMetadataText(e.target.value)} />
        {metadataError && <div className="error">{metadataError}</div>}

        {error && <div className="error">{error}</div>}

        <div style={{ marginTop: 12 }}>
          <button type="submit" disabled={submitting || flightId === ''}>
            {submitting ? 'Submitting…' : 'Create log'}
          </button>
        </div>
      </form>

      {created && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Created</h2>
          <div className="muted">
            Log #{created.id} on flight #{created.flight_id} ·{' '}
            <span className={`severity-${created.severity}`}>{created.severity}</span>
          </div>
          <div className="result-message" style={{ marginTop: 6 }}>
            {created.message}
          </div>
          <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
            {created.embedded
              ? 'Embedding generated and stored.'
              : created.embedding_error
                ? `Embedding deferred (${created.embedding_error}). The next ingestion pass will retry.`
                : 'Embedding deferred. The next ingestion pass will retry.'}
          </div>
        </div>
      )}
    </>
  );
}
