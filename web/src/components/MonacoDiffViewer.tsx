"use client";

import React, { useState, useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import { Columns, AlignJustify, Loader2 } from "lucide-react";

// Dynamically import DiffEditor from @monaco-editor/react to prevent SSR issues
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="monaco-loading-container">
        <Loader2 className="animate-spin text-indigo-400" size={28} />
        <span>Loading Monaco Diff Editor...</span>
      </div>
    ),
  }
);

interface MonacoDiffViewerProps {
  original: string;
  modified: string;
  language?: string;
  filename?: string;
  height?: string;
}

export function MonacoDiffViewer({
  original,
  modified,
  language = "python",
  filename,
  height = "520px",
}: MonacoDiffViewerProps) {
  const [isSplit, setIsSplit] = useState(true);
  const [isMounted, setIsMounted] = useState(false);
  const diffEditorRef = useRef<any>(null);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const handleEditorDidMount = (editor: any) => {
    diffEditorRef.current = editor;
  };

  useEffect(() => {
    if (diffEditorRef.current) {
      diffEditorRef.current.updateOptions({
        renderSideBySide: isSplit,
      });
      diffEditorRef.current.layout();
    }
  }, [isSplit]);

  // Detect language from filename if provided
  let detectedLang = language;
  if (filename) {
    if (filename.endsWith(".py")) detectedLang = "python";
    else if (filename.endsWith(".ts") || filename.endsWith(".tsx"))
      detectedLang = "typescript";
    else if (filename.endsWith(".js") || filename.endsWith(".jsx"))
      detectedLang = "javascript";
    else if (filename.endsWith(".json")) detectedLang = "json";
    else if (filename.endsWith(".yaml") || filename.endsWith(".yml"))
      detectedLang = "yaml";
  }

  return (
    <div className="monaco-viewer-card">
      <div className="monaco-viewer-header">
        <div className="monaco-header-left">
          {filename && <span className="monaco-filename">{filename}</span>}
          <span className="monaco-lang-tag">{detectedLang}</span>
        </div>
        <div className="monaco-header-controls">
          <button
            type="button"
            className={`monaco-mode-btn ${isSplit ? "active" : ""}`}
            onClick={() => setIsSplit(true)}
            title="Side-by-side split view"
          >
            <Columns size={14} />
            <span>Split View</span>
          </button>
          <button
            type="button"
            className={`monaco-mode-btn ${!isSplit ? "active" : ""}`}
            onClick={() => setIsSplit(false)}
            title="Inline unified view"
          >
            <AlignJustify size={14} />
            <span>Inline</span>
          </button>
        </div>
      </div>

      <div className="monaco-editor-wrapper" style={{ height }}>
        {isMounted ? (
          <DiffEditor
            original={original}
            modified={modified}
            language={detectedLang}
            theme="vs-dark"
            onMount={handleEditorDidMount}
            options={{
              readOnly: true,
              renderSideBySide: isSplit,
              automaticLayout: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              fontSize: 13,
              lineNumbers: "on",
              wordWrap: "off",
              folding: true,
              smoothScrolling: true,
              renderOverviewRuler: false,
            }}
          />
        ) : (
          <div className="monaco-loading-container">
            <Loader2 className="animate-spin text-indigo-400" size={28} />
            <span>Initializing editor...</span>
          </div>
        )}
      </div>
    </div>
  );
}
