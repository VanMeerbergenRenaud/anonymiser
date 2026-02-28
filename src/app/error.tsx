"use client";

import { useEffect } from "react";

export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        // Peut être envoyé à un service de log comme Sentry
        console.error("Global UI Error caught by boundary:", error);
    }, [error]);

    return (
        <div className="min-h-screen flex flex-col items-center justify-center px-4 py-12 bg-background">
            <div className="w-full max-w-md p-6 bg-white border border-border rounded-lg shadow-sm text-center">
                <h2 className="text-xl font-semibold mb-3 text-red-600">Une erreur inattendue est survenue</h2>
                <p className="text-sm text-muted mb-6">
                    L’application a rencontré un problème. Veuillez réessayer ou recharger la page.
                </p>
                <button
                    onClick={() => reset()}
                    className="px-5 py-2 text-sm font-medium bg-foreground text-background rounded-lg hover:opacity-90 transition-opacity"
                >
                    Réessayer
                </button>
            </div>
        </div>
    );
}
