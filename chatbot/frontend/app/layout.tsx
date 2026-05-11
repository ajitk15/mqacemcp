import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MCP Chatbot",
  description: "Generic chat UI for any MCP server",
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
