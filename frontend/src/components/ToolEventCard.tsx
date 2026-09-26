import type { ToolEvent } from "../types/transcript";

interface ToolEventCardProps {
  events: ToolEvent[];
  isConnected: boolean;
}

const TOOL_ICONS: Record<string, string> = {
  create_ticket: "🎫",
  dispatch_worker: "👷",
  retrieve_context: "🧠",
  send_notification: "🔔",
  update_status: "🔄",
};

export function ToolEventCard({ events, isConnected }: ToolEventCardProps) {
  return (
    <div className="tool-events-container">
      <div className="tool-events-header">
        <span className="tool-events-header__title">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
          Operational Tool Executions
        </span>
        <span className="tool-events-header__count">{events.length} actions executed</span>
      </div>

      <div className="tool-events-feed" tabIndex={0} aria-label="Tool execution logs">
        {events.length === 0 ? (
          <div className="tool-empty-state">
            <div className="tool-empty-icon">
              <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
            </div>
            <h4 className="tool-empty-title">
              {isConnected ? "Awaiting Tool Triggers" : "Tools Standby"}
            </h4>
            <p className="tool-empty-text">
              When you ask the voice assistant to create tickets, dispatch personnel, or retrieve context, real-time backend tool events will be displayed here with validation and execution status.
            </p>
          </div>
        ) : (
          <div className="tool-cards-list">
            {events.map((event) => {
              const icon = TOOL_ICONS[event.name] || "⚙️";
              const isSuccess = event.status === "succeeded";
              const isRunning = event.status === "started";
              const isFailed = event.status === "failed";

              return (
                <div
                  key={event.id}
                  className={`tool-event-card tool-event-card--${event.status}`}
                >
                  <div className="tool-event-card__top">
                    <div className="tool-event-card__title-group">
                      <span className="tool-event-card__icon">{icon}</span>
                      <div>
                        <span className="tool-event-card__name">{event.name}</span>
                        <span className="tool-event-card__time">
                          {new Date(event.receivedAt).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })}
                        </span>
                      </div>
                    </div>

                    <span className={`tool-status-pill tool-status-pill--${event.status}`}>
                      {isRunning && <span className="tool-spinner" />}
                      {isSuccess && (
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      )}
                      {isFailed && (
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3">
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      )}
                      <span>
                        {isRunning ? "Executing" : isSuccess ? "Success" : isFailed ? "Failed" : "Event"}
                      </span>
                    </span>
                  </div>

                  {event.detail && (
                    <div className="tool-event-card__body">
                      <p className="tool-event-card__detail">{event.detail}</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
