import { useEffect, useRef } from "react";
import type { TranscriptEntry } from "../types/transcript";

interface TranscriptPanelProps {
  entries: TranscriptEntry[];
  isConnected: boolean;
}

function speakerLabel(speaker: TranscriptEntry["speaker"]): string {
  if (speaker === "user") return "You";
  if (speaker === "agent") return "Agent";
  return "Unknown";
}

export function TranscriptPanel({ entries, isConnected }: TranscriptPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  return (
    <div className="panel-scroll" ref={scrollRef} tabIndex={0} aria-label="Live transcript">
      {entries.length === 0 ? (
        <p className="panel-empty">
          {isConnected
            ? "Listening for speech — transcript will appear here as the conversation happens."
            : "Connect to a session to see the live transcript."}
        </p>
      ) : (
        <ul className="transcript-list">
          {entries.map((entry) => (
            <li key={entry.id} className={`transcript-item transcript-item--${entry.speaker}`}>
              <div className="transcript-item__meta">
                <span className="transcript-item__speaker">{speakerLabel(entry.speaker)}</span>
                {!entry.final && <span className="transcript-item__interim">transcribing…</span>}
              </div>
              <p className="transcript-item__text">{entry.text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
