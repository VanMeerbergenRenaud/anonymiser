"use client";

import { useState } from "react";
import FileTab from "@/components/FileTab";
import TextTab from "@/components/TextTab";

// ---------------------------------------------------------------------------
// Page principale
// ---------------------------------------------------------------------------

/**
 * Page d'accueil de l'application d'anonymisation.
 *
 * Deux onglets :
 * - **Fichiers** : upload de documents (PDF, DOCX, TXT) pour anonymisation automatique.
 * - **Texte** : saisie libre de texte à anonymiser.
 */
export default function Home() {
  const [activeTab, setActiveTab] = useState<"files" | "text">("files");

  return (
    <main className="min-h-screen flex flex-col items-center px-4 py-12">
      {/* En-tête */}
      <div className="w-full max-w-2xl mb-10">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Anonymiseur
        </h1>
        <p className="mt-1 text-sm text-muted">
          Anonymisation de documents juridiques
        </p>
      </div>

      {/* Onglets */}
      <div className="w-full max-w-2xl">
        <div className="flex border-b border-border mb-6">
          <button
            onClick={() => setActiveTab("files")}
            className={`px-4 py-2 text-sm font-medium transition-colors -mb-px ${activeTab === "files"
                ? "border-b-2 border-foreground text-foreground"
                : "text-muted hover:text-foreground"
              }`}
          >
            Fichiers
          </button>
          <button
            onClick={() => setActiveTab("text")}
            className={`px-4 py-2 text-sm font-medium transition-colors -mb-px ${activeTab === "text"
                ? "border-b-2 border-foreground text-foreground"
                : "text-muted hover:text-foreground"
              }`}
          >
            Texte
          </button>
        </div>

        {/* Contenu de l'onglet actif */}
        {activeTab === "files" && <FileTab />}
        {activeTab === "text" && <TextTab />}
      </div>

      {/* Pied de page */}
      <footer className="mt-auto pt-12 pb-4 text-xs text-muted">
        Traitement local via Presidio — aucune donnée n&apos;est stockée.
      </footer>
    </main>
  );
}
