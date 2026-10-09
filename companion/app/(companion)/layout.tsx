import type { Metadata } from "next";
import { Inter } from "next/font/google";
import AdaptiveShell from "@/components/shell/AdaptiveShell";
import { odooTestMode } from "@/server/odoo-runtime";
import "./globals.scss";
const inter = Inter({variable: "--font-inter", subsets: ["latin"]});
export const metadata: Metadata = {title: "Dojang Companion", description: "Front desk and member management"};
export const dynamic = "force-dynamic";
export default function RootLayout({children}: {children: React.ReactNode}) {
  const connected = odooTestMode();
  const context = connected ? <section><h2>Authorized test records</h2><p>Identity and attendance come from the selected Odoo test database.</p><a href="/integration/members">Choose a member</a><p>The Companion reads shared attendance and class context. Enable internal parent follow-ups in Odoo; AI drafting uses the configured provider. External sends and automatic bookings are not enabled.</p></section> : undefined;
  return <html lang="en" className={`${inter.variable} h-full antialiased`}><body className="min-h-full flex flex-col"><AdaptiveShell connectedTest={connected} context={context}>{children}</AdaptiveShell></body></html>;
}
