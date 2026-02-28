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
        <div>
            {/* Zone de dépôt */}
            <div
                onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragOver(true);
                }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors ${isDragOver
                    ? "border-foreground bg-neutral-100"
                    : "border-border hover:border-neutral-400"
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
                <div className="text-muted text-sm">
                    <p className="font-medium text-foreground mb-1">
                        Déposez vos fichiers ici
                    </p>
                    <p>
                        ou cliquez pour sélectionner — PDF, DOCX, TXT (max {MAX_FILES}{" "}
                        fichiers, 4.5 Mo chacun)
                    </p>
                </div>
            </div>

            {/* Liste des fichiers */}
            {files.length > 0 && (
                <div className="mt-4 space-y-2">
                    {/* Bouton tout télécharger */}
                    {files.some((f) => f.status === "done") && (
                        <div className="flex justify-end mb-2">
                            <button
                                onClick={downloadAll}
                                className="px-4 py-1.5 text-sm font-medium bg-foreground text-background rounded-md hover:opacity-90 transition-opacity"
                            >
                                Tout télécharger
                            </button>
                        </div>
                    )}

                    {files.map((tf) => (
                        <div
                            key={tf.id}
                            className="flex items-center justify-between border border-border rounded-md px-4 py-3 text-sm"
                        >
                            <div className="flex items-center gap-3 min-w-0 flex-1">
                                {/* Indicateur de statut */}
                                {tf.status === "processing" && (
                                    <span className="shrink-0 w-4 h-4 border-2 border-foreground border-t-transparent rounded-full animate-spin" />
                                )}
                                {tf.status === "done" && (
                                    <span className="shrink-0 text-foreground">✓</span>
                                )}
                                {tf.status === "error" && (
                                    <span className="shrink-0 text-red-600">✕</span>
                                )}
                                {tf.status === "pending" && (
                                    <span className="shrink-0 w-4 h-4 bg-neutral-300 rounded-full" />
                                )}

                                <span className="truncate">{tf.file.name}</span>
                                <span className="text-muted text-xs">
                                    ({Math.max(1, Math.ceil(tf.file.size / 1024))} Ko)
                                </span>
                            </div>

                            <div className="flex items-center gap-3">
                                {tf.status === "done" && tf.downloadUrl && (
                                    <a
                                        href={tf.downloadUrl}
                                        download={tf.downloadName}
                                        className="text-xs font-semibold text-foreground hover:underline transition-all"
                                    >
                                        Télécharger
                                    </a>
                                )}
                                {tf.status === "error" && tf.error && (
                                    <span className="text-xs text-red-600 max-w-48 truncate">
                                        {tf.error}
                                    </span>
                                )}
                                <button
                                    onClick={() => removeFile(tf.id)}
                                    className="text-muted hover:text-foreground text-xs"
                                >
                                    Supprimer
                                </button>
                            </div>
                        </div>
                    ))}

                    <button
                        onClick={clearFiles}
                        className="mt-2 text-xs text-muted hover:text-foreground transition-colors"
                    >
                        Tout effacer
                    </button>
                </div>
            )}
        </div>
    );
}
