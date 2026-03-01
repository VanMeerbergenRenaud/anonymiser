"use client";

import { useState } from "react";
import FileTab from "@/components/FileTab";
import TextTab from "@/components/TextTab";

// ---------------------------------------------------------------------------
// Main Application Display
// ---------------------------------------------------------------------------

export default function Home() {
  const [activeTab, setActiveTab] = useState<"files" | "text">("files");

  return (
    // Force the whole app into a single screen height with flex centering.
    // The background is handled by globals.css
    <main className="h-screen w-full flex flex-col items-center justify-center p-4 pb-0">

      {/* Central Application Window */}
      <div className="w-full max-w-2xl h-185 max-h-[85vh] bg-white flex flex-col rounded-2xl border border-border overflow-hidden relative shadow-sm">

        {/* Header / Top Bar */}
        <header className="px-8 py-6 border-b border-border flex items-center justify-between z-10 bg-white">
          <div>
            <h1 className="text-xl font-medium tracking-tight text-foreground">
              Anonymiseur
            </h1>
            <p className="text-xs text-muted mt-0.5">
              Traitement NLP local en Belgique
            </p>
          </div>

          {/* Minimalist Segmented Control for Tabs */}
          <div className="flex bg-neutral-100/50 p-0.5 rounded-lg border border-border/50">
            <button
              onClick={() => setActiveTab("files")}
              className={`px-4 py-1.5 text-xs font-medium rounded-lg transition-all duration-300 cursor-pointer ${activeTab === "files"
                ? "bg-white text-foreground border border-border/50"
                : "text-muted hover:text-foreground border border-transparent"
                }`}
            >
              Fichiers
            </button>
            <button
              onClick={() => setActiveTab("text")}
              className={`px-4 py-1.5 text-xs font-medium rounded-lg transition-all duration-300 cursor-pointer ${activeTab === "text"
                ? "bg-white text-foreground border border-border/50"
                : "text-muted hover:text-foreground border border-transparent"
                }`}
            >
              Texte
            </button>
          </div>
        </header>

        {/* Dynamic Content Area */}
        <div className="flex-1 overflow-hidden relative">
          <div className={`absolute inset-0 transition-opacity duration-300 ${activeTab === "files" ? "opacity-100 z-10" : "opacity-0 -z-10 pointer-events-none"}`}>
            <div className="h-full p-6 overflow-y-auto">
              <FileTab />
            </div>
          </div>
          <div className={`absolute inset-0 transition-opacity duration-300 ${activeTab === "text" ? "opacity-100 z-10" : "opacity-0 -z-10 pointer-events-none"}`}>
            <div className="h-full p-6 overflow-y-auto">
              <TextTab />
            </div>
          </div>
        </div>

      </div>

      {/* Minimal Footer Footer */}
      <footer className="mt-8 text-[11px] text-muted tracking-wide uppercase">
        Réaliser par&nbsp;
        <a href="https://renaud-vmb.com" title="Vers le site du créateur" className="underline" target="_blank">
          Renaud Vmb
        </a>
      </footer>
    </main>
  );
}
