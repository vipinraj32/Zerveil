# Zerveil — Privacy and custody

This document states plainly what Zerveil keeps private, what it can see, and what you are trusting it with. It
describes the software in this repository; it is not a legal privacy policy. If you operate Zerveil, publish your own
privacy policy too.

## Summary

- **Zerveil holds no funds.** There is no escrow, operator wallet, payout queue or refund. Zerveil has no spending key
  and cannot move, lock, hold or return anyone's money.
- **Payments are direct and shielded.** An advertiser pays each creator from the advertiser's own wallet to a fresh
  shielded Unified Address of the creator's own wallet account. HTTP 402 API calls are paid directly to the API
  seller's own account.
- **Zerveil confirms payments with a viewing key.** A creator connects the Unified Full Viewing Key (UFVK) of a
  dedicated receiving account, after reading and accepting an authorization statement. That key can see the account's
  transactions; it cannot spend.
- **The network is Zcash testnet (TAZ), which has no real monetary value.**
- In **simulated mode** (the default), nothing touches the blockchain and every record is labeled *simulated*.

## What is on the blockchain

For a shielded transaction (Ironwood after NU7, Orchard or Sapling), the chain shows that a transaction happened and
its fee. It does **not** show the amount, the sender, the recipient address or the memo.

Each invoice uses its own diversified Unified Address of the recipient's account, with shielded receivers only
(`ZCASH_RECEIVER_TYPES=orchard,sapling`; no transparent receiver). Without the viewing key, two such addresses cannot
be linked to each other or to the creator's profile address.

Privacy also depends on how **you** pay. Paying from a transparent (`t…`) balance reveals your side of the
transaction. Pay from a shielded balance, with a wallet that uses ZIP-317 fees and builds current (v5/v6)
transactions.

## The viewing key: what you give Zerveil

A Unified Full Viewing Key lets its holder:
- see every note the account **receives** (amount, memo, the receiving address, the transaction id);
- see what the account **sends** (amounts, recipients and memos of its outgoing transactions);
- derive new receiving addresses for the account.

It cannot sign or spend. Zerveil refuses seed phrases, spending keys and keys for the wrong network at the API; it
never asks for them.

Because a full viewing key also reveals outgoing transactions, **connect a dedicated receiving account** that you use
only for Zerveil, not your main wallet account. Move funds out of it whenever you like; Zerveil will see that
transfer too.

Where the key is kept:
- In `PAYMENT_MODE=zcash`, the UFVK is imported into the operator's view-only zcash-devtool wallet directory
  (`ZCASH_DEVTOOL_WALLET_DIR`). That directory must stay private to the operator.
- The database stores only a SHA-256 fingerprint of the key, the birthday height, the account id in the scanner, and
  when you authorized or revoked it. The key itself is never in the database, the browser, logs, error messages or
  LLM prompts. Scanner errors are redacted before they are logged.

Disconnecting stops Zerveil from issuing invoices to that account and from recording payments for you. It does **not**
yet remove the imported key from the operator's view-only wallet: the operator must run
`zcash-devtool wallet -w <dir> delete-account <account uuid>` (the uuid is the record's `scanner_account` in the `receiving_accounts` table).
Until then the operator could still see that account's transactions. If that matters to you, stop using the account.

## What Zerveil can see

| Data | Who can see it | Where it is kept |
|---|---|---|
| Your Google email, name, avatar; a creator's YouTube channel id, title and statistics | you; operators; channel facts are shown to advertisers in the directory | database (`users`) |
| A creator's profile Unified Address (named in signed deal terms) | that creator and operators only. Redacted for advertisers, never in the directory, the A2A Agent Card or MCP tool output | database (`users`, signed deal terms) |
| A creator's receiving-account record: key fingerprint, birthday, authorization time | that creator and operators | database (`receiving_accounts`) |
| Deal terms and amounts, milestones, negotiation transcripts | the advertiser and the creator of that deal; operators | database |
| Invoices: amount, the one-time address, memo `ZRV1:<id>`, status, confirmations | the payer, the recipient, operators. The public `/pay/:id` page shows amount, address, memo and status, but not transaction ids or campaign terms | database (`invoices`) |
| Notes received for an invoice (txid, pool, amount, confirmations) | payer, recipient, operators | database (`invoices`), `payment_events` |
| Receipts (amount, pools, txids, mined height, receipt hash) | payer, recipient, operators | database (`payment_receipts`) |
| Other transactions of a connected receiving account | technically visible to the operator's scanner; Zerveil records only notes that match one of its invoices | the operator's view-only wallet directory |

The operator never holds a spending key for anyone.

## What is never stored or exposed

- No user-submitted transaction id is accepted as proof of payment. Only notes the scanner itself finds on the
  invoice's own address (or carrying the invoice's `ZRV1:` memo) count, each claimed once.
- Unconfirmed payments are never treated as confirmed: an invoice is `confirmed` only at
  `ZCASH_CONFIRMATION_THRESHOLD` confirmations (default 3).
- `/api/status` and `/api/wallet/status` never show the wallet directory, keys or secrets.
- Private keys:
  - Creators' Ed25519 agent keys are stored encrypted with `SESSION_SECRET`.
  - No Zcash spending key exists anywhere in Zerveil.
  - No key is ever sent to the browser or to an LLM.
- Read-only MCP tools, which the LLM assistant uses, scrub every Zcash address from their results before they reach
  the model provider.

## Receipts

A receipt records what the scanner saw: amount received, pools, transaction ids, mined height, confirmations, the
memo, and the campaign/deal/milestone it settles. Its `receipt_hash` is a SHA-256 over those fields (excluding the
changing confirmation count), so either party can check that a receipt was not altered. Receipts contain no
addresses beyond what the invoice already showed the parties, and no keys.

## Data sent to third parties

- **Google** (sign-in, YouTube Data API): the OAuth exchange and public channel/video lookups.
- **Anthropic** (only if `ANTHROPIC_API_KEY` is set): offers, briefs, public channel facts and negotiation text. Never
  addresses, viewing keys, agent keys or wallet data. The deterministic policy engine, not the LLM, decides every
  amount.
- **LangSmith** (only if tracing is enabled): traces of the agent runs above.
- **The lightwalletd server** (`ZCASH_LIGHTWALLETD_URL`, default `testnet.zec.rocks`): the scanner downloads compact
  blocks and full transactions from it. The server learns the operator's IP address and which transactions the scanner
  fetches, but not the viewing keys. Run your own lightwalletd for stronger privacy.
- **Zcash network:** shielded transactions, as described above.

## Custody

There is none. Zerveil:
- does not receive campaign money, so it cannot release, withhold or refund it;
- cannot pay creators on an advertiser's behalf: the advertiser's wallet signs every payment, after the advertiser
  approves the agreement in Zerveil and then sends the payment in their wallet;
- does not resolve payment disputes by moving funds. An underpaid or late payment is recorded, and the parties settle
  the difference between their own wallets.

The trade-off: there is no protection if a creator is paid and does not deliver. Milestones limit that risk: the fixed
fee is requested upfront and the performance fee only after the post is verified and its views counted at T+7d.

## Testnet and simulation

- **Testnet (TAZ) has no monetary value.** This build is restricted to testnet: `PAYMENT_MODE=zcash` refuses mainnet.
- Testnet TAZ comes from the faucet (`https://zcash-faucet.vercel.app/`). Users claim it themselves; Zerveil never
  claims faucet funds or sends donations.
- **Simulated mode** uses fake Unified Address strings and `sim…` transaction ids. Nothing is broadcast. Every
  invoice, receipt and deal paid this way is marked `simulated` in the API and UI. Zerveil never falls back to
  simulated mode silently.

## Retention and deletion

Records are kept in the operator's PostgreSQL database for as long as the operator keeps them. Records from the
earlier escrow version (archived deals and the `legacy_escrow_payouts` table) are kept read-only as history. Deleting a
record from Zerveil cannot remove a transaction from the blockchain.
