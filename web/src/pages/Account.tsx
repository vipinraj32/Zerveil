import { WalletStatusPanel } from "../components/Payments";
import { ReceivingAccountPanel } from "../components/ReceivingAccount";
import { Avatar, Card } from "../components/ui";
import { ZcashAddressForm } from "../components/ZcashAddressForm";
import { num, when } from "../format";
import { useMe } from "../session";

export function Account() {
  const { user } = useMe();
  return (
    <div className="stack narrow">
      <h1>Account</h1>
      <Card title="Google account">
        <div className="who-cell">
          <Avatar src={user.picture} name={user.name} size={44} />
          <div>
            <strong>{user.name}</strong>
            <div className="muted small">
              {user.email} · {user.role} · since {when(user.created_at)}
            </div>
          </div>
        </div>
      </Card>
      {user.role === "influencer" && user.channel && (
        <Card title="Verified YouTube channel">
          <p>
            <strong>{user.channel.title}</strong> · {num(user.channel.subscribers)} subscribers · <span className="mono">{user.channel.channel_id}</span>
          </p>
          <p className="muted small">
            Verified {when(user.channel.verified_at)} through Google OAuth. Sign in again with “Continue as an influencer” to refresh your subscriber count.
          </p>
        </Card>
      )}
      {user.role === "advertiser" && (
        <Card title="Brand">
          <p>
            {user.company || "—"} <span className="muted">{user.brand_handle}</span>
          </p>
        </Card>
      )}
      <Card title="Your Zcash address">
        <ZcashAddressForm />
      </Card>
      {user.role === "influencer" && (
        <Card title="Receiving account (viewing key)">
          <ReceivingAccountPanel />
        </Card>
      )}
      <Card title="Network and scanner">
        <WalletStatusPanel />
      </Card>
      {user.role === "influencer" && user.agent_signer && (
        <Card title="Agent signing key">
          <p className="mono small">{user.agent_signer}</p>
          <p className="muted small">Your creator agent signs deal terms with this key. It was authorized when you saved your shielded address.</p>
        </Card>
      )}
    </div>
  );
}
