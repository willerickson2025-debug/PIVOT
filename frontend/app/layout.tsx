import type { Metadata } from "next";
import "./globals.css";
import Nav from "./_components/Nav";

export const metadata: Metadata = {
  title: "PIVOT",
  description: "NBA intelligence platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col">
        <Nav />
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
