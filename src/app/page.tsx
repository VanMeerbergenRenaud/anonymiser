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
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_FILES = 5;
const MAX_FILE_SIZE = 4.5 * 1024 * 1024; // 4.5 MB
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getExtension(name: string): string {
  const idx = name.lastIndexOf(".");
  return idx !== -1 ? name.slice(idx).toLowerCase() : "";
}

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Home() {
  const [activeTab, setActiveTab] = useState<"files" | "text">("files");

  // --- Files tab state ---
  const [files, setFiles] = useState<TrackedFile[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // --- Text tab state ---
  const [inputText, setInputText] = useState("");
  const [anonymizedText, setAnonymizedText] = useState("");
  const [isTextProcessing, setIsTextProcessing] = useState(false);
  const [textError, setTextError] = useState("");
  const [copied, setCopied] = useState(false);

  // -----------------------------------------------------------------------
  // File handling
  // -----------------------------------------------------------------------

  const processFile = useCallback(async (tracked: TrackedFile) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === tracked.id ? { ...f, status: "processing" as FileStatus } : f)),
    );

    try {
      const formData = new FormData();
      formData.append("file", tracked.file);

      const res = await fetch("/api/anonymize-file", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Erreur inconnue" }));
        throw new Error(err.error || `Erreur ${res.status}`);
      }

      // Trigger automatic download
      const blob = await res.blob();
      const filename =
        res.headers.get("X-Filename") ||
        tracked.file.name.replace(/(\.\w+)$/, "_anonymise$1");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      setFiles((prev) =>
        prev.map((f) => (f.id === tracked.id ? { ...f, status: "done" as FileStatus } : f)),
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Erreur inconnue";
      setFiles((prev) =>
        prev.map((f) =>
          f.id === tracked.id ? { ...f, status: "error" as FileStatus, error: msg } : f,
        ),
      );
    }
  }, []);

  const addFiles = useCallback(
    (incoming: FileList | File[]) => {
      const newFiles: TrackedFile[] = [];

      const currentCount = files.length;
      const allowed = MAX_FILES - currentCount;

      if (allowed <= 0) return;

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

      // Auto-process pending files
      for (const tf of newFiles) {
        if (tf.status === "pending") {
          processFile(tf);
        }
      }
    },
    [files.length, processFile],
  );

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles],
  );

  const handleFileInput = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        addFiles(e.target.files);
        e.target.value = "";
      }
    },
    [addFiles],
  );

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const clearFiles = () => setFiles([]);

  // -----------------------------------------------------------------------
  // Text handling
  // -----------------------------------------------------------------------

  const handleAnonymizeText = async () => {
    if (!inputText.trim()) return;
    setIsTextProcessing(true);
    setTextError("");
    setAnonymizedText("");
    setCopied(false);

    try {
      const res = await fetch("/api/anonymize-text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: inputText }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Erreur inconnue" }));
        throw new Error(err.error || `Erreur ${res.status}`);
      }

      const data = await res.json();
      setAnonymizedText(data.anonymized);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Erreur inconnue";
      setTextError(msg);
    } finally {
      setIsTextProcessing(false);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(anonymizedText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
      const textarea = document.createElement("textarea");
      textarea.value = anonymizedText;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <main className="min-h-screen flex flex-col items-center px-4 py-12">
      {/* Header */}
      <div className="w-full max-w-2xl mb-10">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Anonymiseur
        </h1>
        <p className="mt-1 text-sm text-muted">
          Anonymisation de documents juridiques
        </p>
      </div>

      {/* Tabs */}
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

        {/* ============================================================= */}
        {/* FILES TAB                                                      */}
        {/* ============================================================= */}
        {activeTab === "files" && (
          <div>
            {/* Drop zone */}
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
                  ou cliquez pour sélectionner — PDF, DOCX, TXT (max{" "}
                  {MAX_FILES} fichiers, 4.5 Mo chacun)
                </p>
              </div>
            </div>

            {/* File list */}
            {files.length > 0 && (
              <div className="mt-4 space-y-2">
                {files.map((tf) => (
                  <div
                    key={tf.id}
                    className="flex items-center justify-between border border-border rounded-md px-4 py-3 text-sm"
                  >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      {/* Status indicator */}
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
                        ({(tf.file.size / 1024).toFixed(0)} Ko)
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
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
        )}

        {/* ============================================================= */}
        {/* TEXT TAB                                                        */}
        {/* ============================================================= */}
        {activeTab === "text" && (
          <div>
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Collez votre texte ici…"
              rows={8}
              className="w-full border border-border rounded-md px-4 py-3 text-sm resize-y focus:outline-none focus:border-neutral-400 bg-white placeholder:text-neutral-400"
            />

            <button
              onClick={handleAnonymizeText}
              disabled={isTextProcessing || !inputText.trim()}
              className="mt-3 px-5 py-2 text-sm font-medium bg-foreground text-background rounded-md hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isTextProcessing && (
                <span className="w-3.5 h-3.5 border-2 border-background border-t-transparent rounded-full animate-spin" />
              )}
              Anonymiser
            </button>

            {textError && (
              <p className="mt-3 text-sm text-red-600">{textError}</p>
            )}

            {anonymizedText && (
              <div className="mt-6">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-sm font-medium text-foreground">
                    Résultat
                  </h2>
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
        )}
      </div>

      {/* Footer */}
      <footer className="mt-auto pt-12 pb-4 text-xs text-muted">
        Traitement local via Presidio — aucune donnée n&apos;est stockée.
      </footer>
    </main>
  );
}
