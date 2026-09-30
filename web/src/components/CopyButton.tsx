"use client";

import React, { useState } from "react";
import { Check, Copy } from "lucide-react";

interface CopyButtonProps {
  text: string;
  label?: string;
  className?: string;
}

export function CopyButton({ text, label, className = "" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy", err);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`copy-btn ${copied ? "copied" : ""} ${className}`}
      title={copied ? "Copied!" : "Copy to clipboard"}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {label && <span>{copied ? "Copied!" : label}</span>}
    </button>
  );
}
