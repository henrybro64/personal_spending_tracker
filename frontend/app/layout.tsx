import type { ReactNode } from "react";
import type { Metadata } from "next";
import { Providers } from "./providers";
import "./globals.css";


export const metadata: Metadata = {
  title: "Personal Spending Tracker",
  description: "Upload statement PDFs and review spending with a richer dashboard.",
};


export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
