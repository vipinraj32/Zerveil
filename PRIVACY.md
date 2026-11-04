# Zerveil — Privacy and custody

This document states plainly what Zerveil keeps private, what it can see, and what you are trusting it with. It
describes the software in this repository; it is not a legal privacy policy. If you operate Zerveil, publish your own
privacy policy too.

## Summary

- Payments are **shielded Zcash** transactions. On the blockchain, amounts, senders, receivers and memos are
  encrypted.
- The **escrow is a wallet account run by the Zerveil operator**. It is not a smart contract and not trustless. The
  operator can see everything that account receives and sends, and could, by misbehaving, move its funds.
- **No payout leaves the escrow on code alone:**
  - Deterministic rules decide each payout.
  - By default, an operator must also approve it (`PAYOUT_APPROVAL_REQUIRED=true`).
  - Every decision is recorded in an audit trail.
- A creator's **payout address is private** to that creator and to operators.
- In **simulated mode** (the default), no ZEC moves at all; everything is labeled *simulated*.

## What is on the blockchain

For a shielded Orchard/Sapling transaction, the chain shows that a transaction happened and its fee. It does **not**
show:
- the amount;
- the sender or the recipient address;
- the memo.

Zerveil invoices use one fresh Unified Address per invoice, with shielded receivers only (Orchard and Sapling, no
transparent receiver). Payouts are sent with the `FullPrivacy` policy by default (`ZCASH_PRIVACY_POLICY`).

Privacy also depends on how **you** pay. Paying an invoice from a transparent (`t…`) address reveals your side of the
transaction. Use a shielded wallet balance.

## What Zerveil can see

| Data | Who can see it | Where it is kept |
|---|---|---|
| Your Google email, name, avatar; a creator's YouTube channel id, title and statistics | you; operators; channel facts are shown to advertisers in the directory | database (`users`) |
| A creator's shielded payout address | that creator and operators only. Redacted for advertisers, never in the directory, the A2A Agent Card or MCP tool output | database (`users`, signed deal terms, payout requests) |
| An advertiser's shielded refund address | that advertiser and operators | database (`users`, campaigns, refund payouts) |
| Deal terms and amounts, negotiation transcripts | the advertiser and the creator of that deal; operators | database |
| Invoices: amount, the one-time address, memo `ZRV1:<id>`, status | the payer; for payment links, the creator; operators. The public `/pay/:id` page shows amount, address, memo and status, but not transaction ids or campaign terms | database (`invoices`) |
| Notes received for an invoice (txid, pool, amount, confirmations) | the invoice owner and operators | database (`invoices`), `payment_events` |
| Payouts (amount, recipient address, txid, approvals) | the recipient; the advertiser of the campaign, with creator addresses redacted; operators | database (`payout_requests`), `payment_events` |
| Everything the escrow wallet receives and sends | the operator, through the wallet | the wallet (Zallet) |

The operator holds the escrow account's spending key. That means it can decrypt every note sent to the escrow and
see every payout. That visibility is what lets the server verify payments without trusting what users type.

## What is never stored or exposed

- No user-submitted transaction id is ever accepted as proof of payment. Only the wallet's own scan data counts.
- `/api/status` and the UI never show the wallet RPC URL or credentials. Wallet errors shown to non-operators carry no
  connection details.
- Private keys:
  - Creators' Ed25519 agent keys are stored encrypted with `SESSION_SECRET`.
  - The escrow spending key lives only in the wallet, never in Zerveil's database.
  - No key is ever sent to the browser or to an LLM.
- Read-only MCP tools, which the LLM assistant uses, scrub every Zcash address from their results before they reach
  the model provider.

## Data sent to third parties

- **Google** (sign-in, YouTube Data API): the OAuth exchange and public channel/video lookups.
- **Anthropic** (only if `ANTHROPIC_API_KEY` is set):
  - What the agents' LLM sees: offers, briefs, public channel facts and negotiation text.
  - It never sees payout or refund addresses, keys, or wallet data.
  - The deterministic policy engine, not the LLM, decides every payment.
- **LangSmith** (only if tracing is enabled): traces of the agent runs above.
- **Zcash network:** shielded transactions, as described above.

## Custody: what you are trusting

- **Funds in escrow are controlled by the Zerveil operator.** Shielded Zcash has no general smart contracts to lock
  funds under on-chain rules, so this MVP uses a custodial wallet account.
- **Rules are in code, and approvals are recorded.** Each payout carries the evidence hash it was decided on (terms
  hash, proof checks, views). Approvals, rejections and resolutions are logged with who decided and when.
- **Reconciliation:** the admin custody view compares the wallet's escrow balance with received minus paid-out
  amounts. The fee reserve explains small differences.
- **Lost outcomes are not guessed.** If a send's result is lost, the payout is marked `unknown` and matched against
  the wallet's outgoing memos. It is never resent automatically.
- **Refunds:**
  - Unspent caps go back to the advertiser's refund address.
  - Late or partial payments to expired invoices are refunded once each, minus the network fee reserve, after
    operator approval.

Making the escrow trust-minimized (for example threshold-signed spending) is future work and not claimed here.

## Testnet and simulation

- **Testnet (TAZ) has no monetary value.** The MVP is built and tested for testnet. Running it on mainnet is not
  supported by this repository's tests.
- **Simulated mode:**
  - Uses fake Unified Address strings and `sim…` transaction ids.
  - Nothing is broadcast.
  - Every invoice, payout and deal funded this way is marked `simulated` in the API and UI.

## Retention and deletion

Records are kept in the operator's PostgreSQL database for as long as the operator keeps them. Payment and audit
records are needed to reconcile the escrow and should not be deleted while funds may still move. Deleting a
transaction record from Zerveil cannot remove the transaction from the blockchain.
