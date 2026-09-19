import { useId, useState } from "react";

interface SignInPanelProps {
  defaultTenantSlug: string;
  defaultExternalId: string;
  isAuthenticating: boolean;
  authError: string | null;
  onSubmit: (tenantSlug: string, externalId: string) => void;
}

export function SignInPanel({
  defaultTenantSlug,
  defaultExternalId,
  isAuthenticating,
  authError,
  onSubmit,
}: SignInPanelProps) {
  const [tenantSlug, setTenantSlug] = useState(defaultTenantSlug);
  const [externalId, setExternalId] = useState(defaultExternalId);
  const tenantFieldId = useId();
  const userFieldId = useId();

  return (
    <div className="sign-in-panel">
      <div className="sign-in-panel__body">
        <h2 className="sign-in-panel__title">Start a session</h2>
        <p className="sign-in-panel__lead">
          Issues a development token from the backend and connects to a fresh LiveKit room.
        </p>
        <form
          className="sign-in-panel__form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(tenantSlug.trim() || "default", externalId.trim() || "demo-user");
          }}
        >
          <label className="field" htmlFor={tenantFieldId}>
            <span className="field__label">Tenant slug</span>
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
            <span className="field__label">External user id</span>
            <input
              id={userFieldId}
              className="field__input"
              value={externalId}
              onChange={(event) => setExternalId(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <button type="submit" className="btn btn--primary btn--wide" disabled={isAuthenticating}>
            {isAuthenticating ? "Starting session…" : "Start session"}
          </button>
        </form>
        {authError && <p className="sign-in-panel__error">{authError}</p>}
      </div>
    </div>
  );
}
