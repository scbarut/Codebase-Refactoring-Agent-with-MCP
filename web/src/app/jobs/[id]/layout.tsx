"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams, usePathname } from "next/navigation";
import { fetchJob } from "@/lib/api";
import { JobResponse } from "@/lib/types";
import { Breadcrumbs, BreadcrumbItem } from "@/components/Breadcrumbs";
import { JobHeader } from "@/components/JobHeader";
import { Loader2 } from "lucide-react";

export default function JobLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams();
  const pathname = usePathname();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id as string);

  const [job, setJob] = useState<JobResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadJob = useCallback(async () => {
    if (!id) return;
    try {
      const data = await fetchJob(id);
      setJob(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load job details");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadJob();
    // Poll job status every 4s to keep badge and file counts fresh
    const interval = setInterval(loadJob, 4000);
    return () => clearInterval(interval);
  }, [loadJob]);

  // Determine current page sub-label for breadcrumbs
  let currentTabLabel = "Job Details";
  if (pathname.includes("/plan")) currentTabLabel = "Plan Review";
  else if (pathname.includes("/progress")) currentTabLabel = "Live Progress";
  else if (pathname.includes("/results")) currentTabLabel = "Results & Diffs";

  const breadcrumbItems: BreadcrumbItem[] = [
    {
      label: `Job ${id ? id.slice(0, 8) : "..."}`,
      href: `/jobs/${id}/plan`,
    },
    {
      label: currentTabLabel,
      active: true,
    },
  ];

  return (
    <div className="container">
      <div className="job-page-layout">
        <Breadcrumbs items={breadcrumbItems} />

        <JobHeader job={job} jobId={id} />

        {error && (
          <div className="job-error-banner">
            <span>{error}</span>
          </div>
        )}

        <div className="job-content-area">{children}</div>
      </div>
    </div>
  );
}
