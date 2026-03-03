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
    <main className="h-screen w-full flex flex-col items-center justify-center my-4 px-4">

      {/* Central Application Window */}
      <div className="w-full max-w-2xl min-h-180 max-h-[85vh] bg-white/95 backdrop-blur-2xl flex flex-col rounded-[32px] border border-black/[0.08] overflow-hidden relative shadow-[0_8px_30px_rgb(0,0,0,0.06)] ring-1 ring-black/[0.02]">

        {/* Header / Top Bar */}
        <header className="px-8 py-6 border-b border-gray-100 flex items-center justify-between z-10 bg-transparent">
          <div>
            <h1 className="text-xl font-medium tracking-tight text-foreground">
              Anonymiseur
            </h1>
            <p className="text-xs text-muted mt-0.5">
              Traitement NLP local en Belgique
            </p>
          </div>

          {/* Minimalist Segmented Control for Tabs */}
          <div className="flex bg-neutral-100/60 p-1 rounded-xl border border-gray-100 shadow-inner">
            <button
              onClick={() => setActiveTab("files")}
              className={`px-4 py-1.5 text-xs font-medium rounded-lg transition-all duration-300 cursor-pointer ${activeTab === "files"
                ? "bg-white text-foreground shadow-sm ring-1 ring-black/5"
                : "text-muted hover:text-foreground"
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
    </main>
  );
}
