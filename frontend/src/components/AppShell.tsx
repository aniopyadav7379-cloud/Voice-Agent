import { useState, type ReactNode } from "react";
import type { ThemePreference } from "../hooks/useTheme";
import type { AgentState, ToolEvent, TranscriptEntry } from "../types/transcript";
import type { AppErrorInfo, MicPhase, SessionPhase } from "../types/livekit";
import { ThemeToggle } from "./ThemeToggle";
import { ConnectionStatus } from "./ConnectionStatus";
import { VoiceVisualizer } from "./VoiceVisualizer";
import { VoiceControls } from "./VoiceControls";
import { TranscriptPanel } from "./TranscriptPanel";
import { ToolEventCard } from "./ToolEventCard";
import { ErrorBanner } from "./ErrorBanner";

interface AppShellProps {
  themePreference: ThemePreference;
  onThemeChange: (preference: ThemePreference) => void;
  connectionPhase: SessionPhase;
  micPhase: MicPhase;
  agentState: AgentState;
  agentIdentity: string | null;
  tenantSlug?: string;
  externalId?: string;
  localAudioLevel: number;
  agentAudioLevel: number;
  transcript: TranscriptEntry[];
  toolEvents: ToolEvent[];
  errors: AppErrorInfo[];
  onDismissError: (id: string) => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onStartMic: () => void;
  onStopMic: () => void;
  onToggleMute: () => void;
  signInSlot: ReactNode;
  showSignIn: boolean;
  isAuthenticating?: boolean;
}

type PanelTab = "transcript" | "events" | "architecture";

export function AppShell({
  themePreference,
  onThemeChange,
  connectionPhase,
  micPhase,
  agentState,
  agentIdentity,
  tenantSlug,
  externalId,
  localAudioLevel,
  agentAudioLevel,
  transcript,
  toolEvents,
  errors,
  onDismissError,
  onConnect,
  onDisconnect,
  onStartMic,
  onStopMic,
  onToggleMute,
  signInSlot,
  showSignIn,
  isAuthenticating = false,
}: AppShellProps) {
  const [activeTab, setActiveTab] = useState<PanelTab>("transcript");
  const isConnected = connectionPhase === "connected";

  return (
    <div className="app-shell">
      {/* Sleek Glassmorphism Top Navigation Header */}
      <header className="app-header">
        <div className="app-header__brand">
          <div className="brand-logo-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
              <circle cx="12" cy="12" r="10" stroke="url(#logo-grad)" strokeWidth="2.5" />
              <path d="M12 7v10M8 10v4M16 9v6" stroke="url(#logo-grad)" strokeWidth="2.5" strokeLinecap="round" />
              <defs>
                <linearGradient id="logo-grad" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
                  <stop stopColor="#6366f1" />
                  <stop offset="0.5" stopColor="#38bdf8" />
                  <stop offset="1" stopColor="#a855f7" />
                </linearGradient>
              </defs>
            </svg>
          </div>
          <div>
            <div className="app-header__title-row">
              <h1 className="app-header__title">AURA OPS</h1>
              <span className="brand-tag">VOICE AI</span>
            </div>
            <p className="app-header__subtitle">Autonomous Industrial Field Operations Platform</p>
          </div>
        </div>

        <div className="app-header__end">
          <ConnectionStatus
            phase={connectionPhase}
            tenantSlug={tenantSlug}
            externalId={externalId}
            agentIdentity={agentIdentity}
          />
          <ThemeToggle preference={themePreference} onChange={onThemeChange} />
        </div>
      </header>

      {/* Global Toast / Error Banner */}
      <ErrorBanner errors={errors} onDismiss={onDismissError} />

      {/* Main Content Stage */}
      {showSignIn ? (
        <main className="app-main app-main--centered">{signInSlot}</main>
      ) : (
        <main className="app-main app-grid">
          {/* Left Stage: Hero Voice Orb & Command Console */}
          <section className="panel panel--voice-stage" aria-label="Hero Voice Console">
            <div className="panel-header-badge">
              <span className="panel-header-badge__dot" />
              <span>LIVE VOICE STATION</span>
            </div>

            <VoiceVisualizer
              agentState={agentState}
              connectionPhase={connectionPhase}
              micPhase={micPhase}
              localAudioLevel={localAudioLevel}
              agentAudioLevel={agentAudioLevel}
            />

            <VoiceControls
              connectionPhase={connectionPhase}
              micPhase={micPhase}
              isAuthenticating={isAuthenticating}
              onConnect={onConnect}
              onDisconnect={onDisconnect}
              onStartMic={onStartMic}
              onStopMic={onStopMic}
              onToggleMute={onToggleMute}
            />
          </section>

          {/* Right Stage: Tabbed Interactive Activity Hub */}
          <section className="panel panel--activity" aria-label="Session Activity">
            <div className="panel-tabs-bar" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "transcript"}
                className={`panel-tab-btn ${activeTab === "transcript" ? "is-active" : ""}`}
                onClick={() => setActiveTab("transcript")}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <span>Live Transcript</span>
                {transcript.length > 0 && (
                  <span className="panel-tab-count">{transcript.length}</span>
                )}
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "events"}
                className={`panel-tab-btn ${activeTab === "events" ? "is-active" : ""}`}
                onClick={() => setActiveTab("events")}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                </svg>
                <span>Tool Executions</span>
                {toolEvents.length > 0 && (
                  <span className="panel-tab-count">{toolEvents.length}</span>
                )}
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "architecture"}
                className={`panel-tab-btn ${activeTab === "architecture" ? "is-active" : ""}`}
                onClick={() => setActiveTab("architecture")}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="12 2 2 7 12 12 22 7 12 2" />
                  <polyline points="2 17 12 22 22 17" />
                  <polyline points="2 12 12 17 22 12" />
                </svg>
                <span>Architecture</span>
              </button>
            </div>

            <div className="panel-content-area">
              <div role="tabpanel" hidden={activeTab !== "transcript"}>
                <TranscriptPanel entries={transcript} isConnected={isConnected} />
              </div>

              <div role="tabpanel" hidden={activeTab !== "events"}>
                <ToolEventCard events={toolEvents} isConnected={isConnected} />
              </div>

              <div role="tabpanel" hidden={activeTab !== "architecture"}>
                <div className="architecture-view">
                  <div className="architecture-header">
                    <h4>Multi-Modal Voice Pipeline</h4>
                    <p>End-to-end low-latency WebRTC voice stack with distributed tool calling</p>
                  </div>

                  <div className="pipeline-steps-grid">
                    <div className="pipeline-card">
                      <div className="pipeline-card__step">01</div>
                      <div className="pipeline-card__icon">🎙️</div>
                      <h5>Browser Audio</h5>
                      <p>Opus WebRTC stream connected to LiveKit Cloud (India South region)</p>
                      <span className="pipeline-tag">16 kHz / Low Latency</span>
                    </div>

                    <div className="pipeline-card">
                      <div className="pipeline-card__step">02</div>
                      <div className="pipeline-card__icon">🇮🇳</div>
                      <h5>Sarvam Saaras v3</h5>
                      <p>Full duplex Indian English & Hinglish Speech-to-Text streaming WebSocket</p>
                      <span className="pipeline-tag">WebSocket STT</span>
                    </div>

                    <div className="pipeline-card">
                      <div className="pipeline-card__step">03</div>
                      <div className="pipeline-card__icon">⚡</div>
                      <h5>Groq Qwen-3.8-27B</h5>
                      <p>Fast LLM reasoning engine with dynamic tool routing and context extraction</p>
                      <span className="pipeline-tag">~200ms TTFT</span>
                    </div>

                    <div className="pipeline-card">
                      <div className="pipeline-card__step">04</div>
                      <div className="pipeline-card__icon">🔊</div>
                      <h5>Sarvam Bulbul v3</h5>
                      <p>Indian English natural neural voice (Shubh voice persona) in 24kHz Linear16</p>
                      <span className="pipeline-tag">Sub-Second TTS</span>
                    </div>

                    <div className="pipeline-card">
                      <div className="pipeline-card__step">05</div>
                      <div className="pipeline-card__icon">🐘</div>
                      <h5>Neon PostgreSQL</h5>
                      <p>Async transaction-pooled database tracking session turns, audit logs, and security events</p>
                      <span className="pipeline-tag">9 Schema Tables</span>
                    </div>

                    <div className="pipeline-card">
                      <div className="pipeline-card__step">06</div>
                      <div className="pipeline-card__icon">🛡️</div>
                      <h5>Security Boundary</h5>
                      <p>HMAC-SHA256 JWT auth + strict single-participant room boundary enforcement</p>
                      <span className="pipeline-tag">Multi-Tenant Isolation</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </main>
      )}
    </div>
  );
}
