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
}

type PanelTab = "transcript" | "events";

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
}: AppShellProps) {
  const [activeTab, setActiveTab] = useState<PanelTab>("transcript");
  const isConnected = connectionPhase === "connected";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__brand">
          <span className="app-header__mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20">
              <path
                d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 0 0-7 0v5A3.5 3.5 0 0 0 12 15Z"
                fill="currentColor"
              />
              <path
                d="M6.5 11.25a5.5 5.5 0 0 0 11 0M12 17.25V20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <div>
            <p className="app-header__title">Field Voice Console</p>
            <p className="app-header__subtitle">Voice Agent Platform</p>
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

      <ErrorBanner errors={errors} onDismiss={onDismissError} />

      {showSignIn ? (
        <main className="app-main app-main--centered">{signInSlot}</main>
      ) : (
        <main className="app-main app-grid">
          <section className="panel panel--session" aria-label="Voice session">
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
              onConnect={onConnect}
              onDisconnect={onDisconnect}
              onStartMic={onStartMic}
              onStopMic={onStopMic}
              onToggleMute={onToggleMute}
            />
          </section>

          <section className="panel panel--activity" aria-label="Session activity">
            <div className="panel-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "transcript"}
                className={`panel-tabs__tab${activeTab === "transcript" ? " is-active" : ""}`}
                onClick={() => setActiveTab("transcript")}
              >
                Transcript
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "events"}
                className={`panel-tabs__tab${activeTab === "events" ? " is-active" : ""}`}
                onClick={() => setActiveTab("events")}
              >
                Tool events
                {toolEvents.length > 0 && <span className="panel-tabs__badge">{toolEvents.length}</span>}
              </button>
            </div>
            <div className="panel-tabs__panels">
              <div role="tabpanel" hidden={activeTab !== "transcript"}>
                <TranscriptPanel entries={transcript} isConnected={isConnected} />
              </div>
              <div role="tabpanel" hidden={activeTab !== "events"}>
                <ToolEventCard events={toolEvents} isConnected={isConnected} />
              </div>
            </div>
          </section>
        </main>
      )}
    </div>
  );
}
