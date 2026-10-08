import type { ReviewTerm } from "@/lib/anonymizeFile";

/**
 * Mots à relire après l'anonymisation (calculés par le serveur, voir
 * `api/review.py`) : ce que le moteur a choisi de conserver alors qu'il
 * pourrait s'agir d'une personne, et les noms propres possibles non masqués.
 * Rien n'est modifié : la liste invite seulement à vérifier le résultat.
 */
export default function ReviewList({ review }: { review: ReviewTerm[] }) {
    if (review.length === 0) {
        return (
            <p className="mt-1.5 text-[11px] text-green-700">Aucun mot à relire signalé.</p>
        );
    }
    const total = review.length;
    return (
        <details className="mt-1.5 group/review">
            <summary className="cursor-pointer select-none text-[11px] font-medium text-amber-700 hover:text-amber-800">
                {total} mot{total > 1 ? "s" : ""} à relire avant usage
            </summary>
            <ul className="mt-2 max-h-56 overflow-y-auto rounded-lg border border-amber-200 bg-amber-50/60 divide-y divide-amber-100">
                {review.map((item) => (
                    <li key={item.term} className="px-3 py-1.5 text-[12px] leading-snug">
                        <span className="font-medium text-foreground break-words">{item.term}</span>
                        {item.count > 1 && <span className="text-muted"> ×{item.count}</span>}
                        <span className="block text-[11px] text-muted">{item.reason}</span>
                    </li>
                ))}
            </ul>
        </details>
    );
}
