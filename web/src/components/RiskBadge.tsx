import React from "react";
import { RiskLevel } from "@/lib/types";

interface RiskBadgeProps {
  risk: RiskLevel | string;
}

export function RiskBadge({ risk }: RiskBadgeProps) {
  const norm = (risk || "LOW").toUpperCase();

  const config: Record<
    string,
    { label: string; className: string; description: string }
  > = {
    LOW: {
      label: "LOW RISK",
      className: "risk-low",
      description:
        "Deterministic 1-to-1 AST rewrite (e.g. parameter rename, method alias). No semantic behavior change.",
    },
    MEDIUM: {
      label: "MED RISK",
      className: "risk-medium",
      description:
        "Structural transformation with minor syntax adjustments (e.g. @validator to @field_validator, classmethod wrapping).",
    },
    HIGH: {
      label: "HIGH RISK",
      className: "risk-high",
      description:
        "Significant structural change or signature rewrite (e.g. @root_validator to @model_validator, Config to ConfigDict).",
    },
  };

  const item = config[norm] || {
    label: norm,
    className: "risk-low",
    description: "Standard migration risk category.",
  };

  return (
    <span
      className={`risk-badge ${item.className}`}
      title={`${item.label}: ${item.description}`}
    >
      <span className="risk-indicator" />
      <span>{item.label}</span>
      <span className="risk-tooltip">{item.description}</span>
    </span>
  );
}
