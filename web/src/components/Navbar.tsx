"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { PlusCircle, Activity, RefreshCw } from "lucide-react";
import { checkBackendHealth } from "@/lib/api";
import { NewJobModal } from "./NewJobModal";

export function Navbar() {
  const [isHealthy, setIsHealthy] = useState<boolean | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const checkStatus = async () => {
    const ok = await checkBackendHealth();
    setIsHealthy(ok);
  };

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <>
      <nav className="navbar">
        <div className="navbar-container">
          <Link href="/" className="brand">
            <span className="brand-icon">⚡</span>
            <span className="brand-text">Migration Agent</span>
            <span className="brand-version">v1.0</span>
          </Link>

          <div className="navbar-right">
            <div className="nav-links">
              <Link href="/" className="nav-link">
                Dashboard
              </Link>
            </div>

            <div
              className={`status-badge ${
                isHealthy === true
                  ? "online"
                  : isHealthy === false
                  ? "offline"
                  : "checking"
              }`}
              title={
                isHealthy === true
                  ? "Backend API connected"
                  : isHealthy === false
                  ? "Cannot reach FastAPI backend"
                  : "Checking API connection..."
              }
            >
              <span className="pulse-dot" />
              <span>
                {isHealthy === true
                  ? "API Online"
                  : isHealthy === false
                  ? "API Offline"
                  : "Connecting..."}
              </span>
            </div>

            <button
              type="button"
              className="btn-create-job"
              onClick={() => setModalOpen(true)}
            >
              <PlusCircle size={15} />
              <span>New Job</span>
            </button>
          </div>
        </div>
      </nav>

      <NewJobModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}
