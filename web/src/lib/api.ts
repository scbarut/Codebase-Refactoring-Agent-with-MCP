import {
  FilePlanEntry,
  JobApproveRequest,
  JobApproveResponse,
  JobCreateRequest,
  JobCreateResponse,
  JobResponse,
  MigrationResult,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function getWsUrl(jobId: string): string {
  if (typeof window !== "undefined") {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    try {
      const parsed = new URL(base);
      const wsProtocol = parsed.protocol === "https:" ? "wss:" : "ws:";
      return `${wsProtocol}//${parsed.host}/api/jobs/${jobId}/stream`;
    } catch {
      const loc = window.location;
      const wsProtocol = loc.protocol === "https:" ? "wss:" : "ws:";
      return `${wsProtocol}//${loc.hostname}:8000/api/jobs/${jobId}/stream`;
    }
  }
  return `ws://localhost:8000/api/jobs/${jobId}/stream`;
}

export async function fetchJobs(): Promise<JobResponse[]> {
  const res = await fetch(`${API_BASE}/api/jobs`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch jobs (${res.status} ${res.statusText})`);
  }
  return res.json();
}

export async function fetchJob(id: string): Promise<JobResponse> {
  const res = await fetch(`${API_BASE}/api/jobs/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error(`Job '${id}' not found`);
    }
    throw new Error(`Failed to fetch job ${id} (${res.status})`);
  }
  return res.json();
}

export async function createJob(
  payload: JobCreateRequest
): Promise<JobCreateResponse> {
  const res = await fetch(`${API_BASE}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to create job (${res.status})`);
  }
  return res.json();
}

export async function fetchJobPlan(id: string): Promise<FilePlanEntry[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${id}/plan`, {
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 409) {
      const err = await res.json().catch(() => ({ detail: "Plan not ready" }));
      const error = new Error(err.detail || "Plan not ready");
      (error as any).status = 409;
      throw error;
    }
    throw new Error(`Failed to fetch plan for job ${id} (${res.status})`);
  }
  return res.json();
}

export async function approveJob(
  id: string,
  approvedFiles: string[]
): Promise<JobApproveResponse> {
  const payload: JobApproveRequest = { approved_files: approvedFiles };
  const res = await fetch(`${API_BASE}/api/jobs/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to approve job (${res.status})`);
  }
  return res.json();
}

export async function fetchJobResults(id: string): Promise<MigrationResult> {
  const res = await fetch(`${API_BASE}/api/jobs/${id}/results`, {
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 409) {
      const err = await res
        .json()
        .catch(() => ({ detail: "Results not yet available" }));
      const error = new Error(err.detail || "Results not yet available");
      (error as any).status = 409;
      throw error;
    }
    throw new Error(`Failed to fetch results for job ${id} (${res.status})`);
  }
  return res.json();
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`, {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}
