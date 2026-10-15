import { NextResponse } from 'next/server';

const backendUrl = process.env.TRIAGE_API_URL ?? 'http://127.0.0.1:8000';
const forwardedHeaders = ['accept', 'authorization', 'cookie', 'content-type'];

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  if (request.method === 'POST') {
    const origin = request.headers.get('origin');
    if (!origin || origin !== new URL(request.url).origin) {
      return NextResponse.json({ detail: 'Request origin was rejected.' }, { status: 403 });
    }
  }

  const { path } = await context.params;
  const headers = new Headers();
  for (const name of forwardedHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  try {
    const upstream = await fetch(`${backendUrl}/${path.map(encodeURIComponent).join('/')}`, {
      method: request.method,
      headers,
      body: request.method === 'GET' ? undefined : await request.arrayBuffer(),
      cache: 'no-store',
      redirect: 'manual',
    });
    const responseHeaders = new Headers({ 'cache-control': 'no-store' });
    const contentType = upstream.headers.get('content-type');
    if (contentType) responseHeaders.set('content-type', contentType);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error('Backend request failed:', error);
    return NextResponse.json(
      { detail: 'The backend API is unavailable.' },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;