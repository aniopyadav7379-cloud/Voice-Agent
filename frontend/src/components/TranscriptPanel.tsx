import { useEffect, useRef, useState } from "react";
import type { TranscriptEntry } from "../types/transcript";

interface TranscriptPanelProps {
  entries: TranscriptEntry[];
  isConnected: boolean;
}

export function TranscriptPanel({ entries, isConnected }: TranscriptPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [entries]);

  const handleCopy = () => {
    if (entries.length === 0) return;
    const text = entries
      .map(
        (e) =>
          `[${new Date(e.firstReceivedAt).toLocaleTimeString()}] ${
            e.speaker === "user" ? "You" : "AI Agent"
          }: ${e.text}`
      )
      .join("\n\n");
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="transcript-container">
      {/* Transcript Header bar */}
      <div className="transcript-header-bar">
        <div className="transcript-header-bar__left">
          <span className="transcript-header-bar__badge">
            <span className="live-pulse-dot" />
            LIVE TRANSCRIPT
          </span>
          <span className="transcript-count">{entries.length} turns recorded</span>
        </div>
        {entries.length > 0 && (
          <button
            type="button"
            className="btn-copy-transcript"
            onClick={handleCopy}
            title="Copy conversation transcript"
          >
            {copied ? (
              <>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#10b981" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <span>Copied!</span>
              </>
            ) : (
              <>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
                <span>Copy</span>
              </>
            )}
          </button>
        )}
      </div>

      {/* Main Conversation Feed */}
      <div className="transcript-feed" ref={scrollRef} tabIndex={0} aria-label="Conversation stream">
        {entries.length === 0 ? (
          <div className="transcript-empty-state">
            <div className="transcript-empty-icon">
              <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <h4 className="transcript-empty-title">
              {isConnected ? "Listening for Voice Input" : "Session Inactive"}
            </h4>
            <p className="transcript-empty-text">
              {isConnected
                ? "Start speaking in English or Hindi. Real-time transcripts with Sarvam STT and Groq responses will appear here."
                : "Connect to the session to stream conversations and trigger automated field operations."}
            </p>
          </div>
        ) : (
          <div className="transcript-bubbles-list">
            {entries.map((entry) => {
              const isUser = entry.speaker === "user";
              return (
                <div
                  key={entry.id}
                  className={`chat-bubble-row ${isUser ? "chat-bubble-row--user" : "chat-bubble-row--agent"}`}
                >
                  <div className="chat-avatar" aria-hidden="true">
                    {isUser ? (
                      <span className="avatar-letter user-letter">U</span>
                    ) : (
                      <span className="avatar-letter ai-letter">AI</span>
                    )}
                  </div>
                  <div className="chat-bubble-content">
                    <div className="chat-bubble-meta">
                      <span className="chat-speaker-name">
                        {isUser ? "You (Field Worker)" : "Field AI Agent"}
                      </span>
                      <span className="chat-timestamp">
                        {new Date(entry.firstReceivedAt).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </span>
                      {!entry.final && (
                        <span className="chat-streaming-badge">
                          <span className="wave-dot" />
                          <span className="wave-dot" />
                          <span className="wave-dot" />
                          streaming…
                        </span>
                      )}
                    </div>
                    <div className={`chat-message-card ${isUser ? "card-user" : "card-agent"}`}>
                      <p className="chat-message-text">{entry.text}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
