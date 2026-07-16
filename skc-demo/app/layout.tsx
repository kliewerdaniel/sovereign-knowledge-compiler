import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sovereign Knowledge Compiler — Live Demo",
  description:
    "153 blog posts compiled into a decision graph by a local-first knowledge compiler. 1,513 facts, 436 decisions, entirely on a local LLM. Live proof.",
  openGraph: {
    title: "Sovereign Knowledge Compiler — Live Demo",
    description:
      "153 blog posts → 1,513 facts → 436 decisions, compiled locally. Interactive 3D knowledge graph.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
