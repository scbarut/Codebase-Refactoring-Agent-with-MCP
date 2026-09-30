"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { X, Play, Loader2, Sparkles, Folder, Globe } from "lucide-react";
import { createJob } from "@/lib/api";

interface NewJobModalProps {
  isOpen: boolean;
  onClose: () => void;
  onJobCreated?: (jobId: string) => void;
}

export function NewJobModal({ isOpen, onClose, onJobCreated }: NewJobModalProps) {
  const router = useRouter();
  const [source, setSource] = useState("");
  const [targetLibrary, setTargetLibrary] = useState("pydantic");
  const [workspaceBaseDir, setWorkspaceBaseDir] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!source.trim()) {
      setError("Please provide a repository URL or local directory path.");
      return;
    }
    if (!targetLibrary.trim()) {
      setError("Please specify the target library being migrated.");
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const res = await createJob({
        path_or_url: source.trim(),
        target_library: targetLibrary.trim(),
        workspace_base_dir: workspaceBaseDir.trim() || undefined,
      });

      if (onJobCreated) {
        onJobCreated(res.job_id);
      }
      onClose();
      // Navigate to the plan review page where scanning state is handled
      router.push(`/jobs/${res.job_id}/plan`);
    } catch (err: any) {
      setError(err.message || "Failed to start migration job");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-header">
          <div className="modal-title-wrap">
            <Sparkles className="text-indigo-400" size={20} />
            <h2>New Migration Job</h2>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          {error && <div className="modal-error-alert">{error}</div>}

          <div className="form-group">
            <label htmlFor="source-input">
              <span>Codebase Source</span>
              <span className="label-hint">
                Local directory path or Git clone URL
              </span>
            </label>
            <div className="input-with-icon">
              {source.startsWith("http") || source.startsWith("git@") ? (
                <Globe size={16} className="input-icon" />
              ) : (
                <Folder size={16} className="input-icon" />
              )}
              <input
                id="source-input"
                type="text"
                placeholder="e.g. /home/user/my-repo or https://github.com/org/repo.git"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="preset-row">
              <span className="preset-label">Examples:</span>
              <button
                type="button"
                className="preset-chip"
                onClick={() => setSource("./test-repo")}
              >
                ./test-repo
              </button>
              <button
                type="button"
                className="preset-chip"
                onClick={() =>
                  setSource("https://github.com/tiangolo/fastapi.git")
                }
              >
                fastapi.git
              </button>
            </div>
          </div>

          <div className="form-group">
            <label htmlFor="target-lib-input">
              <span>Target Library to Modernize</span>
              <span className="label-hint">e.g. pydantic v1 → v2</span>
            </label>
            <input
              id="target-lib-input"
              type="text"
              placeholder="e.g. pydantic"
              value={targetLibrary}
              onChange={(e) => setTargetLibrary(e.target.value)}
              required
            />
            <div className="preset-row">
              <span className="preset-label">Supported:</span>
              {["pydantic", "sqlalchemy", "requests", "celery"].map((lib) => (
                <button
                  key={lib}
                  type="button"
                  className={`preset-chip ${targetLibrary === lib ? "selected" : ""}`}
                  onClick={() => setTargetLibrary(lib)}
                >
                  {lib}
                </button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label htmlFor="workspace-base-dir">
              <span>Workspace Directory (Optional)</span>
              <span className="label-hint">Leave blank for default sandbox</span>
            </label>
            <input
              id="workspace-base-dir"
              type="text"
              placeholder="e.g. /tmp/migration-workspaces"
              value={workspaceBaseDir}
              onChange={(e) => setWorkspaceBaseDir(e.target.value)}
            />
          </div>

          <div className="modal-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="animate-spin" size={16} />
                  <span>Initiating Scan...</span>
                </>
              ) : (
                <>
                  <Play size={16} />
                  <span>Start Migration</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
