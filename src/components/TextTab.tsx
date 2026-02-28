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
        <div className="flex flex-col h-full relative">
            {!anonymizedText ? (
                <div className="flex flex-col h-full bg-white rounded-xl border border-border overflow-hidden">
                    <textarea
                        value={inputText}
                        onChange={(e) => setInputText(e.target.value)}
                        placeholder="Collez le texte à anonymiser ici..."
                        className="flex-1 w-full p-6 text-sm resize-none focus:outline-none bg-transparent placeholder:text-neutral-400 leading-relaxed"
                    />

                    <div className="border-t border-border p-4 bg-neutral-50 flex items-center justify-between shrink-0">
                        <div className="text-xs text-muted flex items-center gap-2">
                            {error && <span className="text-red-500">{error}</span>}
                        </div>

                        <button
                            onClick={handleAnonymize}
                            disabled={isProcessing || !inputText.trim()}
                            className="px-6 py-2 text-sm font-medium bg-foreground text-background rounded-lg hover:bg-neutral-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2"
                        >
                            {isProcessing && (
                                <svg className="w-4 h-4 text-background animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                            )}
                            Anonymiser
                        </button>
                    </div>
                </div>
            ) : (
                <div className="flex flex-col h-full bg-white rounded-xl border border-border overflow-hidden">
                    <div className="border-b border-border p-4 bg-neutral-50 flex items-center justify-between shrink-0">
                        <div className="flex items-center gap-3">
                            <span className="flex items-center justify-center w-6 h-6 rounded-full bg-green-100 text-green-700">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="20 6 9 17 4 12" />
                                </svg>
                            </span>
                            <h3 className="text-sm font-medium text-foreground">Texte anonymisé</h3>
                        </div>
                        <div className="flex flex-row gap-2">
                            <button
                                onClick={() => {
                                    setAnonymizedText("");
                                    setInputText("");
                                }}
                                className="px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-neutral-200/50 rounded-md transition-colors"
                            >
                                Recommencer
                            </button>
                            <button
                                onClick={handleCopy}
                                className="px-4 py-1.5 text-xs font-medium bg-white border border-border text-foreground rounded-md hover:bg-neutral-50 transition-colors flex items-center gap-2"
                            >
                                {copied ? (
                                    <>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-600">
                                            <polyline points="20 6 9 17 4 12" />
                                        </svg>
                                        Copié !
                                    </>
                                ) : (
                                    <>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                        </svg>
                                        Copier
                                    </>
                                )}
                            </button>
                        </div>
                    </div>

                    <div className="flex-1 p-6 text-sm whitespace-pre-wrap bg-transparent leading-relaxed overflow-y-auto selection:bg-neutral-200">
                        {anonymizedText}
                    </div>
                </div>
            )}
        </div>
    );
}
