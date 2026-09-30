"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Clock,
  Download,
  FileCode,
  Flame,
  HelpCircle,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Terminal,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import { getWsUrl } from "@/lib/api";
import { StreamEvent } from "@/lib/types";

export default function LiveProgressPage() {
  const params = useParams();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id as string);

  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<
    "connecting" | "connected" | "disconnected"
  >("connecting");
  const [autoScroll, setAutoScroll] = useState(true);
  const [activeStep, setActiveStep] = useState<string>("Initializing");
  const [currentFile, setCurrentFile] = useState<string | null>(null);
  const [testsPassed, setTestsPassed] = useState(0);
  const [testsFailed, setTestsFailed] = useState(0);
  const [healingAttempts, setHealingAttempts] = useState(0);
  const [jobStatus, setJobStatus] = useState<string | null>(null);

  const logContainerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Auto-scroll handler
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  const connectWebSocket = useCallback(() => {
    if (!id) return;

    if (wsRef.current) {
      wsRef.current.close();
    }

    setConnectionStatus("connecting");
    const wsUrl = getWsUrl(id);
    const socket = new WebSocket(wsUrl);
    wsRef.current = socket;

    socket.onopen = () => {
      setConnectionStatus("connected");

      // Set up ping heartbeat every 15s
      pingIntervalRef.current = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send("ping");
        }
      }, 15000);
    };

    socket.onmessage = (msgEvent) => {
      try {
        const data = JSON.parse(msgEvent.data);
        if (data.type === "pong") return;

        const event = data as StreamEvent;
        setEvents((prev) => [...prev, event]);

        // Update real-time metrics
        if (event.status) setJobStatus(event.status);
        if (event.step) setActiveStep(event.step);
        if (event.file_path) setCurrentFile(event.file_path);

        if (event.type === "file_tests_executed" || event.passed !== undefined) {
          if (event.passed === true) {
            setTestsPassed((p) => p + 1);
          } else if (event.passed === false) {
            setTestsFailed((f) => f + 1);
          }
        }

        if (
          event.type === "file_healing_attempt" ||
          (event.attempt != null && event.attempt > 0)
        ) {
          setHealingAttempts((h) => h + 1);
        }
      } catch (err) {
        console.error("Error parsing WebSocket event", err);
      }
    };

    socket.onerror = (err) => {
      console.warn("WebSocket error:", err);
      setConnectionStatus("disconnected");
    };

    socket.onclose = () => {
      setConnectionStatus("disconnected");
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
      }
      // Reconnect after 3s if not complete or unmounted
      reconnectTimeoutRef.current = setTimeout(() => {
        connectWebSocket();
      }, 3000);
    };
  }, [id]);

  useEffect(() => {
    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [connectWebSocket]);

  const clearLogs = () => {
    setEvents([]);
  };

  const downloadLogs = () => {
    const text = events
      .map(
        (e) =>
          `[${e.timestamp}] [${e.type.toUpperCase()}] ${
            e.file_path ? `(${e.file_path}) ` : ""
          }${e.message || JSON.stringify(e)}`
      )
      .join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `migration-job-${id}-logs.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const getEventBadge = (type: string) => {
    const t = type.toLowerCase();
    if (t.includes("scan")) return <span className="log-tag tag-blue">SCAN</span>;
    if (t.includes("hitl") || t.includes("gateway") || t.includes("plan"))
      return <span className="log-tag tag-amber">HITL</span>;
    if (t.includes("test")) return <span className="log-tag tag-purple">TEST</span>;
    if (t.includes("heal")) return <span className="log-tag tag-orange">HEAL</span>;
    if (t.includes("complete") || t.includes("success"))
      return <span className="log-tag tag-green">DONE</span>;
    if (t.includes("fail") || t.includes("error"))
      return <span className="log-tag tag-red">ERROR</span>;
    return <span className="log-tag tag-default">{type}</span>;
  };

  const formatLogTime = (isoString?: string) => {
    if (!isoString) return "";
    try {
      const d = new Date(isoString);
      return d.toTimeString().split(" ")[0]; // HH:MM:SS
    } catch {
      return isoString;
    }
  };

  return (
    <div className="progress-page">
      {/* State Transitions Banners */}
      {jobStatus === "awaiting_approval" && (
        <div className="stream-alert-banner alert-amber">
          <div className="alert-content">
            <Sparkles size={20} className="text-amber-400" />
            <div>
              <h4>HITL Gateway Reached — Plan Ready for Review</h4>
              <p>
                Static AST scanning has completed. Review the proposed
                refactoring operations and approve the files to proceed.
              </p>
            </div>
          </div>
          <Link href={`/jobs/${id}/plan`} className="btn-alert-action">
            Review & Approve Plan <ArrowRight size={14} />
          </Link>
        </div>
      )}

      {jobStatus === "complete" && (
        <div className="stream-alert-banner alert-emerald">
          <div className="alert-content">
            <CheckCircle2 size={20} className="text-emerald-400" />
            <div>
              <h4>Migration Completed Successfully!</h4>
              <p>
                All approved files have been rewritten and validated in the
                Docker pytest sandbox. You can now inspect diffs and copy git
                commands.
              </p>
            </div>
          </div>
          <Link href={`/jobs/${id}/results`} className="btn-alert-action">
            View Results & Diffs <ArrowRight size={14} />
          </Link>
        </div>
      )}

      {/* Metrics Row */}
      <div className="live-metrics-row">
        <div className="live-metric-card">
          <div className="metric-icon-wrap icon-indigo">
            <Activity size={18} />
          </div>
          <div className="metric-info">
            <span className="metric-label">Current Step</span>
            <span className="metric-value highlight">{activeStep}</span>
          </div>
        </div>

        <div className="live-metric-card">
          <div className="metric-icon-wrap icon-blue">
            <FileCode size={18} />
          </div>
          <div className="metric-info">
            <span className="metric-label">Active File</span>
            <span className="metric-value" title={currentFile || "None"}>
              {currentFile || "Idle / Orchestrating"}
            </span>
          </div>
        </div>

        <div className="live-metric-card">
          <div className="metric-icon-wrap icon-emerald">
            <CheckCircle2 size={18} />
          </div>
          <div className="metric-info">
            <span className="metric-label">Tests Passed</span>
            <span className="metric-value text-emerald">{testsPassed}</span>
          </div>
        </div>

        <div className="live-metric-card">
          <div className="metric-icon-wrap icon-rose">
            <Flame size={18} />
          </div>
          <div className="metric-info">
            <span className="metric-label">Healing Attempts</span>
            <span className="metric-value text-amber">{healingAttempts}</span>
          </div>
        </div>
      </div>

      {/* Terminal Log Console */}
      <div className="terminal-card">
        {/* Terminal Header */}
        <div className="terminal-header">
          <div className="terminal-dots">
            <span className="dot red" />
            <span className="dot yellow" />
            <span className="dot green" />
            <span className="terminal-title">
              <Terminal size={14} />
              <span>Migration Event Stream</span>
            </span>
          </div>

          <div className="terminal-controls">
            <div className={`ws-status-chip ${connectionStatus}`}>
              {connectionStatus === "connected" ? (
                <>
                  <Wifi size={13} />
                  <span>Streaming Live</span>
                </>
              ) : connectionStatus === "connecting" ? (
                <>
                  <RotateCcw size={13} className="animate-spin" />
                  <span>Connecting...</span>
                </>
              ) : (
                <>
                  <WifiOff size={13} />
                  <span>Disconnected</span>
                </>
              )}
            </div>

            <button
              type="button"
              className={`console-btn ${autoScroll ? "active" : ""}`}
              onClick={() => setAutoScroll(!autoScroll)}
              title={autoScroll ? "Pause autoscroll" : "Resume autoscroll"}
            >
              {autoScroll ? <Pause size={13} /> : <Play size={13} />}
              <span>{autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}</span>
            </button>

            <button
              type="button"
              className="console-btn"
              onClick={downloadLogs}
              title="Download raw event logs"
            >
              <Download size={13} />
              <span>Export</span>
            </button>

            <button
              type="button"
              className="console-btn"
              onClick={clearLogs}
              title="Clear viewer display"
            >
              <span>Clear</span>
            </button>
          </div>
        </div>

        {/* Terminal Body */}
        <div className="terminal-body" ref={logContainerRef}>
          {events.length === 0 ? (
            <div className="terminal-empty">
              <RotateCcw className="animate-spin text-muted" size={24} />
              <p>Waiting for WebSocket stream events from FastAPI...</p>
            </div>
          ) : (
            <div className="log-entries">
              {events.map((ev, index) => (
                <div key={index} className="log-row">
                  <span className="log-time">
                    {formatLogTime(ev.timestamp)}
                  </span>
                  <span className="log-type-wrap">
                    {getEventBadge(ev.type)}
                  </span>

                  {ev.file_path && (
                    <span className="log-filepath" title={ev.file_path}>
                      {ev.file_path}
                    </span>
                  )}

                  <span className="log-msg">
                    {ev.message ||
                      (ev.error && `Error: ${ev.error}`) ||
                      (ev.status && `Status updated to: ${ev.status}`) ||
                      (ev.step && `Step: ${ev.step}`) ||
                      JSON.stringify(ev)}
                  </span>

                  {ev.attempt != null && ev.attempt > 0 && (
                    <span className="log-badge-heal">
                      attempt {ev.attempt}/{ev.max_attempts || 3}
                    </span>
                  )}

                  {ev.passed === true && (
                    <span className="log-badge-test pass">test passed</span>
                  )}
                  {ev.passed === false && (
                    <span className="log-badge-test fail">
                      test failed (code {ev.exit_code})
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
