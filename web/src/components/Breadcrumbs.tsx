import React from "react";
import Link from "next/link";
import { ChevronRight, Home } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  href?: string;
  active?: boolean;
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <Link href="/" className="breadcrumb-item breadcrumb-home">
        <Home size={14} />
        <span>Dashboard</span>
      </Link>
      {items.map((item, idx) => (
        <React.Fragment key={idx}>
          <ChevronRight size={13} className="breadcrumb-separator" />
          {item.href && !item.active ? (
            <Link href={item.href} className="breadcrumb-item">
              {item.label}
            </Link>
          ) : (
            <span className="breadcrumb-item active">{item.label}</span>
          )}
        </React.Fragment>
      ))}
    </nav>
  );
}
