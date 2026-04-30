/**
 * Dashboard: API call shape, stat rendering, error surfacing,
 * ingestion trigger button.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Dashboard from '../Dashboard';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockReset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Dashboard', () => {
  it('hits /api/stats and /api/health on mount and renders the values', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(((url: RequestInfo) => {
      const u = String(url);
      if (u === '/api/stats') {
        return Promise.resolve(
          jsonResponse({
            flights: 100,
            flight_logs: 500,
            incidents: 12,
            embedded_logs: 480,
            unembedded_logs: 20,
          })
        );
      }
      if (u === '/api/health') {
        return Promise.resolve(jsonResponse({ status: 'ok', database: 'ok' }));
      }
      return Promise.reject(new Error(`unexpected url ${u}`));
    }) as typeof fetch);

    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('100')).toBeInTheDocument();
      expect(screen.getByText('500')).toBeInTheDocument();
      expect(screen.getByText('12')).toBeInTheDocument();
      expect(screen.getByText('480')).toBeInTheDocument();
      expect(screen.getByText('20')).toBeInTheDocument();
    });

    // Both endpoints were called.
    const urls = fetchSpy.mock.calls.map(([u]) => String(u)).sort();
    expect(urls).toContain('/api/health');
    expect(urls).toContain('/api/stats');
  });

  it('surfaces an error when /api/stats fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(((_url: RequestInfo) =>
      Promise.resolve(jsonResponse({ detail: 'database down' }, 500))) as typeof fetch);

    render(<Dashboard />);
    await waitFor(() => expect(screen.getByText(/database down/i)).toBeInTheDocument());
  });

  it('triggers ingestion when the button is clicked and shows the result', async () => {
    let calls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(((url: RequestInfo, init?: RequestInit) => {
      const u = String(url);
      if (u === '/api/ingest' && init?.method === 'POST') {
        calls += 1;
        return Promise.resolve(jsonResponse({ scanned: 50, embedded: 50, errors: [] }));
      }
      if (u === '/api/health') {
        return Promise.resolve(jsonResponse({ status: 'ok', database: 'ok' }));
      }
      // /api/stats and any other GET fall through to a stable stat payload.
      return Promise.resolve(
        jsonResponse({
          flights: 1,
          flight_logs: 1,
          incidents: 0,
          embedded_logs: 0,
          unembedded_logs: 1,
        })
      );
    }) as typeof fetch);

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Dashboard />);
    await user.click(await screen.findByRole('button', { name: /run ingestion pass/i }));

    await waitFor(() => expect(calls).toBe(1));
    await waitFor(() =>
      expect(screen.getByText(/Scanned 50, embedded 50/i)).toBeInTheDocument()
    );
  });
});
