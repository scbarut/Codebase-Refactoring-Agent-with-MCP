"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  FileCode2,
  Filter,
  Folder,
  GitBranch,
  Loader2,
  Play,
  Radar,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import { approveJob, fetchJob, fetchJobPlan } from "@/lib/api";
import { FilePlanEntry, JobResponse, RiskLevel } from "@/lib/types";
import { RiskBadge } from "@/components/RiskBadge";
import { CopyButton } from "@/components/CopyButton";

export default function PlanReviewPage() {
  const params = useParams();
  const router = useRouter();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id as string);

  const [job, setJob] = useState<JobResponse | null>(null);
  const [plan, setPlan] = useState<FilePlanEntry[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [isScanning, setIsScanning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [riskFilter, setRiskFilter] = useState<string>("ALL");
  const [customBranch, setCustomBranch] = useState<string>("");
  const [selectedTransformer, setSelectedTransformer] = useState<{
    ruleId: string;
    className: string;
    description?: string;
  } | null>(null);

  const loadData = useCallback(async () => {
    if (!id) return;
    try {
      const jobData = await fetchJob(id);
      setJob(jobData);
      setCustomBranch((prev) => {
        if (prev) return prev;
        return (
          jobData.branch_name ||
          `migrate/${jobData.target_library ? jobData.target_library.toLowerCase().replace(/[^a-z0-9_\-\.]/g, "-") : "v2"}-v2`
        );
      });

      if (jobData.status === "scanning") {
        setIsScanning(true);
        setPlan([]);
        setLoading(false);
        return;
      }

      // If job is ready or past scanning, fetch the plan
      try {
        const planData = await fetchJobPlan(id);
        setPlan(planData);
        setIsScanning(false);
        // Pre-check all files by default as requested in Ticket 11
        setSelectedFiles(new Set(planData.map((entry) => entry.file_path)));
        setError(null);
      } catch (planErr: any) {
        if (planErr.status === 409 || planErr.message?.includes("scanning")) {
          setIsScanning(true);
        } else {
          setError(planErr.message || "Failed to load migration plan");
        }
      }
    } catch (err: any) {
      setError(err.message || "Failed to fetch job");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // If scanning, poll every 2.5 seconds until ready
  useEffect(() => {
    if (!isScanning) return;
    const interval = setInterval(() => {
      loadData();
    }, 2500);
    return () => clearInterval(interval);
  }, [isScanning, loadData]);

  const toggleSelectAll = () => {
    const displayedFiles = filteredPlan.map((entry) => entry.file_path);
    const allSelected = displayedFiles.every((f) => selectedFiles.has(f));

    const next = new Set(selectedFiles);
    if (allSelected) {
      displayedFiles.forEach((f) => next.delete(f));
    } else {
      displayedFiles.forEach((f) => next.add(f));
    }
    setSelectedFiles(next);
  };

  const toggleFile = (filePath: string) => {
    const next = new Set(selectedFiles);
    if (next.has(filePath)) {
      next.delete(filePath);
    } else {
      next.add(filePath);
    }
    setSelectedFiles(next);
  };

  const toggleExpand = (filePath: string) => {
    const next = new Set(expandedRows);
    if (next.has(filePath)) {
      next.delete(filePath);
    } else {
      next.add(filePath);
    }
    setExpandedRows(next);
  };

  const handleApprove = async () => {
    if (selectedFiles.size === 0) return;
    try {
      setApproving(true);
      setError(null);
      const filesToApprove = Array.from(selectedFiles);
      await approveJob(id, filesToApprove, customBranch || undefined);
      // Immediately navigate to live progress page to observe execution
      router.push(`/jobs/${id}/progress`);
    } catch (err: any) {
      setError(err.message || "Approval failed");
      setApproving(false);
    }
  };

  const filteredPlan = plan.filter((entry) => {
    if (riskFilter === "ALL") return true;
    return entry.risk === riskFilter;
  });

  const isAwaitingApproval = job?.status === "awaiting_approval";
  const isAlreadyApproved =
    job?.status === "migrating" ||
    job?.status === "complete" ||
    job?.status === "failed";

  // Calculate statistics
  const highRiskCount = plan.filter((p) => p.risk === "HIGH").length;
  const mediumRiskCount = plan.filter((p) => p.risk === "MEDIUM").length;
  const lowRiskCount = plan.filter((p) => p.risk === "LOW").length;
  const totalRewrites = plan.reduce(
    (acc, p) => acc + (p.affected_nodes?.length || 0),
    0
  );

  return (
    <div className="plan-page">
      {/* Scanning State */}
      {isScanning ? (
        <div className="scanning-state-card">
          <div className="scanning-radar-wrap">
            <Radar className="radar-icon animate-pulse" size={54} />
            <div className="radar-sweep" />
          </div>
          <h2>AST Scanning Codebase...</h2>
          <p>
            The static analysis engine is traversing Python source files,
            building Concrete Syntax Trees (CST), matching deprecation rules for{" "}
            <strong>{job?.target_library || "the target library"}</strong>, and
            calculating risk scores.
          </p>
          <div className="scanning-meta">
            <span className="scanning-pulse-pill">
              <span className="pulse-dot" /> Auto-updating every 2.5s
            </span>
            <Link
              href={`/jobs/${id}/progress`}
              className="btn-link-stream"
            >
              Watch Live Stream <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      ) : loading ? (
        <div className="loading-state-card">
          <Loader2 className="animate-spin text-indigo-400" size={32} />
          <p>Loading migration plan...</p>
        </div>
      ) : error ? (
        <div className="plan-error-card">
          <AlertTriangle className="text-amber-400" size={24} />
          <div className="error-body">
            <h3>Unable to Load Plan</h3>
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
      ) : plan.length === 0 ? (
        <div className="empty-state-card">
          <CheckCircle2 size={48} className="text-emerald-400" />
          <h3>No Deprecations Detected</h3>
          <p>
            Static analysis scanned the codebase and found no deprecated symbol
            usages for <strong>{job?.target_library}</strong>. All files match
            modern specifications.
          </p>
          <Link href="/" className="btn-secondary">
            Back to Dashboard
          </Link>
        </div>
      ) : (
        <>
          {/* Status Alert if Already Approved or Migrating */}
          {isAlreadyApproved && (
            <div className="plan-notice-banner">
              <div className="notice-left">
                <CheckCircle2 size={18} className="text-indigo-400" />
                <span>
                  <strong>Plan Already Approved</strong> — This job has moved to{" "}
                  <strong>{job?.status}</strong> status with{" "}
                  {job?.approved_files_count || plan.length} approved files.
                </span>
              </div>
              <div className="notice-actions">
                <Link
                  href={`/jobs/${id}/progress`}
                  className="btn-notice-action"
                >
                  Live Progress <ArrowRight size={13} />
                </Link>
                {job?.status === "complete" && (
                  <Link
                    href={`/jobs/${id}/results`}
                    className="btn-notice-action highlight"
                  >
                    View Results <ArrowRight size={13} />
                  </Link>
                )}
              </div>
            </div>
          )}

          {/* Workspace & Branch Configuration Card */}
          <div className="workspace-meta-card">
            <div className="workspace-meta-grid">
              <div className="workspace-meta-col">
                <div className="workspace-meta-header">
                  <Folder size={16} className="text-indigo-400" />
                  <span className="workspace-meta-title">
                    Isolated Workspace Directory
                  </span>
                </div>
                <div className="workspace-meta-content">
                  <div className="path-code-box">
                    <span
                      className="path-text"
                      title={job?.workspace_path || ""}
                    >
                      {job?.workspace_path || "Preparing workspace..."}
                    </span>
                    {job?.workspace_path && (
                      <CopyButton
                        text={job.workspace_path}
                        label="Copy"
                        className="btn-copy-mini"
                      />
                    )}
                  </div>
                  <span className="workspace-hint">
                    Refactored code is generated in this isolated directory. Your
                    original repository is untouched until you apply/merge.
                  </span>
                </div>
              </div>

              <div className="workspace-meta-col">
                <div className="workspace-meta-header">
                  <GitBranch size={16} className="text-cyan-400" />
                  <span className="workspace-meta-title">
                    Target Git Branch
                  </span>
                </div>
                <div className="workspace-meta-content">
                  {isAwaitingApproval ? (
                    <div className="branch-input-wrapper">
                      <input
                        type="text"
                        className="branch-input"
                        value={customBranch}
                        onChange={(e) => setCustomBranch(e.target.value)}
                        placeholder="e.g. migrate/pydantic-v2"
                        spellCheck={false}
                      />
                      <span className="workspace-hint">
                        Target branch for migration commits (editable before
                        approval).
                      </span>
                    </div>
                  ) : (
                    <div className="branch-display-wrapper">
                      <code className="branch-badge">
                        {job?.branch_name || customBranch || "migrate/v2"}
                      </code>
                      <span className="workspace-hint">
                        Migration branch created and checked out in workspace.
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Plan Summary Row */}
          <div className="plan-summary-row">
            <div className="plan-stats-bar">
              <div className="plan-stat">
                <span className="stat-num">{plan.length}</span>
                <span className="stat-desc">Files Planned</span>
              </div>
              <div className="plan-stat">
                <span className="stat-num">{totalRewrites}</span>
                <span className="stat-desc">AST Rewrites</span>
              </div>
              <div className="plan-stat">
                <span className="stat-num text-rose">{highRiskCount}</span>
                <span className="stat-desc">High Risk</span>
              </div>
              <div className="plan-stat">
                <span className="stat-num text-amber">{mediumRiskCount}</span>
                <span className="stat-desc">Medium Risk</span>
              </div>
              <div className="plan-stat">
                <span className="stat-num text-emerald">{lowRiskCount}</span>
                <span className="stat-desc">Low Risk</span>
              </div>
            </div>

            {/* Filter and Selection Controls */}
            <div className="plan-filters">
              <div className="risk-filter-chips">
                <Filter size={14} className="filter-icon" />
                {["ALL", "HIGH", "MEDIUM", "LOW"].map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={`filter-chip ${
                      riskFilter === level ? "active" : ""
                    }`}
                    onClick={() => setRiskFilter(level)}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Plan Table Card */}
          <div className="plan-table-card">
            <div className="plan-table-header">
              <div className="header-selection-info">
                <label className="checkbox-wrap">
                  <input
                    type="checkbox"
                    checked={
                      filteredPlan.length > 0 &&
                      filteredPlan.every((f) => selectedFiles.has(f.file_path))
                    }
                    onChange={toggleSelectAll}
                    disabled={!isAwaitingApproval}
                  />
                  <span className="custom-check" />
                </label>
                <span className="selection-count">
                  <strong>{selectedFiles.size}</strong> of {plan.length} files
                  selected
                </span>
              </div>

              {isAwaitingApproval && (
                <button
                  type="button"
                  className="btn-approve-primary"
                  onClick={handleApprove}
                  disabled={approving || selectedFiles.size === 0}
                >
                  {approving ? (
                    <>
                      <Loader2 className="animate-spin" size={16} />
                      <span>Submitting Approval...</span>
                    </>
                  ) : (
                    <>
                      <Check size={16} />
                      <span>
                        Approve & Start Migration ({selectedFiles.size} files)
                      </span>
                    </>
                  )}
                </button>
              )}
            </div>

            <div className="table-responsive">
              <table className="plan-table">
                <thead>
                  <tr>
                    <th className="th-checkbox"></th>
                    <th>File Path</th>
                    <th>Risk Level</th>
                    <th>Rewrites</th>
                    <th>Matched Rules</th>
                    <th className="th-toggle"></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPlan.map((entry) => {
                    const isSelected = selectedFiles.has(entry.file_path);
                    const isExpanded = expandedRows.has(entry.file_path);
                    const rewritesCount = entry.affected_nodes?.length || 0;

                    return (
                      <React.Fragment key={entry.file_path}>
                        <tr
                          className={`plan-row ${
                            isSelected ? "row-selected" : ""
                          }`}
                          onClick={() => {
                            if (isAwaitingApproval) {
                              toggleFile(entry.file_path);
                            }
                          }}
                        >
                          <td
                            className="td-checkbox"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <label className="checkbox-wrap">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleFile(entry.file_path)}
                                disabled={!isAwaitingApproval}
                              />
                              <span className="custom-check" />
                            </label>
                          </td>

                          <td className="td-filepath">
                            <div className="file-path-row">
                              <FileCode2 size={16} className="file-icon" />
                              <span
                                className="file-name"
                                title={entry.file_path}
                              >
                                {entry.file_path}
                              </span>
                            </div>
                          </td>

                          <td className="td-risk">
                            <RiskBadge risk={entry.risk} />
                          </td>

                          <td className="td-rewrites">
                            <span className="rewrite-badge">
                              {rewritesCount}{" "}
                              {rewritesCount === 1 ? "rewrite" : "rewrites"}
                            </span>
                          </td>

                          <td className="td-rules">
                            <div className="rules-chip-group">
                              {entry.matched_rules?.slice(0, 2).map((r) => (
                                <span
                                  key={r.rule_id}
                                  className="rule-chip"
                                  title={`${r.old_qualified_name} → ${r.new_qualified_name}`}
                                >
                                  {r.rule_id}
                                </span>
                              ))}
                              {entry.matched_rules?.length > 2 && (
                                <span className="rules-more-chip">
                                  +{entry.matched_rules.length - 2} more
                                </span>
                              )}
                            </div>
                          </td>

                          <td
                            className="td-toggle"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleExpand(entry.file_path);
                            }}
                          >
                            <button
                              type="button"
                              className="btn-expand-row"
                              title="Toggle rule details"
                            >
                              {isExpanded ? (
                                <ChevronDown size={16} />
                              ) : (
                                <ChevronRight size={16} />
                              )}
                            </button>
                          </td>
                        </tr>

                        {/* Expanded details row */}
                        {isExpanded && (
                          <tr className="plan-detail-row">
                            <td colSpan={6}>
                              <div className="plan-detail-card">
                                <div className="detail-section">
                                  <h4>Matched Rules ({entry.matched_rules?.length || 0})</h4>
                                  <div className="rules-list">
                                    {entry.matched_rules?.map((rule) => (
                                      <div
                                        key={rule.rule_id}
                                        className="rule-item"
                                      >
                                        <div className="rule-item-header">
                                          <span className="rule-id">
                                            {rule.rule_id}
                                          </span>
                                          <RiskBadge risk={rule.risk} />
                                        </div>
                                        <div className="rule-transition">
                                          <span className="old-sym">
                                            {rule.old_qualified_name}
                                          </span>
                                          <span className="arrow">→</span>
                                          <span className="new-sym">
                                            {rule.new_qualified_name}
                                          </span>
                                        </div>
                                        {rule.transformer_class && (
                                          <div className="transformer-trigger-wrap">
                                            <button
                                              type="button"
                                              className="transformer-chip-btn"
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                setSelectedTransformer({
                                                  ruleId: rule.rule_id,
                                                  className: rule.transformer_class!,
                                                  description: rule.description ?? undefined,
                                                });
                                              }}
                                              title="Click to view full transformer class details"
                                            >
                                              <Code2 size={13} />
                                              <span>
                                                Transformer: {rule.transformer_class.split(".").pop()}
                                              </span>
                                            </button>
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                </div>

                                {entry.affected_nodes &&
                                  entry.affected_nodes.length > 0 && (
                                    <div className="detail-section">
                                      <h4>
                                        AST Target Nodes (
                                        {entry.affected_nodes.length})
                                      </h4>
                                      <div className="nodes-list">
                                        {entry.affected_nodes.map(
                                          (node, nIdx) => (
                                            <div
                                              key={nIdx}
                                              className="node-pill"
                                            >
                                              <span className="node-type">
                                                {node.node_type}
                                              </span>
                                              <span className="node-name">
                                                {node.symbol_name}
                                              </span>
                                              <span className="node-lines">
                                                lines {node.start_line}-
                                                {node.end_line}
                                              </span>
                                            </div>
                                          )
                                        )}
                                      </div>
                                    </div>
                                  )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Transformer Details Modal Dialog */}
      {selectedTransformer && (
        <div
          className="transformer-modal-overlay"
          onClick={() => setSelectedTransformer(null)}
        >
          <div
            className="transformer-modal-card"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="transformer-modal-header">
              <h3>
                <Code2 className="text-indigo-400" size={18} />
                <span>CST Transformer Details</span>
              </h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setSelectedTransformer(null)}
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <div className="transformer-modal-body">
              <div className="transformer-meta-item">
                <span className="transformer-meta-label">Associated Rule</span>
                <span className="transformer-meta-val">
                  {selectedTransformer.ruleId}
                </span>
              </div>

              <div className="transformer-meta-item">
                <span className="transformer-meta-label">Qualified Class Path</span>
                <div className="transformer-class-box">
                  <code className="transformer-class-code">
                    {selectedTransformer.className}
                  </code>
                  <CopyButton
                    text={selectedTransformer.className}
                    label="Copy"
                    className="transformer-copy-btn"
                  />
                </div>
              </div>

              {selectedTransformer.description && (
                <div className="transformer-meta-item">
                  <span className="transformer-meta-label">
                    Transformation Behavior
                  </span>
                  <p className="transformer-desc-text">
                    {selectedTransformer.description}
                  </p>
                </div>
              )}

              <div className="transformer-note-box">
                💡 <strong>LibCST Transformer:</strong> Mechanically transforms the
                Python AST node deterministically without requiring an LLM call.
              </div>
            </div>
            <div className="transformer-modal-footer">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setSelectedTransformer(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
