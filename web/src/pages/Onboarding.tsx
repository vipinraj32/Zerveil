import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { api } from "../api";
import { AgentSettingsForm } from "../components/AgentSettingsForm";
import { Avatar, Card, ErrorText } from "../components/ui";
import { ReceivingAccountPanel } from "../components/ReceivingAccount";
import { ZcashAddressForm } from "../components/ZcashAddressForm";
import { num } from "../format";
import { useMe, useSession } from "../session";

function Step({ n, title, done, active, children }: { n: number; title: string; done: boolean; active: boolean; children?: React.ReactNode }) {
  return (
    <li className={`step ${done ? "done" : ""} ${active ? "active" : ""}`}>
      <div className="step-head">
        <span className="step-n">{done ? "✓" : n}</span>
        <h3>{title}</h3>
      </div>
      {active && <div className="step-body">{children}</div>}
    </li>
  );
}

function AdvertiserProfile() {
  const { me, setMe } = useSession();
  const navigate = useNavigate();
  const [company, setCompany] = useState(me!.user.company);
  const [handle, setHandle] = useState(me!.user.brand_handle || "@");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setMe(await api.saveAdvertiser(company, handle));
      navigate("/advertiser");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <div className="grid-2">
        <label>
          Brand / company name
          <input value={company} onChange={(e) => setCompany(e.target.value)} required maxLength={80} />
        </label>
        <label>
          Brand handle
          <input value={handle} onChange={(e) => setHandle(e.target.value)} pattern="@[A-Za-z0-9_.]{2,40}" required />
          <small>Creators must mention it in the video description.</small>
        </label>
      </div>
      <button className="btn btn-primary">Finish setup</button>
      <ErrorText error={error} />
    </form>
  );
}

export function Onboarding() {
  const { user, profile_complete } = useMe();
  const navigate = useNavigate();
  if (profile_complete && user.role === "advertiser") return <Navigate to="/advertiser" replace />;

  if (user.role === "advertiser") {
    return (
      <Card title="Set up your advertiser account" className="narrow">
        <ol className="steps">
          <Step n={1} title={`Signed in with Google as ${user.email}`} done active={false} />
          <Step n={2} title="Brand details" done={profile_complete} active={!profile_complete}>
            <AdvertiserProfile />
            <p className="muted small">You pay creators directly from your own Zcash wallet; Zerveil never holds your money.</p>
          </Step>
        </ol>
      </Card>
    );
  }

  const ch = user.channel;
  const addressDone = !!user.zcash_address && !!user.delegation_signature;
  const receivingDone = !!user.receiving_account_id;
  return (
    <Card title="Set up your creator agent" className="narrow">
      <ol className="steps">
        <Step n={1} title="Google + YouTube channel verified" done={!!ch} active={!ch}>
          <p>Sign out and use “Continue as an influencer” to grant read-only YouTube access.</p>
        </Step>
        {ch && (
          <div className="channel-card">
            <Avatar src={ch.thumbnail_url} name={ch.title} size={48} />
            <div>
              <strong>{ch.title}</strong>
              <div className="muted small">
                {num(ch.subscribers)} subscribers · <span className="mono">{ch.channel_id}</span>
              </div>
            </div>
          </div>
        )}
        <Step n={2} title="Add your own shielded address (Unified Address)" done={addressDone} active={!!ch && !addressDone}>
          <ZcashAddressForm submitLabel="Save address" />
        </Step>
        <Step n={3} title="Connect your receiving account (viewing key)" done={receivingDone} active={addressDone && !receivingDone}>
          <p className="small">
            Advertisers pay you directly, into your own wallet. Connect a viewing key so Zerveil can confirm those payments; you are not listed for deals
            until you do.
          </p>
          <ReceivingAccountPanel />
        </Step>
        <Step n={4} title="Set your rates and switch your agent on" done={profile_complete} active={addressDone && receivingDone && !profile_complete}>
          <AgentSettingsForm submitLabel="Activate my agent" onSaved={() => navigate("/influencer")} />
        </Step>
      </ol>
      {profile_complete && (
        <button type="button" className="btn btn-primary" onClick={() => navigate("/influencer")}>
          Go to my deals
        </button>
      )}
    </Card>
  );
}
