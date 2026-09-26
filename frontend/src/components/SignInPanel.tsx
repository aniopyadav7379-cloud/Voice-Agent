import { useId, useState } from "react";

interface SignInPanelProps {
  defaultTenantSlug: string;
  defaultExternalId: string;
  isAuthenticating: boolean;
  authError: string | null;
  onSubmit: (tenantSlug: string, externalId: string) => void;
}

const PRESET_PERSONAS = [
  {
    role: "Field Technician",
    icon: "👷",
    tenant: "default",
    userId: "demo-tech",
    badge: "Equipment Repair",
    desc: "Create maintenance tickets & check pump telemetry",
  },
  {
    role: "Dispatch Supervisor",
    icon: "📡",
    tenant: "default",
    userId: "supervisor-1",
    badge: "Operations Command",
    desc: "Dispatch field workers & broadcast emergency alerts",
  },
];

export function SignInPanel({
  defaultTenantSlug,
  defaultExternalId,
  isAuthenticating,
  authError,
  onSubmit,
}: SignInPanelProps) {
  const [selectedPersona, setSelectedPersona] = useState<string>("demo-tech");
  const [tenantSlug, setTenantSlug] = useState(defaultTenantSlug);
  const [externalId, setExternalId] = useState(defaultExternalId);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const tenantFieldId = useId();
  const userFieldId = useId();

  const handleSelectPersona = (tenant: string, userId: string) => {
    setSelectedPersona(userId);
    setTenantSlug(tenant);
    setExternalId(userId);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    onSubmit(tenantSlug.trim() || "default", externalId.trim() || "demo-tech");
  };

  return (
    <div className="portal-card">
      <div className="portal-card__glow-border" aria-hidden="true" />
      <div className="portal-card__content">
        {/* Header Branding */}
        <div className="portal-header">
          <div className="portal-brand-badge">
            <span className="portal-brand-icon">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.2">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 3" />
                <path d="M7 12a5 5 0 0 1 10 0" strokeDasharray="3 2" />
              </svg>
            </span>
            <span className="portal-brand-label">AURA OPS · VOICE AI</span>
          </div>

          <h2 className="portal-title">Field Voice Console</h2>
          <p className="portal-subtitle">
            Autonomous multi-tenant voice agent powered by Sarvam AI, Groq LLM, and LiveKit WebRTC.
          </p>
        </div>

        {/* Feature Highlights Pills */}
        <div className="portal-features-grid">
          <div className="feature-pill">
            <span className="feature-pill__icon">⚡</span>
            <div>
              <strong>Sub-Second Voice</strong>
              <span>Streaming WebSockets</span>
            </div>
          </div>
          <div className="feature-pill">
            <span className="feature-pill__icon">🇮🇳</span>
            <div>
              <strong>Indian Vernacular</strong>
              <span>Sarvam Saaras & Bulbul</span>
            </div>
          </div>
          <div className="feature-pill">
            <span className="feature-pill__icon">🔧</span>
            <div>
              <strong>Live Tool Calling</strong>
              <span>Tickets, Dispatch & Alerts</span>
            </div>
          </div>
        </div>

        {/* Preset Persona Selection */}
        <div className="persona-selection-section">
          <label className="section-label">Select Active Field Identity</label>
          <div className="persona-cards-grid">
            {PRESET_PERSONAS.map((p) => {
              const isSelected = selectedPersona === p.userId;
              return (
                <button
                  key={p.userId}
                  type="button"
                  className={`persona-card ${isSelected ? "is-selected" : ""}`}
                  onClick={() => handleSelectPersona(p.tenant, p.userId)}
                >
                  <div className="persona-card__header">
                    <span className="persona-card__icon">{p.icon}</span>
                    <span className="persona-card__badge">{p.badge}</span>
                  </div>
                  <strong className="persona-card__role">{p.role}</strong>
                  <p className="persona-card__desc">{p.desc}</p>
                </button>
              );
            })}
          </div>
        </div>

        {/* Advanced toggle */}
        <div className="advanced-toggle-row">
          <button
            type="button"
            className="btn-toggle-advanced"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            <span>{showAdvanced ? "▾ Hide manual credentials" : "▸ Custom tenant or user ID"}</span>
          </button>
        </div>

        {/* Form */}
        <form className="portal-form" onSubmit={handleSubmit}>
          {showAdvanced && (
            <div className="advanced-fields-box">
              <label className="field" htmlFor={tenantFieldId}>
                <span className="field__label">Tenant Slug</span>
                <input
                  id={tenantFieldId}
                  className="field__input"
                  value={tenantSlug}
                  onChange={(event) => setTenantSlug(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>

              <label className="field" htmlFor={userFieldId}>
                <span className="field__label">External User ID</span>
                <input
                  id={userFieldId}
                  className="field__input"
                  value={externalId}
                  onChange={(event) => {
                    setExternalId(event.target.value);
                    setSelectedPersona(event.target.value);
                  }}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
            </div>
          )}

          <button
            type="submit"
            className={`btn-portal-submit ${isAuthenticating ? "is-loading" : ""}`}
            disabled={isAuthenticating}
          >
            <span className="btn-portal-submit__shine" aria-hidden="true" />
            <span className="btn-portal-submit__text">
              {isAuthenticating ? (
                <>
                  <span className="button-spinner" />
                  Starting Secure Session…
                </>
              ) : (
                <>
                  <span>Launch Voice Station</span>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <line x1="5" y1="12" x2="19" y2="12" />
                    <polyline points="12 5 19 12 12 19" />
                  </svg>
                </>
              )}
            </span>
          </button>
        </form>

        {authError && <div className="portal-error-card">{authError}</div>}
      </div>
    </div>
  );
}
