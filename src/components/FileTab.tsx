"use client";

import { useState, useCallback, useRef, type DragEvent, type ChangeEvent } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FileStatus = "pending" | "processing" | "done" | "error";

interface TrackedFile {
    id: string;
    file: File;
    status: FileStatus;
    error?: string;
    downloadUrl?: string;
    downloadName?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_FILES = 5;
const MAX_FILE_SIZE = 4.5 * 1024 * 1024; // 4.5 Mo
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt"];

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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Onglet "Fichiers" : zone de drag-and-drop pour uploader des fichiers
 * (PDF, DOCX, TXT) et les anonymiser automatiquement.
 */
export default function FileTab() {
    const [files, setFiles] = useState<TrackedFile[]>([]);
    const [isDragOver, setIsDragOver] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    // -----------------------------------------------------------------------
    // Traitement d'un fichier
    // -----------------------------------------------------------------------

    const processFile = useCallback(async (tracked: TrackedFile) => {
        setFiles((prev) =>
            prev.map((f) =>
                f.id === tracked.id ? { ...f, status: "processing" as FileStatus } : f
            )
        );

        try {
            const formData = new FormData();
            formData.append("file", tracked.file);

            const res = await fetch("/api/anonymize_file", {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                const text = await res.text();
                let errMsg = `Erreur ${res.status}`;
                try {
                    const err = JSON.parse(text);
                    if (err.error) errMsg = err.error;
                } catch {
                    errMsg = `Erreur de connexion au serveur (${res.status}). Veuillez vérifier que le serveur backend est lancé.`;
                }
                throw new Error(errMsg);
            }

            const blob = await res.blob();
            const filename = res.headers.get("X-Filename") || "a-" + tracked.file.name;
            const url = URL.createObjectURL(blob);

            setFiles((prev) =>
                prev.map((f) =>
                    f.id === tracked.id
                        ? { ...f, status: "done" as FileStatus, downloadUrl: url, downloadName: filename }
                        : f
                )
            );
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Erreur inconnue";
            setFiles((prev) =>
                prev.map((f) =>
                    f.id === tracked.id ? { ...f, status: "error" as FileStatus, error: msg } : f
                )
            );
        }
    }, []);

    // -----------------------------------------------------------------------
    // Ajout de fichiers
    // -----------------------------------------------------------------------

    const addFiles = useCallback(
        (incoming: FileList | File[]) => {
            const allowed = MAX_FILES - files.length;
            if (allowed <= 0) return;

            const newFiles: TrackedFile[] = [];
            const list = Array.from(incoming).slice(0, allowed);

            for (const file of list) {
                const ext = getExtension(file.name);
                if (!ACCEPTED_EXTENSIONS.includes(ext)) continue;
                if (file.size > MAX_FILE_SIZE) {
                    newFiles.push({
                        id: uid(),
                        file,
                        status: "error",
                        error: "Fichier trop volumineux (max 4.5 Mo)",
                    });
                    continue;
                }
                newFiles.push({ id: uid(), file, status: "pending" });
            }

            if (newFiles.length === 0) return;
            setFiles((prev) => [...prev, ...newFiles]);

            // Lancer le traitement automatiquement
            for (const tf of newFiles) {
                if (tf.status === "pending") processFile(tf);
            }
        },
        [files.length, processFile]
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

    const removeFile = (id: string) => {
        setFiles((prev) => {
            const target = prev.find((f) => f.id === id);
            if (target?.downloadUrl) URL.revokeObjectURL(target.downloadUrl);
            return prev.filter((f) => f.id !== id);
        });
    };

    const clearFiles = () => {
        setFiles((prev) => {
            prev.forEach((f) => {
                if (f.downloadUrl) URL.revokeObjectURL(f.downloadUrl);
            });
            return [];
        });
    };

    const downloadAll = () => {
        files.forEach((f) => {
            if (f.status === "done" && f.downloadUrl) {
                const a = document.createElement("a");
                a.href = f.downloadUrl;
                a.download = f.downloadName || "anonymised.txt";
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
                onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragOver(true);
                }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                className={`border border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-300 ${isDragOver
                    ? "border-foreground bg-neutral-50"
                    : "border-border hover:border-neutral-400 bg-white"
                    }`}
            >
                <input
                    ref={inputRef}
                    type="file"
                    accept=".pdf,.docx,.txt"
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
                            PDF, DOCX, TXT (max {MAX_FILES} fichiers, 4.5 Mo)
                        </p>
                    </div>
                </div>
            </div>

            {/* Liste des fichiers */}
            {files.length > 0 && (
                <div className="mt-6 flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-3 shrink-0">
                        <h3 className="text-xs font-medium text-muted uppercase tracking-wider">Fichiers ({files.length})</h3>
                        {files.some((f) => f.status === "done") && (
                            <button
                                onClick={downloadAll}
                                className="px-3 py-1 text-xs font-medium bg-foreground text-background rounded-md hover:bg-neutral-800 transition-colors"
                            >
                                Tout télécharger
                            </button>
                        )}
                    </div>

                    <div className="space-y-2 overflow-y-auto pr-2 pb-2">
                        {files.map((tf) => (
                            <div
                                key={tf.id}
                                className="group flex items-center justify-between border border-border bg-white rounded-lg px-4 py-3 text-sm hover:border-neutral-300 transition-colors"
                            >
                                <div className="flex items-center gap-3 min-w-0 flex-1">
                                    {/* Indicateur de statut avec SVG minimalistes */}
                                    {tf.status === "processing" && (
                                        <svg className="shrink-0 w-4 h-4 text-neutral-400 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"></circle>
                                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                        </svg>
                                    )}
                                    {tf.status === "done" && (
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-green-600">
                                            <polyline points="20 6 9 17 4 12" />
                                        </svg>
                                    )}
                                    {tf.status === "error" && (
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-red-500">
                                            <line x1="18" y1="6" x2="6" y2="18" />
                                            <line x1="6" y1="6" x2="18" y2="18" />
                                        </svg>
                                    )}
                                    {tf.status === "pending" && (
                                        <div className="shrink-0 w-4 h-4 rounded-full border border-neutral-200" />
                                    )}

                                    <div className="flex flex-col min-w-0">
                                        <span className="truncate font-medium text-foreground text-[13px]">{tf.file.name}</span>
                                        <span className="text-muted text-[11px]">
                                            {Math.max(1, Math.ceil(tf.file.size / 1024))} Ko
                                        </span>
                                    </div>
                                </div>

                                <div className="flex items-center gap-3 ml-4">
                                    {tf.status === "done" && tf.downloadUrl && (
                                        <a
                                            href={tf.downloadUrl}
                                            download={tf.downloadName}
                                            className="text-xs font-medium text-foreground hover:text-neutral-500 transition-colors"
                                        >
                                            Télécharger
                                        </a>
                                    )}
                                    {tf.status === "error" && tf.error && (
                                        <span className="text-[11px] text-red-500 max-w-32 truncate" title={tf.error}>
                                            {tf.error}
                                        </span>
                                    )}
                                    <button
                                        onClick={() => removeFile(tf.id)}
                                        className="text-muted hover:text-red-500 transition-colors p-1 opacity-0 group-hover:opacity-100 focus:opacity-100"
                                        aria-label="Supprimer"
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M3 6h18" />
                                            <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
                                            <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                                        </svg>
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>

                    <div className="mt-2 shrink-0">
                        <button
                            onClick={clearFiles}
                            className="text-[11px] text-muted hover:text-foreground transition-colors uppercase tracking-wide"
                        >
                            Tout effacer
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
