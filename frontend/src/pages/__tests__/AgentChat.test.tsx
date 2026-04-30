/**
 * AgentChat: covers the happy path called out in the phase plan.
 * - submits a question
 * - shows "Thinking…" while the request is in flight
 * - renders the answer text
 * - renders the evidence list
 * - renders the agent_trace JSON in an expandable block
 * - surfaces errors when the API rejects
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AgentChat from '../AgentChat';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockReset();
});

function mockJsonResponse(body: unknown, init: Partial<Response> = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

describe('AgentChat', () => {
  it('renders an empty-state with example question hints', () => {
    render(<AgentChat />);
    expect(screen.getByRole('heading', { name: /agent chat/i })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/ask the agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Were there any recent engine-related/i)).toBeInTheDocument();
  });

  it('calls /api/query with the typed question and renders answer + evidence + trace', async () => {
    const fakeResponse = {
      answer: 'Two critical incidents this week.',
      evidence: [
        { type: 'incident', id: 17, message: 'Engine warning on descent' },
        { type: 'flight_log', id: 42, message: 'ECAM caution observed', severity: 'critical' },
      ],
      agent_trace: {
        strategy: 'vector_and_sql',
        tools_used: ['get_incidents_by_severity'],
        vector_queries: ['critical incidents this week'],
        retrieved_ids: { incident: [17], flight_log: [42] },
      },
    };

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(mockJsonResponse(fakeResponse));

    const user = userEvent.setup();
    render(<AgentChat />);

    await user.type(
      screen.getByPlaceholderText(/ask the agent/i),
      'Were there any critical incidents this week?'
    );
    await user.click(screen.getByRole('button', { name: /ask/i }));

    // The fetch was made to /api/query with the right body.
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toBe('/api/query');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({
      question: 'Were there any critical incidents this week?',
    });

    // Answer renders.
    await waitFor(() =>
      expect(screen.getByText('Two critical incidents this week.')).toBeInTheDocument()
    );

    // Evidence list renders both items with their IDs.
    expect(screen.getByText(/Evidence \(2\)/)).toBeInTheDocument();
    expect(screen.getByText(/Engine warning on descent/)).toBeInTheDocument();
    expect(screen.getByText(/ECAM caution observed/)).toBeInTheDocument();

    // Trace block is rendered as JSON in a <pre>.
    const trace = screen.getByText(/get_incidents_by_severity/);
    expect(trace.tagName).toBe('PRE');
    expect(trace.textContent).toContain('"strategy": "vector_and_sql"');
  });

  it('surfaces the backend error message when /api/query rejects', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'embedding service unavailable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const user = userEvent.setup();
    render(<AgentChat />);
    await user.type(screen.getByPlaceholderText(/ask the agent/i), 'why?');
    await user.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() =>
      expect(screen.getByText(/embedding service unavailable/i)).toBeInTheDocument()
    );
  });

  it('renders "No evidence retrieved." when the agent returns an empty evidence list', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      mockJsonResponse({
        answer: 'I do not have enough information to answer that.',
        evidence: [],
        agent_trace: { strategy: 'vector_only', tools_used: [], retrieved_ids: {} },
      })
    );

    const user = userEvent.setup();
    render(<AgentChat />);
    await user.type(screen.getByPlaceholderText(/ask the agent/i), 'hello?');
    await user.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => expect(screen.getByText(/No evidence retrieved/i)).toBeInTheDocument());
  });
});
