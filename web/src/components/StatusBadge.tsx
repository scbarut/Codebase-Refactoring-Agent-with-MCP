import React from "react";
import { JobStatus } from "@/lib/types";

interface StatusBadgeProps {
  status: JobStatus | string;
  size?: "sm" | "md" | "lg";
}

export function StatusBadge({ status, size = "md" }: StatusBadgeProps) {
  const norm = (status || "").toLowerCase();

  const config: Record<
    string,
    { label: string; className: string; pulse: boolean; icon: string }
  > = {
    scanning: {
      label: "Scanning",
      className: "status-scanning",
      pulse: true,
      icon: "🔍",
    },
    awaiting_approval: {
      label: "Awaiting Approval",
      className: "status-awaiting",
      pulse: true,
      icon: "⏳",
    },
    migrating: {
      label: "Migrating",
      className: "status-migrating",
      pulse: true,
      icon: "⚡",
    },
    complete: {
      label: "Complete",
      className: "status-complete",
      pulse: false,
      icon: "✓",
    },
    failed: {
      label: "Failed",
      className: "status-failed",
      pulse: false,
      icon: "✕",
    },
  };

  const item = config[norm] || {
    label: status,
    className: "status-unknown",
    pulse: false,
    icon: "•",
  };

  const sizeClass =
    size === "sm" ? "badge-sm" : size === "lg" ? "badge-lg" : "badge-md";

  return (
    <span className={`status-badge-custom ${item.className} ${sizeClass}`}>
      {item.pulse ? (
        <span className="badge-pulse-dot" />
      ) : (
        <span className="badge-icon">{item.icon}</span>
      )}
      <span className="badge-text">{item.label}</span>
    </span>
  );
}
