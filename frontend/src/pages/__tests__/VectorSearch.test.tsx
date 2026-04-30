/**
 * VectorSearch: form submission shape, results rendering, empty results.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VectorSearch from '../VectorSearch';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockReset();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('VectorSearch', () => {
  it('posts the query, top_k and filters and renders the resulting hits', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        query: 'engine vibration',
        resource: 'flight_logs',
        top_k: 10,
        results: [
          {
            id: 7,
            score: 0.82,
            distance: 0.18,
            text: 'Engine vibration during climb',
            metadata: {
              flight_number: 'BA123',
              origin: 'LHR',
              destination: 'JFK',
              severity: 'warning',
              source_system: 'ACARS',
            },
          },
        ],
      })
    );

    const user = userEvent.setup();
    render(<VectorSearch />);
    await user.type(screen.getByPlaceholderText(/engine vibration during climb/i), 'engine vibration');
    await user.click(screen.getByRole('button', { name: /search/i }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toBe('/api/search/vector');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ query: 'engine vibration', top_k: 10 });

    // Result row renders with score + flight metadata + log text.
    expect(await screen.findByText(/Engine vibration during climb/i)).toBeInTheDocument();
    expect(screen.getByText('#7')).toBeInTheDocument();
    expect(screen.getByText(/similarity: 0\.820/)).toBeInTheDocument();
    expect(screen.getByText(/flight BA123 · LHR → JFK/)).toBeInTheDocument();
  });

  it('renders the empty-state when the API returns no matches', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({ query: 'x', resource: 'flight_logs', top_k: 10, results: [] })
    );

    const user = userEvent.setup();
    render(<VectorSearch />);
    await user.type(screen.getByPlaceholderText(/engine vibration during climb/i), 'x');
    await user.click(screen.getByRole('button', { name: /search/i }));

    expect(await screen.findByText(/No matches\./i)).toBeInTheDocument();
  });

  it('renders the backend error inside the form on 4xx', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({ detail: 'unsupported filter: foo' }, 400)
    );

    const user = userEvent.setup();
    render(<VectorSearch />);
    await user.type(screen.getByPlaceholderText(/engine vibration during climb/i), 'q');
    await user.click(screen.getByRole('button', { name: /search/i }));

    expect(await screen.findByText(/unsupported filter: foo/i)).toBeInTheDocument();
  });
});
