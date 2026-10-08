"use client";

import { useState, useCallback, useEffect, useRef, type DragEvent, type ChangeEvent } from "react";
import {
    AbortedError,
    MAX_FILE_MB,
    MAX_FILE_SIZE,
    anonymizeFile,
    type FileProgress,
    type ReviewTerm,
} from "@/lib/anonymizeFile";
import ReviewList from "./ReviewList";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FileStatus = "pending" | "processing" | "done" | "error";

interface TrackedFile {
    id: string;
    file: File;
    status: FileStatus;
    /** Dernier avancement connu (envoi, file d'attente, étape du serveur). */
    progress?: FileProgress;
    error?: string;
    downloadUrl?: string;
    downloadName?: string;
    /** Nombre d'images dont le texte a été lu par OCR. */
    ocrImages?: number;
    /** Nombre d'images non analysées (OCR indisponible, format, limite). */
    ocrSkipped?: number;
    /** Mots à relire signalés par le serveur. */
    review?: ReviewTerm[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_FILES = 8;
/**
 * Fichiers traités simultanément : le serveur traite de toute façon un
 * nombre limité de fichiers à la fois (les autres attendent leur tour).
 */
const MAX_CONCURRENT = 2;
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".jfif", ".tif", ".tiff", ".bmp", ".gif", ".webp"];
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ...IMAGE_EXTENSIONS];

const STAGE_LABELS = {
    extract: "Lecture du document",
    ocr: "Lecture des images (OCR)",
    analyze: "Anonymisation",
} as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extrait l'extension d'un nom de fichier (ex: ".pdf"). */
function getExtension(name: string): string {
    const idx = name.lastIndexOf(".");
    return idx !== -1 ? name.slice(idx).toLowerCase() : "";
}

/** Génère un identifiant court aléatoire. */
function uid(): string {
    return Math.random().toString(36).slice(2, 10);
}

/** Taille lisible : « 850 Ko », « 12,4 Mo ». */
function formatSize(bytes: number): string {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.ceil(bytes / 1024))} Ko`;
    return `${(bytes / (1024 * 1024)).toLocaleString("fr-BE", { maximumFractionDigits: 1 })} Mo`;
}

/** Libellé et fraction (0–1, ou null si indéterminée) d'un fichier en cours. */
function describeProgress(tf: TrackedFile): { label: string; fraction: number | null } {
    if (tf.status === "pending") return { label: "En attente…", fraction: null };
    const p = tf.progress;
    if (!p) return { label: "Préparation…", fraction: null };
    switch (p.kind) {
        case "upload":
            return { label: `Envoi… ${Math.floor(p.fraction * 100)} %`, fraction: p.fraction };
        case "waiting":
            return { label: "Traitement…", fraction: null };
        case "queued":
            return { label: "En file d'attente sur le serveur…", fraction: null };
        case "stage": {
            const fraction = p.total > 0 ? Math.min(1, p.done / p.total) : null;
            if (p.stage === "analyze" || fraction === null) {
                const percent = fraction === null ? "" : ` ${Math.floor(fraction * 100)} %`;
                return { label: `${STAGE_LABELS[p.stage]}…${percent}`, fraction };
            }
            const unit = p.stage === "ocr" ? "image" : "page";
            return {
                label: `${STAGE_LABELS[p.stage]} : ${unit} ${Math.min(p.done + 1, p.total)} / ${p.total}`,
                fraction,
            };
        }
    }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Onglet "Fichiers" : zone de drag-and-drop pour uploader des fichiers
 * (PDF, DOCX, TXT, images) et les anonymiser automatiquement. Le texte
 * contenu dans les images est lu par OCR côté serveur.
 */
export default function FileTab() {
    const [files, setFiles] = useState<TrackedFile[]>([]);
    const [isDragOver, setIsDragOver] = useState(false);
    const [notice, setNotice] = useState("");
    const inputRef = useRef<HTMLInputElement>(null);

    // File d'attente côté client (au plus MAX_CONCURRENT envois simultanés).
    const queue = useRef<TrackedFile[]>([]);
    const active = useRef(0);
    const aborts = useRef(new Map<string, () => void>());
    const objectUrls = useRef(new Set<string>());

    const update = useCallback((id: string, patch: Partial<TrackedFile>) => {
        setFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...patch } : f)));
    }, []);

    // Au démontage : annule les traitements en cours et libère les résultats.
    useEffect(() => {
        const pendingAborts = aborts.current;
        const urls = objectUrls.current;
        return () => {
            queue.current = [];
            pendingAborts.forEach((abort) => abort());
            urls.forEach((url) => URL.revokeObjectURL(url));
        };
    }, []);

    // -----------------------------------------------------------------------
    // Traitement d'un fichier
    // -----------------------------------------------------------------------

    const run = useCallback(
        async (tracked: TrackedFile) => {
            update(tracked.id, { status: "processing", progress: undefined });
            const job = anonymizeFile(tracked.file, (progress) => update(tracked.id, { progress }));
            aborts.current.set(tracked.id, job.abort);
            try {
                const result = await job.promise;
                const blob = new Blob([result.content], { type: "text/markdown;charset=utf-8" });
                const url = URL.createObjectURL(blob);
                objectUrls.current.add(url);
                update(tracked.id, {
                    status: "done",
                    progress: undefined,
                    downloadUrl: url,
                    downloadName: result.filename,
                    ocrImages: result.ocrImages,
                    ocrSkipped: result.ocrSkipped,
                    review: result.review,
                });
            } catch (e: unknown) {
                if (e instanceof AbortedError) return; // fichier retiré de la liste
                const msg = e instanceof Error ? e.message : "Erreur inconnue";
                update(tracked.id, { status: "error", progress: undefined, error: msg });
            } finally {
                aborts.current.delete(tracked.id);
            }
        },
        [update]
    );

    const pump = useCallback(() => {
        while (active.current < MAX_CONCURRENT && queue.current.length > 0) {
            const next = queue.current.shift()!;
            active.current += 1;
            void run(next).finally(() => {
                active.current -= 1;
                pump();
            });
        }
    }, [run]);

    // -----------------------------------------------------------------------
    // Ajout de fichiers
    // -----------------------------------------------------------------------

    const addFiles = useCallback(
        (incoming: FileList | File[]) => {
            const all = Array.from(incoming);
            const allowed = Math.max(0, MAX_FILES - files.length);
            const list = all.slice(0, allowed);
            const ignored = all.length - list.length;
            setNotice(
                ignored > 0
                    ? `Maximum ${MAX_FILES} fichiers à la fois : ${ignored} fichier${ignored > 1 ? "s" : ""} ignoré${ignored > 1 ? "s" : ""}.`
                    : ""
            );
            if (list.length === 0) return;

            const newFiles: TrackedFile[] = list.map((file) => {
                const ext = getExtension(file.name);
                if (!ACCEPTED_EXTENSIONS.includes(ext)) {
                    const error =
                        ext === ".doc" || ext === ".odt" || ext === ".rtf"
                            ? "Format non pris en charge : enregistrez-le en .docx ou .pdf"
                            : "Format non pris en charge";
                    return { id: uid(), file, status: "error", error };
                }
                if (file.size > MAX_FILE_SIZE) {
                    return {
                        id: uid(),
                        file,
                        status: "error",
                        error: `Fichier trop volumineux (max ${MAX_FILE_MB} Mo)`,
                    };
                }
                if (file.size === 0) {
                    return { id: uid(), file, status: "error", error: "Fichier vide" };
                }
                return { id: uid(), file, status: "pending" };
            });

            setFiles((prev) => [...prev, ...newFiles]);
            queue.current.push(...newFiles.filter((f) => f.status === "pending"));
            pump();
        },
        [files.length, pump]
    );

    // -----------------------------------------------------------------------
    // Event handlers
    // -----------------------------------------------------------------------

    const handleDrop = useCallback(
        (e: DragEvent) => {
            e.preventDefault();
            setIsDragOver(false);
            if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
        },
        [addFiles]
    );

    const handleFileInput = useCallback(
        (e: ChangeEvent<HTMLInputElement>) => {
            if (e.target.files && e.target.files.length > 0) {
                addFiles(e.target.files);
                e.target.value = "";
            }
        },
        [addFiles]
    );

    /** Annule (si besoin) et oublie les fichiers indiqués. */
    const discard = useCallback((targets: TrackedFile[]) => {
        const ids = new Set(targets.map((f) => f.id));
        queue.current = queue.current.filter((f) => !ids.has(f.id));
        for (const f of targets) {
            aborts.current.get(f.id)?.();
            if (f.downloadUrl) {
                URL.revokeObjectURL(f.downloadUrl);
                objectUrls.current.delete(f.downloadUrl);
            }
        }
        setFiles((prev) => prev.filter((f) => !ids.has(f.id)));
    }, []);

    const removeFile = (tf: TrackedFile) => discard([tf]);

    const clearFiles = () => {
        discard(files);
        setNotice("");
    };

    const downloadAll = () => {
        files.forEach((f) => {
            if (f.status === "done" && f.downloadUrl) {
                const a = document.createElement("a");
                a.href = f.downloadUrl;
                a.download = f.downloadName || "anonymise.md";
                document.body.appendChild(a);
                a.click();
                a.remove();
            }
        });
    };

    // -----------------------------------------------------------------------
    // Render
    // -----------------------------------------------------------------------

    return (
        <div className="flex flex-col h-full">
            {/* Zone de dépôt */}
            <div
                role="button"
                tabIndex={0}
                aria-label="Ajouter des fichiers à anonymiser"
                onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragOver(true);
                }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        inputRef.current?.click();
                    }
                }}
                className={`border border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 ${isDragOver
                    ? "border-foreground bg-neutral-50"
                    : "border-border hover:border-neutral-400 bg-white"
                    }`}
            >
                <input
                    ref={inputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    multiple
                    onChange={handleFileInput}
                    className="hidden"
                />
                <div className="flex flex-col items-center justify-center gap-3">
                    <div className={`p-3 rounded-full transition-colors ${isDragOver ? "bg-neutral-200" : "bg-neutral-100"}`}>
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-foreground">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <polyline points="17 8 12 3 7 8" />
                            <line x1="12" x2="12" y1="3" y2="15" />
                        </svg>
                    </div>
                    <div className="text-sm">
                        <p className="font-medium text-foreground mb-1">
                            Déposez vos fichiers ici
                        </p>
                        <p className="text-muted text-xs">
                            PDF, DOCX, TXT, images (max {MAX_FILES} fichiers, {MAX_FILE_MB} Mo chacun) · résultat en Markdown (.md)
                        </p>
                        <p className="text-muted text-[11px] mt-1">
                            Le texte présent dans les images est lu automatiquement (OCR)
                        </p>
                    </div>
                </div>
            </div>

            {notice && (
                <p className="mt-3 text-[12px] text-amber-700" role="status">
                    {notice}
                </p>
            )}

            {/* Liste des fichiers */}
            {files.length > 0 && (
                <div className="mt-6 flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-3 shrink-0">
                        <h3 className="pl-2 pt-3 text-xs font-medium text-muted uppercase tracking-wider">Fichiers ({files.length})</h3>
                        <div className="flex items-center gap-2 pr-3">
                            <button
                                onClick={clearFiles}
                                className="px-3 py-2 text-[13px] font-medium bg-red-50 text-red-600 rounded-lg hover:bg-red-100 transition-colors cursor-pointer"
                            >
                                Tout supprimer
                            </button>
                            {files.some((f) => f.status === "done") && (
                                <button
                                    onClick={downloadAll}
                                    className="px-3 py-2 text-[13px] font-medium bg-foreground text-background rounded-lg hover:bg-neutral-700 transition-colors cursor-pointer"
                                >
                                    Tout télécharger
                                </button>
                            )}
                        </div>
                    </div>

                    <div className="space-y-2 overflow-y-auto pr-2 pb-2">
                        {files.map((tf) => {
                            const inProgress = tf.status === "pending" || tf.status === "processing";
                            const progress = inProgress ? describeProgress(tf) : null;
                            return (
                                <div
                                    key={tf.id}
                                    className="group flex items-start justify-between border border-border bg-white rounded-lg px-4 py-3 text-sm hover:border-neutral-300 transition-colors"
                                >
                                    <div className="flex items-start gap-3 min-w-0 flex-1">
                                        {/* Indicateur de statut avec SVG minimalistes */}
                                        {tf.status === "processing" && (
                                            <svg className="shrink-0 mt-0.5 w-4 h-4 text-neutral-400 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"></circle>
                                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                            </svg>
                                        )}
                                        {tf.status === "done" && (
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-green-600">
                                                <polyline points="20 6 9 17 4 12" />
                                            </svg>
                                        )}
                                        {tf.status === "error" && (
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-red-500">
                                                <line x1="18" y1="6" x2="6" y2="18" />
                                                <line x1="6" y1="6" x2="18" y2="18" />
                                            </svg>
                                        )}
                                        {tf.status === "pending" && (
                                            <div className="shrink-0 mt-0.5 w-4 h-4 rounded-full border border-neutral-200" />
                                        )}

                                        <div className="flex flex-col min-w-0 flex-1">
                                            <span className="truncate font-medium text-foreground text-[13px]">{tf.file.name}</span>
                                            <span className="text-muted text-[11px]">
                                                {formatSize(tf.file.size)}
                                                {progress && <span>{" · "}{progress.label}</span>}
                                                {!!tf.ocrImages && (
                                                    <span
                                                        className="text-amber-600"
                                                        title="Texte extrait d'images par OCR : relisez les passages signalés dans le fichier"
                                                    >
                                                        {" · "}
                                                        {tf.ocrImages} image{tf.ocrImages > 1 ? "s" : ""} lue{tf.ocrImages > 1 ? "s" : ""} par OCR (à vérifier)
                                                    </span>
                                                )}
                                                {!!tf.ocrSkipped && (
                                                    <span
                                                        className="text-red-500"
                                                        title="Certaines images n'ont pas pu être analysées (voir le fichier)"
                                                    >
                                                        {" · "}
                                                        {tf.ocrSkipped} image{tf.ocrSkipped > 1 ? "s" : ""} non analysée{tf.ocrSkipped > 1 ? "s" : ""}
                                                    </span>
                                                )}
                                            </span>
                                            {progress && (
                                                <div
                                                    className="mt-1.5 h-1 w-full max-w-64 rounded-full bg-neutral-100 overflow-hidden"
                                                    role="progressbar"
                                                    aria-label={progress.label}
                                                    aria-valuemin={0}
                                                    aria-valuemax={100}
                                                    aria-valuenow={progress.fraction === null ? undefined : Math.round(progress.fraction * 100)}
                                                >
                                                    <div
                                                        className={`h-full rounded-full bg-neutral-400 transition-[width] duration-300 ${progress.fraction === null ? "w-1/3 animate-pulse" : ""}`}
                                                        style={progress.fraction === null ? undefined : { width: `${Math.max(2, progress.fraction * 100)}%` }}
                                                    />
                                                </div>
                                            )}
                                            {tf.status === "done" && tf.review && <ReviewList review={tf.review} />}
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-2.5 ml-5">
                                        {tf.status === "done" && tf.downloadUrl && (
                                            <a
                                                href={tf.downloadUrl}
                                                download={tf.downloadName}
                                                className="text-muted hover:text-foreground transition-colors p-1 cursor-pointer"
                                                title="Télécharger"
                                                aria-label={`Télécharger ${tf.downloadName ?? "le fichier anonymisé"}`}
                                            >
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                    <polyline points="7 10 12 15 17 10" />
                                                    <line x1="12" y1="15" x2="12" y2="3" />
                                                </svg>
                                            </a>
                                        )}
                                        {tf.status === "error" && tf.error && (
                                            <span className="text-[11px] text-red-500 max-w-40 truncate" title={tf.error}>
                                                {tf.error}
                                            </span>
                                        )}
                                        <button
                                            onClick={() => removeFile(tf)}
                                            className="text-muted hover:text-red-500 transition-colors p-1 cursor-pointer"
                                            aria-label={inProgress ? "Annuler" : "Supprimer"}
                                            title={inProgress ? "Annuler" : "Supprimer"}
                                        >
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                <path d="M3 6h18" />
                                                <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
                                                <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                                            </svg>
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    {/* Spacer block since clear files was moved to the top right */}
                    <div className="mt-2 shrink-0"></div>
                </div>
            )}
        </div>
    );
}
