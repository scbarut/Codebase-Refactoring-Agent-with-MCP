export type JobStatus =
  | "scanning"
  | "awaiting_approval"
  | "migrating"
  | "complete"
  | "failed";

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export interface JobResponse {
  job_id: string;
  source: string;
  target_library: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  error?: string | null;
  workspace_path?: string | null;
  repo_name?: string | null;
  base_branch?: string | null;
  branch_name?: string | null;
  scanned_files_count: number;
  plan_files_count: number;
  approved_files_count: number;
}

export interface JobCreateRequest {
  path_or_url: string;
  target_library: string;
  workspace_base_dir?: string | null;
}

export interface JobCreateResponse {
  job_id: string;
  status: JobStatus;
}

export interface JobApproveRequest {
  approved_files: string[];
}

export interface JobApproveResponse {
  job_id: string;
  status: JobStatus;
  approved_files: string[];
}

export interface MatchedRule {
  rule_id: string;
  old_qualified_name: string;
  new_qualified_name: string;
  risk: RiskLevel;
  transformer_class?: string | null;
}

export interface AffectedNode {
  symbol_name: string;
  node_type: string;
  start_line: number;
  end_line: number;
}

export interface FilePlanEntry {
  file_path: string;
  matched_rules: MatchedRule[];
  affected_nodes: AffectedNode[];
  risk: RiskLevel;
}

export type FileStatus = "SUCCESS" | "FAILED";

export interface FileResult {
  file_path: string;
  status: FileStatus;
  diff: string;
  traceback: string;
  attempt_count: number;
}

export interface GitCommands {
  commands?: string[];
  one_liner?: string;
  patch_command?: string;
  workspace_path?: string;
  branch_name?: string;
  remote_name?: string;
  pr_command?: string | null;
  is_github?: boolean;
}

export interface MigrationResult {
  job_id: string;
  target_library: string;
  total_files: number;
  successful_files: FileResult[];
  failed_files: FileResult[];
  success_count: number;
  failure_count: number;
  total_healing_attempts: number;
  full_diff: string;
  git_commands: GitCommands;
}

export interface StreamEvent {
  type: string;
  job_id: string;
  timestamp: string;
  message?: string | null;
  status?: string | null;
  step?: string | null;
  file_path?: string | null;
  files_found?: number | null;
  plan_count?: number | null;
  approved_files?: string[] | null;
  attempt?: number | null;
  max_attempts?: number | null;
  passed?: boolean | null;
  exit_code?: number | null;
  error?: string | null;
  result?: any | null;
  is_final?: boolean;
}
