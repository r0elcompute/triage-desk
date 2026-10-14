import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Triage Sentinel',
  description: 'A secure foundation for healthcare triage workflows.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
