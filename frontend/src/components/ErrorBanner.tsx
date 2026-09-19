import type { AppErrorInfo } from "../types/livekit";

interface ErrorBannerProps {
  errors: AppErrorInfo[];
  onDismiss: (id: string) => void;
}

export function ErrorBanner({ errors, onDismiss }: ErrorBannerProps) {
  if (errors.length === 0) return null;

  return (
    <div className="error-stack" role="alert" aria-live="assertive">
      {errors.map((error) => (
        <div key={error.id} className="error-banner">
          <div className="error-banner__body">
            <p className="error-banner__title">{error.title}</p>
            <p className="error-banner__message">{error.message}</p>
          </div>
          <button
            type="button"
            className="error-banner__dismiss"
            onClick={() => onDismiss(error.id)}
            aria-label="Dismiss error"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                d="M3 3l10 10M13 3L3 13"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
