// Vitest setup: registers @testing-library/jest-dom matchers and a
// fresh fetch mock per test so component tests can stub API calls
// without hitting the network.
import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
