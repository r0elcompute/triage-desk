'use client';

import { useEffect, useState } from 'react';

type HealthState = 'checking' | 'connected' | 'unavailable';

export default function Home() {
  const [health, setHealth] = useState<HealthState>('checking');

  useEffect(() => {
    const controller = new AbortController();

    async function checkBackend() {
      try {
        const response = await fetch('/api/health', {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Health check returned ${response.status}`);
        }
        const result: { status: string } = await response.json();
        setHealth(result.status === 'ok' ? 'connected' : 'unavailable');
      } catch (error) {
        if (!controller.signal.aborted) {
          console.error('Unable to connect to the Triage Sentinel API:', error);
          setHealth('unavailable');
        }
      }
    }

    void checkBackend();
    return () => controller.abort();
  }, []);

  const healthMessage = {
    checking: 'Checking backend connection…',
    connected: 'Backend connected',
    unavailable: 'Backend unavailable — start the API and check its URL.',
  }[health];

  return (
    <main className="page">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">TRIAGE SENTINEL</p>
        <h1 id="page-title">A thoughtful foundation for better care.</h1>
        <p className="intro">
          A foundation for healthcare triage workflows, built with care for
          patients and the people who support them.
        </p>
        <div
          className={`connection connection--${health}`}
          role="status"
          aria-live="polite"
        >
          <span className="connection__indicator" aria-hidden="true" />
          {healthMessage}
        </div>
        <p className="disclaimer">
          This MVP foundation does not provide medical advice or clinical
          triage.
        </p>
      </section>
    </main>
  );
}
