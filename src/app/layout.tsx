import type { Metadata } from "next";
import { Inter } from "next/font/google";
import BackgroundOrbs from "@/components/BackgroundOrbs";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Anonymiseur — Anonymisation de documents juridiques",
  description:
    "Anonymisez vos documents juridiques (PDF, DOCX, TXT) et textes libres. Détection automatique des noms, adresses, dates, numéros de téléphone et autres données sensibles.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr">
      <body className={`${inter.variable} antialiased`}>
        <BackgroundOrbs />
        {children}
      </body>
    </html>
  );
}
