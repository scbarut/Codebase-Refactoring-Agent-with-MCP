"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Clock,
  ExternalLink,
  FileCode2,
  FolderGit2,
  Library,
  PlusCircle,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import { fetchJobs } from "@/lib/api";
import { JobResponse, JobStatus } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";
import { NewJobModal } from "@/components/NewJobModal";

export default function DashboardPage() {
  const [jobs, setJobs] = useState<JobResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);

  const loadJobs = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      const data = await fetchJobs();
      setJobs(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load migration jobs");
    } finally {
      setLoading(false);
      if (isManual) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // Polling every 3 seconds if autoRefresh is enabled
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      loadJobs(false);
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, loadJobs]);

  const filteredJobs = jobs.filter((job) => {
    const query = searchQuery.toLowerCase();
    return (
      job.job_id.toLowerCase().includes(query) ||
      job.target_library.toLowerCase().includes(query) ||
      (job.source && job.source.toLowerCase().includes(query)) ||
      (job.repo_name && job.repo_name.toLowerCase().includes(query)) ||
      job.status.toLowerCase().includes(query)
    );
  });

  // Calculate summary counts
  const scanningCount = jobs.filter((j) => j.status === "scanning").length;
  const awaitingCount = jobs.filter(
    (j) => j.status === "awaiting_approval"
  ).length;
  const migratingCount = jobs.filter((j) => j.status === "migrating").length;
  const completeCount = jobs.filter((j) => j.status === "complete").length;

  const formatRelativeTime = (isoString: string) => {
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffSecs = Math.floor(diffMs / 1000);
      if (diffSecs < 10) return "just now";
      if (diffSecs < 60) return `${diffSecs}s ago`;
      const diffMins = Math.floor(diffSecs / 60);
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      return date.toLocaleDateString();
    } catch {
      return isoString;
    }
  };

  const getPrimaryAction = (job: JobResponse) => {
    switch (job.status) {
      case "awaiting_approval":
        return {
          href: `/jobs/${job.job_id}/plan`,
          label: "Review Plan",
          className: "btn-action-approval",
        };
      case "migrating":
      case "scanning":
        return {
          href: `/jobs/${job.job_id}/progress`,
          label: "Live Progress",
          className: "btn-action-progress",
        };
      case "complete":
      case "failed":
        return {
          href: `/jobs/${job.job_id}/results`,
          label: "View Results",
          className: "btn-action-results",
        };
      default:
        return {
          href: `/jobs/${job.job_id}/plan`,
          label: "View Job",
          className: "btn-action-default",
        };
    }
  };

  return (
    <div className="container">
      {/* Top Hero Section */}
      <section className="dashboard-hero">
        <div className="hero-content">
          <div className="hero-badge">
            <Sparkles size={14} />
            <span>HITL Refactoring Gateway</span>
          </div>
          <h1>Autonomous Migration Jobs</h1>
          <p>
            Deterministic static AST scanning, human-in-the-loop plan review, and
            isolated sandbox self-healing powered by Model Context Protocol.
          </p>
        </div>

        <div className="hero-actions">
          <button
            type="button"
            className="btn-primary-glow"
            onClick={() => setIsModalOpen(true)}
          >
            <PlusCircle size={17} />
            <span>New Migration Job</span>
          </button>
        </div>
      </section>

      {/* Summary KPI Cards */}
      <section className="stats-row">
        <div className="stat-card">
          <span className="stat-label">Total Jobs</span>
          <span className="stat-value">{jobs.length}</span>
          <span className="stat-sub">Initiated migrations</span>
        </div>

        <div className={`stat-card ${awaitingCount > 0 ? "highlight-amber" : ""}`}>
          <span className="stat-label">Awaiting Approval</span>
          <span className="stat-value text-amber">{awaitingCount}</span>
          <span className="stat-sub">Ready for HITL review</span>
        </div>

        <div className={`stat-card ${migratingCount > 0 ? "highlight-indigo" : ""}`}>
          <span className="stat-label">Active Running</span>
          <span className="stat-value text-indigo">
            {migratingCount + scanningCount}
          </span>
          <span className="stat-sub">Scanning & rewriting</span>
        </div>

        <div className="stat-card">
          <span className="stat-label">Completed</span>
          <span className="stat-value text-emerald">{completeCount}</span>
          <span className="stat-sub">Successfully migrated</span>
        </div>
      </section>

      {/* Controls Bar: Search, Auto-Refresh Toggle, Manual Refresh */}
      <div className="controls-bar">
        <div className="search-wrap">
          <Search size={16} className="search-icon" />
          <input
            type="text"
            placeholder="Search by job ID, library, repo source..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button
              type="button"
              className="clear-search-btn"
              onClick={() => setSearchQuery("")}
            >
              Clear
            </button>
          )}
        </div>

        <div className="controls-right">
          <button
            type="button"
            className={`toggle-polling-btn ${autoRefresh ? "active" : ""}`}
            onClick={() => setAutoRefresh(!autoRefresh)}
            title={
              autoRefresh ? "Click to pause polling" : "Click to enable auto-refresh"
            }
          >
            <span
              className={`polling-indicator ${autoRefresh ? "polling-on" : ""}`}
            />
            <span>{autoRefresh ? "Polling: 3s" : "Polling: Paused"}</span>
          </button>

          <button
            type="button"
            className="btn-refresh"
            onClick={() => loadJobs(true)}
            disabled={refreshing}
            title="Refresh job list"
          >
            <RefreshCw
              size={15}
              className={refreshing ? "animate-spin" : ""}
            />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="dashboard-error-banner">
          <span>Failed to connect to backend: {error}</span>
          <button
            type="button"
            onClick={() => loadJobs(true)}
            className="btn-retry"
          >
            Retry
          </button>
        </div>
      )}

      {/* Jobs Table or Empty State */}
      {loading ? (
        <div className="loading-state-card">
          <div className="scanner-line" />
          <RefreshCw className="animate-spin text-indigo-400" size={32} />
          <p>Loading migration jobs from backend...</p>
        </div>
      ) : filteredJobs.length === 0 ? (
        <div className="empty-state-card">
          <FileCode2 size={48} className="empty-icon" />
          <h3>
            {searchQuery ? "No matching jobs found" : "No Migration Jobs Yet"}
          </h3>
          <p>
            {searchQuery
              ? `No jobs match "${searchQuery}". Try clearing the search query.`
              : "Start by submitting a local repository path or remote Git URL to scan for deprecated library usage."}
          </p>
          {!searchQuery && (
            <button
              type="button"
              className="btn-primary"
              onClick={() => setIsModalOpen(true)}
            >
              <PlusCircle size={16} />
              <span>Create First Migration Job</span>
            </button>
          )}
        </div>
      ) : (
        <div className="jobs-table-card">
          <div className="table-responsive">
            <table className="jobs-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Target Library</th>
                  <th>Codebase Source</th>
                  <th>Plan & Approved</th>
                  <th>Submitted</th>
                  <th className="th-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((job) => {
                  const action = getPrimaryAction(job);
                  return (
                    <tr key={job.job_id} className="job-row">
                      {/* Status */}
                      <td className="td-status">
                        <StatusBadge status={job.status} size="sm" />
                      </td>

                      {/* Target Library */}
                      <td className="td-library">
                        <div className="library-tag">
                          <Library size={13} />
                          <span>{job.target_library}</span>
                        </div>
                      </td>

                      {/* Codebase Source */}
                      <td className="td-source">
                        <div className="source-info">
                          <span className="source-name" title={job.source}>
                            {job.repo_name || job.source}
                          </span>
                          <span className="job-id-micro">
                            ID: {job.job_id.slice(0, 8)}
                          </span>
                        </div>
                      </td>

                      {/* Files count */}
                      <td className="td-counts">
                        <div className="counts-group">
                          {job.plan_files_count > 0 ? (
                            <span className="count-badge">
                              {job.approved_files_count > 0
                                ? `${job.approved_files_count} / ${job.plan_files_count} approved`
                                : `${job.plan_files_count} files planned`}
                            </span>
                          ) : job.scanned_files_count > 0 ? (
                            <span className="count-badge muted">
                              {job.scanned_files_count} files scanned
                            </span>
                          ) : (
                            <span className="count-badge muted">
                              Scanning...
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Time */}
                      <td className="td-time">
                        <div
                          className="time-info"
                          title={new Date(job.created_at).toLocaleString()}
                        >
                          <Clock size={12} className="time-icon" />
                          <span>{formatRelativeTime(job.created_at)}</span>
                        </div>
                      </td>

                      {/* Actions */}
                      <td className="td-actions">
                        <div className="action-links">
                          <Link
                            href={action.href}
                            className={`btn-action-primary ${action.className}`}
                          >
                            <span>{action.label}</span>
                            <ArrowRight size={13} />
                          </Link>

                          <div className="quick-links">
                            <Link
                              href={`/jobs/${job.job_id}/plan`}
                              className="quick-link-btn"
                              title="Migration Plan"
                            >
                              Plan
                            </Link>
                            <Link
                              href={`/jobs/${job.job_id}/progress`}
                              className="quick-link-btn"
                              title="Live Progress Stream"
                            >
                              Live
                            </Link>
                            <Link
                              href={`/jobs/${job.job_id}/results`}
                              className="quick-link-btn"
                              title="Results & Diffs"
                            >
                              Results
                            </Link>
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* New Job Modal */}
      <NewJobModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onJobCreated={() => loadJobs(false)}
      />
    </div>
  );
}
