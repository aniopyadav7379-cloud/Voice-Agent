import type { ToolEvent } from "../types/transcript";

interface ToolEventCardProps {
  events: ToolEvent[];
  isConnected: boolean;
}

const STATUS_LABEL: Record<ToolEvent["status"], string> = {
  started: "Running",
  succeeded: "Done",
  failed: "Failed",
  unknown: "Event",
};

function EventRow({ event }: { event: ToolEvent }) {
  return (
    <li className={`tool-event tool-event--${event.status}`}>
      <div className="tool-event__header">
        <span className="tool-event__name">{event.name}</span>
        <span className="tool-event__status">{STATUS_LABEL[event.status]}</span>
      </div>
      {event.detail && <p className="tool-event__detail">{event.detail}</p>}
      <span className="tool-event__time">{new Date(event.receivedAt).toLocaleTimeString()}</span>
    </li>
  );
}

export function ToolEventCard({ events, isConnected }: ToolEventCardProps) {
  return (
    <div className="panel-scroll" aria-label="Tool and action events">
      {events.length === 0 ? (
        <p className="panel-empty">
          {isConnected
            ? "No tool activity yet. Actions the agent takes during this session will be listed here."
            : "Connect to a session to see tool and action activity."}
        </p>
      ) : (
        <ul className="tool-event-list">
          {events.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      )}
    </div>
  );
}
