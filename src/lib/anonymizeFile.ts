/**
 * Client de l'endpoint `POST /api/anonymize_file?stream=1`.
 *
 * Utilise XMLHttpRequest plutôt que `fetch` pour suivre la progression de
 * l'envoi (fichiers jusqu'à 100 Mo), et lit le flux NDJSON renvoyé par le
 * serveur au fil du traitement (une ligne JSON par événement : progression,
 * file d'attente, résultat ou erreur — voir `api/index.py`).
 */

/** Taille maximale d'un fichier (doit correspondre à `ANON_MAX_FILE_MB` côté serveur). */
export const MAX_FILE_MB = 100;
export const MAX_FILE_SIZE = MAX_FILE_MB * 1024 * 1024;

/** Étapes du traitement côté serveur. */
export type Stage = "extract" | "ocr" | "analyze";

/** Avancement signalé pendant le traitement d'un fichier. */
export type FileProgress =
    | { kind: "upload"; fraction: number }
    | { kind: "waiting" }
    | { kind: "queued" }
    | { kind: "stage"; stage: Stage; done: number; total: number };

/** Mot à relire signalé par le serveur (voir `api/review.py`). */
export interface ReviewTerm {
    term: string;
    count: number;
    reason: string;
}

/** Résultat d'un traitement réussi (texte Markdown). */
export interface AnonymizedFile {
    filename: string;
    content: string;
    ocrImages: number;
    ocrSkipped: number;
    review: ReviewTerm[];
}

/** Traitement en cours : promesse du résultat et fonction d'annulation. */
export interface FileJob {
    promise: Promise<AnonymizedFile>;
    abort: () => void;
}

/** Erreur levée quand l'utilisateur annule le traitement. */
export class AbortedError extends Error {
    constructor() {
        super("Traitement annulé");
        this.name = "AbortedError";
    }
}

type ServerEvent =
    | { event: "accepted" | "queued" | "keepalive" }
    | { event: "progress"; stage: Stage; done: number; total: number }
    | {
          event: "done";
          filename: string;
          content: string;
          ocr_images: number;
          ocr_skipped: number;
          review?: ReviewTerm[];
      }
    | { event: "error"; status: number; error: string };

const CONNECTION_ERROR =
    "Connexion au serveur impossible. Vérifiez que le serveur backend est lancé puis réessayez.";

/** Message d'erreur d'une réponse non 200 (JSON de l'API, ou page d'un proxy). */
function httpErrorMessage(status: number, body: string): string {
    try {
        const data: unknown = JSON.parse(body);
        if (data && typeof data === "object" && "error" in data && typeof data.error === "string") {
            return data.error;
        }
    } catch {
        // Réponse HTML d'un proxy (Nginx, Next.js) : message générique ci-dessous.
    }
    if (status === 413) return `Le fichier est trop volumineux (max ${MAX_FILE_MB} Mo).`;
    if (status === 502 || status === 503 || status === 504) return CONNECTION_ERROR;
    return `Erreur du serveur (${status}).`;
}

/**
 * Envoie un fichier et suit son traitement.
 *
 * `onProgress` est appelé pendant l'envoi puis à chaque étape du traitement.
 */
export function anonymizeFile(file: File, onProgress: (progress: FileProgress) => void): FileJob {
    const xhr = new XMLHttpRequest();

    const promise = new Promise<AnonymizedFile>((resolve, reject) => {
        let offset = 0;
        let settled = false;

        const succeed = (value: AnonymizedFile) => {
            if (!settled) {
                settled = true;
                resolve(value);
            }
        };
        const fail = (error: Error) => {
            if (!settled) {
                settled = true;
                reject(error);
            }
        };

        const handle = (event: ServerEvent) => {
            switch (event.event) {
                case "queued":
                    onProgress({ kind: "queued" });
                    break;
                case "progress":
                    onProgress({ kind: "stage", stage: event.stage, done: event.done, total: event.total });
                    break;
                case "done":
                    succeed({
                        filename: event.filename,
                        content: event.content,
                        ocrImages: event.ocr_images,
                        ocrSkipped: event.ocr_skipped,
                        review: event.review ?? [],
                    });
                    break;
                case "error":
                    fail(new Error(event.error));
                    break;
                default:
                    break; // accepted, keepalive
            }
        };

        /** Lit les lignes NDJSON complètes reçues depuis le dernier appel. */
        const consume = () => {
            if (xhr.status !== 200) return;
            const text = xhr.responseText;
            let newline = text.indexOf("\n", offset);
            while (newline !== -1) {
                const line = text.slice(offset, newline).trim();
                offset = newline + 1;
                if (line) {
                    try {
                        handle(JSON.parse(line) as ServerEvent);
                    } catch {
                        // Ligne corrompue : ignorée (la fin du flux tranchera).
                    }
                }
                newline = text.indexOf("\n", offset);
            }
        };

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && e.total > 0) {
                onProgress({ kind: "upload", fraction: e.loaded / e.total });
            }
        };
        xhr.upload.onload = () => onProgress({ kind: "waiting" });
        xhr.onprogress = consume;
        xhr.onload = () => {
            if (xhr.status !== 200) {
                fail(new Error(httpErrorMessage(xhr.status, xhr.responseText)));
                return;
            }
            consume();
            fail(new Error("Le traitement a été interrompu. Veuillez réessayer."));
        };
        xhr.onerror = () => fail(new Error(CONNECTION_ERROR));
        xhr.onabort = () => fail(new AbortedError());

        const form = new FormData();
        form.append("file", file);
        xhr.open("POST", "/api/anonymize_file?stream=1");
        xhr.send(form);
    });

    return { promise, abort: () => xhr.abort() };
}
