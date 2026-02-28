"use client";

import { useState } from "react";

/**
 * Onglet "Texte" : permet de coller un texte libre et de l'anonymiser
 * via l'API ``/api/anonymize_text``.
 */
export default function TextTab() {
    const [inputText, setInputText] = useState("");
    const [anonymizedText, setAnonymizedText] = useState("");
    const [isProcessing, setIsProcessing] = useState(false);
    const [error, setError] = useState("");
    const [copied, setCopied] = useState(false);

    // -----------------------------------------------------------------------
    // Anonymiser le texte
    // -----------------------------------------------------------------------

    const handleAnonymize = async () => {
        if (!inputText.trim()) return;
        setIsProcessing(true);
        setError("");
        setAnonymizedText("");
        setCopied(false);

        try {
            const res = await fetch("/api/anonymize_text", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: inputText }),
            });

            if (!res.ok) {
                const text = await res.text();
                let errMsg = `Erreur ${res.status}`;
                try {
                    const err = JSON.parse(text);
                    if (err.error) errMsg = err.error;
                } catch {
                    // Fallback for HTML error pages (e.g. 500/502 from Next.js server)
                    errMsg = `Erreur de connexion au serveur (${res.status}). Veuillez vérifier que le serveur backend est lancé.`;
                }
                throw new Error(errMsg);
            }

            const data = await res.json();
            setAnonymizedText(data.anonymized);
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Erreur inconnue";
            setError(msg);
        } finally {
            setIsProcessing(false);
        }
    };

    // -----------------------------------------------------------------------
    // Copier le résultat
    // -----------------------------------------------------------------------

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(anonymizedText);
        } catch {
            // Fallback pour les navigateurs sans Clipboard API
            const textarea = document.createElement("textarea");
            textarea.value = anonymizedText;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand("copy");
            textarea.remove();
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    // -----------------------------------------------------------------------
    // Render
    // -----------------------------------------------------------------------

    return (
        <div>
            <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="Collez votre texte ici…"
                rows={8}
                className="w-full border border-border rounded-md px-4 py-3 text-sm resize-y focus:outline-none focus:border-neutral-400 bg-white placeholder:text-neutral-400"
            />

            <button
                onClick={handleAnonymize}
                disabled={isProcessing || !inputText.trim()}
                className="mt-3 px-5 py-2 text-sm font-medium bg-foreground text-background rounded-md hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
            >
                {isProcessing && (
                    <span className="w-3.5 h-3.5 border-2 border-background border-t-transparent rounded-full animate-spin" />
                )}
                Anonymiser
            </button>

            {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

            {anonymizedText && (
                <div className="mt-6">
                    <div className="flex items-center justify-between mb-2">
                        <h2 className="text-sm font-medium text-foreground">Résultat</h2>
                        <button
                            onClick={handleCopy}
                            className="text-xs text-muted hover:text-foreground transition-colors"
                        >
                            {copied ? "Copié ✓" : "Copier le texte"}
                        </button>
                    </div>
                    <div className="border border-border rounded-md px-4 py-3 text-sm whitespace-pre-wrap bg-white leading-relaxed">
                        {anonymizedText}
                    </div>
                </div>
            )}
        </div>
    );
}
