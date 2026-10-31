import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Pipeline Shorts — Auditor de Viralidade & Estrategista IA',
  description: 'Auditoria avançada de retenção, ganchos nos primeiros 3s e psicologia de cortes para TikTok e YouTube Shorts.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR" className="dark">
      <body className="min-h-screen bg-[#09090b] text-[#f4f4f5] antialiased">
        {children}
      </body>
    </html>
  );
}
