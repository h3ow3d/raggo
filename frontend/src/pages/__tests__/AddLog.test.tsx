/**
 * AddLog: API call shape on submit, error rendering, success state.
 *
 * Labels in this page aren't wired to inputs via htmlFor/id, so we
 * locate the flight select and metadata textarea positionally.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AddLog from '../AddLog';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockReset();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const FLIGHTS = {
  results: [
    {
      id: 1,
      flight_number: 'BA001',
      airline: 'BA',
      origin: 'LHR',
      destination: 'JFK',
      scheduled_departure: '2024-01-01T08:00:00Z',
      status: 'scheduled',
    },
  ],
};

describe('AddLog', () => {
  it('lists flights from /api/flights and submits a log to /api/logs', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((async (
      url: RequestInfo,
      init?: RequestInit
    ) => {
      const u = String(url);
      if (u.startsWith('/api/flights')) {
        return jsonResponse(FLIGHTS);
      }
      if (u === '/api/logs' && init?.method === 'POST') {
        return jsonResponse({
          id: 99,
          flight_id: 1,
          log_time: '2024-01-01T09:00:00Z',
          log_type: 'maintenance',
          source_system: 'ACARS',
          severity: 'info',
          message: 'a test',
          embedded: true,
          embedding_error: null,
        });
      }
      throw new Error(`unexpected url ${u}`);
    }) as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<AddLog />);

    await waitFor(() => expect(screen.getByText(/BA001/)).toBeInTheDocument());
    const selects = document.querySelectorAll('select');
    await user.selectOptions(selects[0] as HTMLSelectElement, '1');

    const messageBox = screen.getByPlaceholderText(/Describe what happened/i);
    await user.type(messageBox, 'a test');

    await user.click(screen.getByRole('button', { name: /create log/i }));

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(
        ([u, ini]) =>
          String(u) === '/api/logs' && (ini as RequestInit | undefined)?.method === 'POST'
      );
      expect(postCall).toBeDefined();
    });

    expect(await screen.findByText(/Log #99 on flight #1/i)).toBeInTheDocument();
  });

  it('shows an inline error when the metadata textarea contains invalid JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(FLIGHTS));
    const user = userEvent.setup();
    render(<AddLog />);

    await waitFor(() => expect(screen.getByText(/BA001/)).toBeInTheDocument());

    // metadata textarea is the second textarea on the page.
    const textareas = document.querySelectorAll('textarea');
    const metadataBox = textareas[1] as HTMLTextAreaElement;
    await user.clear(metadataBox);
    await user.type(metadataBox, '{{not valid');

    expect(await screen.findByText(/Metadata is not valid JSON/i)).toBeInTheDocument();
  });
});
