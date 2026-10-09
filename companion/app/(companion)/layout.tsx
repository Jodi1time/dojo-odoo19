import type { Metadata } from "next";
import { Inter } from "next/font/google";
import AdaptiveShell from "@/components/shell/AdaptiveShell";
import ConnectedCompanion from "@/components/ai/ConnectedCompanion";
import {odooTestMode} from "@/server/odoo-runtime";
import "./globals.scss";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Dojang Companion",
  description: "AI-first front desk and member management",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <AdaptiveShell connectedTest={odooTestMode()} context={odooTestMode()?<nav><h2>Connected test workspace</h2><p>Only synthetic authorized records are shown.</p><a href="/integration/members">Test members</a></nav>:undefined} companion={odooTestMode()?<ConnectedCompanion />:undefined}>{children}</AdaptiveShell>
      </body>
    </html>
  );
}
