import { NextResponse } from 'next/server';

const backendUrl = process.env.TRIAGE_API_URL ?? 'http://127.0.0.1:8000';

export async function GET() {
  try {
    const response = await fetch(`${backendUrl}/health`, { cache: 'no-store' });

    return new Response(response.body, {
      status: response.status,
      headers: {
        'content-type':
          response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Unable to connect to the Triage Sentinel API:', error);
    return NextResponse.json(
      { error: 'The backend health check is unavailable.' },
      { status: 502 },
    );
  }
}
