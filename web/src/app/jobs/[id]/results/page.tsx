"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Code2,
  Copy,
  ExternalLink,
  FileCode,
  Flame,
  Folder,
  GitBranch,
  GitPullRequest,
  Loader2,
  RefreshCw,
  Terminal,
  X,
  XCircle,
} from "lucide-react";
import { fetchJob, fetchJobResults } from "@/lib/api";
import { FileResult, JobResponse, MigrationResult } from "@/lib/types";
import { parseUnifiedDiff } from "@/lib/diff-parser";
import { MonacoDiffViewer } from "@/components/MonacoDiffViewer";
import { CopyButton } from "@/components/CopyButton";

export default function ResultsPage() {
  const params = useParams();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id as string);

  const [job, setJob] = useState<JobResponse | null>(null);
  const [results, setResults] = useState<MigrationResult | null>(null);
  const [selectedFile, setSelectedFile] = useState<FileResult | null>(null);
  const [fileFilter, setFileFilter] = useState<"ALL" | "SUCCESS" | "FAILED">("ALL");
  const [loading, setLoading] = useState(true);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prCopiedNotice, setPrCopiedNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!id) return;
    try {
      const jobData = await fetchJob(id);
      setJob(jobData);

      if (jobData.status !== "complete" && jobData.status !== "failed") {
        setIsPending(true);
        setLoading(false);
        return;
      }

      try {
        const resData = await fetchJobResults(id);
        setResults(resData);
        setIsPending(false);

        // Pre-select the first file
        const allFiles = [
          ...(resData.failed_files || []),
          ...(resData.successful_files || []),
        ];
        if (allFiles.length > 0 && !selectedFile) {
          setSelectedFile(allFiles[0]);
        }
        setError(null);
      } catch (resErr: any) {
        if (resErr.status === 409 || resErr.message?.includes("not yet available")) {
          setIsPending(true);
        } else {
          setError(resErr.message || "Failed to load migration results");
        }
      }
    } catch (err: any) {
      setError(err.message || "Failed to fetch job data");
    } finally {
      setLoading(false);
    }
  }, [id, selectedFile]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // If pending, poll every 3s
  useEffect(() => {
    if (!isPending) return;
    const interval = setInterval(() => {
      loadData();
    }, 3000);
    return () => clearInterval(interval);
  }, [isPending, loadData]);

  // Combine successful and failed files for unified file navigation
  const allFiles: FileResult[] = [
    ...(results?.failed_files || []),
    ...(results?.successful_files || []),
  ];

  const filteredFiles = allFiles.filter((f) => {
    if (fileFilter === "SUCCESS") return f.status === "SUCCESS";
    if (fileFilter === "FAILED") return f.status === "FAILED";
    return true;
  });

  const parsedDiff = selectedFile
    ? parseUnifiedDiff(selectedFile.diff)
    : { original: "", modified: "" };

  const gitCommands = results?.git_commands;
  const isGithub = gitCommands?.is_github || job?.source?.includes("github.com");

  return (
    <div className="results-page">
      {/* Pending State */}
      {isPending ? (
        <div className="pending-state-card">
          <Loader2 className="animate-spin text-indigo-400" size={48} />
          <h2>Migration in Progress...</h2>
          <p>
            Results and Monaco split-diffs will become available once the File
            Sub-graph completes rewriting and sandbox validation for all
            approved files.
          </p>
          <div className="pending-actions">
            <Link
              href={`/jobs/${id}/progress`}
              className="btn-primary"
            >
              <span>Watch Live Progress</span>
              <ArrowRight size={15} />
            </Link>
          </div>
        </div>
      ) : loading ? (
        <div className="loading-state-card">
          <Loader2 className="animate-spin text-indigo-400" size={32} />
          <p>Loading migration results...</p>
        </div>
      ) : error ? (
        <div className="plan-error-card">
          <AlertCircle className="text-rose-400" size={24} />
          <div className="error-body">
            <h3>Unable to Load Results</h3>
            <p>{error}</p>
          </div>
          <button
            type="button"
            className="btn-retry"
            onClick={() => loadData()}
          >
            Retry
          </button>
        </div>
      ) : !results ? (
        <div className="empty-state-card">
          <Code2 size={48} className="text-muted" />
          <h3>No Results Recorded</h3>
          <p>No results were generated for this job.</p>
        </div>
      ) : (
        <>
          {/* Summary Stats Cards */}
          <div className="results-stats-row">
            <div className="res-stat-card">
              <span className="res-stat-label">Total Files Processed</span>
              <span className="res-stat-value">{results.total_files}</span>
            </div>

            <div className="res-stat-card success-border">
              <span className="res-stat-label">Succeeded</span>
              <div className="res-stat-val-group text-emerald">
                <CheckCircle2 size={20} />
                <span className="res-stat-value">{results.success_count}</span>
              </div>
            </div>

            <div
              className={`res-stat-card ${
                results.failure_count > 0 ? "failure-border" : ""
              }`}
            >
              <span className="res-stat-label">Failed</span>
              <div
                className={`res-stat-val-group ${
                  results.failure_count > 0 ? "text-rose" : "text-muted"
                }`}
              >
                <XCircle size={20} />
                <span className="res-stat-value">{results.failure_count}</span>
              </div>
            </div>

            <div className="res-stat-card">
              <span className="res-stat-label">Healing Attempts</span>
              <div className="res-stat-val-group text-indigo">
                <Flame size={20} />
                <span className="res-stat-value">
                  {results.total_healing_attempts}
                </span>
              </div>
            </div>
          </div>

          {/* Main Content Area: Split File Navigator + Monaco Diff View */}
          <div className="results-grid">
            {/* Left Column: File List Navigator */}
            <div className="file-nav-panel">
              <div className="file-nav-header">
                <h3>Files ({allFiles.length})</h3>
                <div className="file-nav-filters">
                  <button
                    type="button"
                    className={`nav-filter-chip ${
                      fileFilter === "ALL" ? "active" : ""
                    }`}
                    onClick={() => setFileFilter("ALL")}
                  >
                    All ({allFiles.length})
                  </button>
                  <button
                    type="button"
                    className={`nav-filter-chip ${
                      fileFilter === "SUCCESS" ? "active" : ""
                    }`}
                    onClick={() => setFileFilter("SUCCESS")}
                  >
                    Passed ({results.success_count})
                  </button>
                  {results.failure_count > 0 && (
                    <button
                      type="button"
                      className={`nav-filter-chip failed ${
                        fileFilter === "FAILED" ? "active" : ""
                      }`}
                      onClick={() => setFileFilter("FAILED")}
                    >
                      Failed ({results.failure_count})
                    </button>
                  )}
                </div>
              </div>

              <div className="file-list-wrap">
                {filteredFiles.map((file) => {
                  const isSelected = selectedFile?.file_path === file.file_path;
                  const isFailed = file.status === "FAILED";

                  return (
                    <button
                      key={file.file_path}
                      type="button"
                      className={`file-nav-item ${
                        isSelected ? "active" : ""
                      } ${isFailed ? "item-failed" : "item-success"}`}
                      onClick={() => setSelectedFile(file)}
                    >
                      <div className="item-icon-wrap">
                        {isFailed ? (
                          <XCircle size={15} className="text-rose" />
                        ) : (
                          <CheckCircle2 size={15} className="text-emerald" />
                        )}
                      </div>

                      <div className="item-text">
                        <span className="item-filename" title={file.file_path}>
                          {file.file_path}
                        </span>
                        <div className="item-badges">
                          <span
                            className={`item-status-pill ${
                              isFailed ? "pill-failed" : "pill-success"
                            }`}
                          >
                            {file.status}
                          </span>
                          {file.attempt_count > 0 && (
                            <span className="item-heal-pill">
                              {file.attempt_count}{" "}
                              {file.attempt_count === 1
                                ? "heal"
                                : "heals"}
                            </span>
                          )}
                        </div>
                      </div>

                      <ChevronRight size={14} className="item-arrow" />
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Right Column: Diff & Traceback Display */}
            <div className="diff-display-panel">
              {selectedFile ? (
                <div className="diff-panel-content">
                  {/* If file failed: display red alert with traceback */}
                  {selectedFile.status === "FAILED" && (
                    <div className="file-failure-card">
                      <div className="failure-card-header">
                        <div className="failure-title-group">
                          <XCircle size={20} className="text-rose-400" />
                          <div>
                            <h4>Migration Validation Failed</h4>
                            <span className="failure-sub">
                              File: <code>{selectedFile.file_path}</code> •{" "}
                              {selectedFile.attempt_count} self-healing attempts
                              exhausted
                            </span>
                          </div>
                        </div>

                        {selectedFile.traceback && (
                          <CopyButton
                            text={selectedFile.traceback}
                            label="Copy Traceback"
                          />
                        )}
                      </div>

                      {selectedFile.traceback ? (
                        <div className="traceback-container">
                          <div className="traceback-header">
                            <Terminal size={13} />
                            <span>Pytest Sandbox Traceback</span>
                          </div>
                          <pre className="traceback-content">
                            {selectedFile.traceback}
                          </pre>
                        </div>
                      ) : (
                        <p className="no-traceback-msg">
                          Tests failed in the sandbox container without a
                          captured traceback.
                        </p>
                      )}
                    </div>
                  )}

                  {/* Monaco Split-Diff Editor */}
                  <div className="monaco-section">
                    <div className="monaco-section-header">
                      <div className="section-title-wrap">
                        <Code2 size={16} className="text-indigo-400" />
                        <h3>
                          Split-Diff: <code>{selectedFile.file_path}</code>
                        </h3>
                      </div>
                      <CopyButton
                        text={selectedFile.diff}
                        label="Copy Raw Diff"
                      />
                    </div>

                    <MonacoDiffViewer
                      original={parsedDiff.original}
                      modified={parsedDiff.modified}
                      language="python"
                      filename={selectedFile.file_path}
                      height="540px"
                    />
                  </div>
                </div>
              ) : (
                <div className="no-file-selected">
                  <FileCode size={36} className="text-muted" />
                  <p>Select a file from the list to inspect its split-diff.</p>
                </div>
              )}
            </div>
          </div>

          {/* Git Commands & PR Section */}
          <div className="git-commands-card">
            <div className="git-header">
              <div className="git-title-group">
                <GitBranch size={20} className="text-indigo-400" />
                <div>
                  <h3>Apply Changes Locally</h3>
                  <p>
                    Execute these git commands in your repository root to check
                    out and merge the migration branch{" "}
                    <strong>
                      {gitCommands?.branch_name || job?.branch_name || "migrate"}
                    </strong>
                    .
                  </p>
                  {(job?.workspace_path || gitCommands?.workspace_path) && (
                    <div className="results-workspace-info">
                      <span className="workspace-info-label">
                        <Folder size={13} className="text-indigo-400" />
                        Workspace:
                      </span>
                      <code className="workspace-info-path">
                        {gitCommands?.workspace_path || job?.workspace_path}
                      </code>
                    </div>
                  )}
                </div>
              </div>

              {/* Optional Create PR button for GitHub repos */}
              {isGithub && (
                <div className="github-pr-cta">
                  <button
                    type="button"
                    className="btn-github-pr"
                    onClick={async () => {
                      const prCmd =
                        gitCommands?.pr_command ||
                        `gh pr create --title "Migrate to ${results.target_library}" --body "Automated migration generated by Migration Agent"`;
                      try {
                        await navigator.clipboard.writeText(prCmd);
                      } catch (err) {
                        console.error("Clipboard write failed", err);
                      }
                      setPrCopiedNotice(prCmd);
                    }}
                  >
                    <GitPullRequest size={15} />
                    <span>Create Pull Request</span>
                  </button>
                </div>
              )}
            </div>

            {/* Inline glassmorphic PR notification banner */}
            {prCopiedNotice && (
              <div className="pr-copied-banner">
                <div className="pr-banner-content">
                  <CheckCircle2 size={18} className="text-emerald" />
                  <div>
                    <strong>GitHub CLI command copied to clipboard!</strong>
                    <p>
                      Run this command from your local terminal to create the
                      pull request on GitHub:
                    </p>
                    <code>{prCopiedNotice}</code>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn-banner-dismiss"
                  onClick={() => setPrCopiedNotice(null)}
                  title="Dismiss notification"
                >
                  <X size={15} />
                </button>
              </div>
            )}

            {/* Quick One-Liner Box */}
            {gitCommands?.one_liner && (
              <div className="command-box-highlight">
                <div className="box-header">
                  <span>Quick One-Liner (Apply all changes):</span>
                  <CopyButton text={gitCommands.one_liner} label="Copy One-Liner" />
                </div>
                <code>{gitCommands.one_liner}</code>
              </div>
            )}

            {/* Individual step commands */}
            {gitCommands?.commands && gitCommands.commands.length > 0 && (
              <div className="git-steps-list">
                <h4>Step-by-step commands:</h4>
                <div className="steps-flow">
                  {gitCommands.commands.map((cmd, idx) => (
                    <div key={idx} className="step-row">
                      <span className="step-num">{idx + 1}</span>
                      <code className="step-cmd">{cmd}</code>
                      <CopyButton text={cmd} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
