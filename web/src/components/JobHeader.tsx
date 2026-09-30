"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  Activity,
  CheckCircle2,
  FolderGit2,
  Library,
  Clock,
} from "lucide-react";
import { JobResponse } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";
import { CopyButton } from "./CopyButton";

interface JobHeaderProps {
  job: JobResponse | null;
  jobId: string;
}

export function JobHeader({ job, jobId }: JobHeaderProps) {
  const pathname = usePathname();

  const tabs = [
    {
      label: "Migration Plan",
      path: `/jobs/${jobId}/plan`,
      icon: <FileText size={16} />,
      badge: job?.plan_files_count ? `${job.plan_files_count} files` : undefined,
    },
    {
      label: "Live Progress",
      path: `/jobs/${jobId}/progress`,
      icon: <Activity size={16} />,
    },
    {
      label: "Results & Diffs",
      path: `/jobs/${jobId}/results`,
      icon: <CheckCircle2 size={16} />,
      badge:
        job?.status === "complete"
          ? `${job.approved_files_count || 0} completed`
          : undefined,
    },
  ];

  const formattedDate = job?.created_at
    ? new Date(job.created_at).toLocaleString()
    : "";

  return (
    <div className="job-header-card">
      <div className="job-header-top">
        <div className="job-title-group">
          <div className="job-title-row">
            <h1 className="job-title">Job {jobId.slice(0, 8)}</h1>
            <CopyButton text={jobId} label="Copy ID" />
            {job && <StatusBadge status={job.status} size="lg" />}
          </div>

          <div className="job-meta-row">
            {job?.source && (
              <div className="job-meta-item">
                <FolderGit2 size={14} className="meta-icon" />
                <span className="meta-label">Source:</span>
                <span className="meta-value" title={job.source}>
                  {job.repo_name || job.source}
                </span>
              </div>
            )}

            {job?.target_library && (
              <div className="job-meta-item">
                <Library size={14} className="meta-icon" />
                <span className="meta-label">Library:</span>
                <span className="meta-value highlight">
                  {job.target_library}
                </span>
              </div>
            )}

            {formattedDate && (
              <div className="job-meta-item">
                <Clock size={14} className="meta-icon" />
                <span className="meta-label">Started:</span>
                <span className="meta-value">{formattedDate}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="job-tabs">
        {tabs.map((tab) => {
          const isActive = pathname === tab.path;
          return (
            <Link
              key={tab.path}
              href={tab.path}
              className={`job-tab-btn ${isActive ? "active" : ""}`}
            >
              {tab.icon}
              <span>{tab.label}</span>
              {tab.badge && <span className="tab-pill">{tab.badge}</span>}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
