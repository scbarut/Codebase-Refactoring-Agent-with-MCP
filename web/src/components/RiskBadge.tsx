import React from "react";
import { RiskLevel } from "@/lib/types";

interface RiskBadgeProps {
  risk: RiskLevel | string;
}

export function RiskBadge({ risk }: RiskBadgeProps) {
  const norm = (risk || "LOW").toUpperCase();

  const config: Record<string, { label: string; className: string }> = {
    LOW: { label: "LOW RISK", className: "risk-low" },
    MEDIUM: { label: "MED RISK", className: "risk-medium" },
    HIGH: { label: "HIGH RISK", className: "risk-high" },
  };

  const item = config[norm] || { label: norm, className: "risk-low" };

  return (
    <span className={`risk-badge ${item.className}`}>
      <span className="risk-indicator" />
      {item.label}
    </span>
  );
}
