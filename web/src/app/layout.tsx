import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Migration Agent — Autonomous Code Modernization",
  description: "Autonomous Codebase Refactoring & Migration Agent with Model Context Protocol",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <nav className="navbar">
          <div className="brand">
            <span>⚡</span>
            <span>Migration Agent</span>
          </div>
          <div className="status-badge">
            <span className="pulse-dot"></span>
            <span>Stack Online</span>
          </div>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
