import { Link } from "react-router";
import { api } from "../../api";
import { Avatar, Card, Empty, ErrorText, Notice, Spinner, Stat } from "../../components/ui";
import { NEEDS_PROOF, awaitingFor, dealCurrency, receivedFor } from "../../creatorDeals";
import { LEGACY_ASSET, formatUsd, humanize, isUsd, money, num, sumUsd } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";
import { DealCard } from "./MyDeals";
import "../advertiser/dashboard.css";
import "./deals.css";

export function InfluencerHome() {
  const me = useMe();
  const user = me.user;
  const deals = useResource(api.influencerDeals, "my-deals");
  const inbox = useResource(api.influencerInbox, "inbox");
  useLiveEvents(
    () => true, // the stream only carries events for this user's own deals
    () => {
      void deals.reload();
      void inbox.reload();
    },
  );

  const list = deals.data ?? [];
  // USD totals over USD-priced deals, in integer cents; legacy deals (test units) are not added in
  const usdDeals = list.filter((d) => isUsd(dealCurrency(d)));
  const earned = sumUsd(usdDeals.map(receivedFor));
  const requested = sumUsd(usdDeals.map(awaitingFor));
  const todo = list.filter((d) => NEEDS_PROOF.has(d.state));

  return (
    <div className="stack">
      <div className="page-head">
        <div className="who-cell">
          <Avatar src={user.channel?.thumbnail_url} name={user.channel?.title ?? user.name} size={44} />
          <div>
            <h1>{user.channel?.title}</h1>
            <p className="muted small">
              {num(user.channel?.subscribers)} subscribers · {(user.niches?.length ? user.niches : [user.niche]).join(", ")} ·{" "}
              {me.agent_listed ? <span className="chip good">agent live</span> : <span className="chip warn">agent paused</span>}
            </p>
          </div>
        </div>
        <Link to="/influencer/agent" className="btn btn-secondary">
          Agent settings
        </Link>
      </div>

      {todo.length > 0 && (
        <Notice tone="warn">
          {todo.length === 1 ? "1 deal is" : `${todo.length} deals are`} paid and waiting for your video.{" "}
          <Link to="/influencer/deals?status=accepted">Open them</Link>
        </Notice>
      )}

      {!user.receiving_account_id && (
        <Notice tone="warn">
          Connect a viewing key of your receiving account so payments to your wallet can be confirmed. You are not listed for new deals until you do.{" "}
          <Link to="/influencer/payments">Connect it</Link>
        </Notice>
      )}

      {!me.agent_listed && (
        <Notice tone="warn">
          Your agent is not discoverable. <Link to="/influencer/agent">Switch it on</Link> so advertiser agents can negotiate with you.
        </Notice>
      )}

      <div className="stats">
        <Stat label="Confirmed in your wallet" value={formatUsd(earned)} hint="Milestone payments the scanner confirmed (USD value). Not your wallet balance." />
        <Stat label="Requested from brands" value={formatUsd(requested)} hint="Payment requests to your wallet not yet confirmed" />
        <Stat label="Deals" value={list.length} />
        <Stat label="Need your video" value={todo.length} />
      </div>

      <div className="dash-grid">
        <Card
          title="Recent deals"
          actions={
            list.length > 0 && (
              <Link to="/influencer/deals" className="btn btn-ghost btn-sm">
                View all
              </Link>
            )
          }
        >
          <ErrorText error={deals.error} />
          {deals.loading && !deals.data ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Empty>No deals yet. When an advertiser's agent contacts yours, the negotiation appears here live.</Empty>
          ) : (
            <ul className="md-list">
              {list.slice(0, 4).map((d) => (
                <DealCard key={d.id} deal={d} />
              ))}
            </ul>
          )}
        </Card>

        <Card title="Agent inbox (A2A)">
          <p className="muted small">Payment notices your agent received from advertiser agents.</p>
          {!inbox.data?.length ? (
            <Empty>Nothing yet.</Empty>
          ) : (
            <ul className="inbox">
              {inbox.data.map((m, i) => (
                <li key={i}>
                  <strong>{humanize(String(m.type))}</strong> · <span className="mono">{String(m.deal_id)}</span>
                  {m.usd_amount !== undefined ? (
                    <>
                      {" "}
                      · {humanize(String(m.kind))} {formatUsd(String(m.usd_amount))}
                    </>
                  ) : (
                    m.amount !== undefined && (
                      <>
                        {" "}
                        · {humanize(String(m.kind))} {money(String(m.amount), LEGACY_ASSET)}
                      </>
                    )
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
