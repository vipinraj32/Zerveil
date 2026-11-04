import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router";
import { LazyMotion, MotionConfig, domAnimation, m as motion, useMotionValueEvent, useReducedMotion, useScroll, useSpring } from "motion/react";
import { Logo } from "../../components/Logo";
import { centsToString, formatUsd } from "../../format";
import { useSession } from "../../session";
import { Cta, Icon, Status, StatusTag } from "./parts";
import "./landing.css";

const REPO = "https://github.com/vipinraj32/Zerveil";
const DOCS = `${REPO}#readme`;
const IMG = "/landing";
const EASE = [0.2, 0.7, 0.2, 1] as const;

const NAV = [
  { href: "#product", label: "Product" },
  { href: "#how", label: "How It Works" },
  { href: "#pay-math", label: "Payments" },
  { href: "#creators", label: "For Creators" },
  { href: "#faq", label: "FAQ" },
];

type Direction = "down" | "up";

/** Brand illustration (no screenshots): icon on a violet→cyan field with a few short lines. */
function Art({ icon, lines = [], label }: { icon: string; lines?: string[]; label?: string }) {
  return (
    <div className="lp-art" role="img" aria-label={label ?? lines.join(", ")}>
      <span className="lp-art-ic" aria-hidden>
        <Icon name={icon} size={56} />
      </span>
      {lines.length > 0 && (
        <ul aria-hidden>
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Which way the page is scrolling; sections enter from that side. */
const ScrollDirection = createContext<Direction>("down");

/** Props for an element that slides in from the scroll direction each time it enters the viewport. */
function revealProps(dir: Direction, delay = 0) {
  return {
    initial: "hidden",
    whileInView: "show",
    viewport: { once: false, amount: 0.2 },
    variants: {
      hidden: { opacity: 0, y: dir === "down" ? 48 : -48 },
      show: { opacity: 1, y: 0, transition: { duration: 0.6, delay, ease: EASE } },
    },
  } as const;
}

function useReveal() {
  const dir = useContext(ScrollDirection);
  return (delay = 0) => revealProps(dir, delay);
}

function Header({ direction, signedIn, advertiserStart, influencerStart }: { direction: Direction; signedIn: boolean; advertiserStart: string; influencerStart: string }) {
  const [open, setOpen] = useState(false);
  const [atTop, setAtTop] = useState(true);
  const [active, setActive] = useState("");
  const { scrollY, scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 220, damping: 32, restDelta: 0.001 });

  useMotionValueEvent(scrollY, "change", (current) => {
    setAtTop(current < 80);
    const line = window.innerHeight * 0.35;
    let found = "";
    for (const n of NAV) {
      const r = document.querySelector(n.href)?.getBoundingClientRect();
      if (r && r.top <= line && r.bottom > line) found = n.href;
    }
    setActive(found);
  });
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Hide while reading down the page, come back as soon as the visitor scrolls up.
  const hidden = direction === "down" && !atTop && !open;

  return (
    <motion.header className={`lp-header ${atTop ? "" : "lp-scrolled"}`} animate={{ y: hidden ? "-100%" : "0%" }} transition={{ duration: 0.35, ease: EASE }}>
      <div className="lp-wide lp-header-row">
        <a href="#top" className="lp-brand" aria-label="Zerveil home">
          <Logo size={34} />
        </a>
        <nav className={`lp-nav ${open ? "lp-open" : ""}`} id="lp-nav" aria-label="Main">
          <ul>
            {NAV.map((n) => (
              <li key={n.href}>
                <a href={n.href} className={active === n.href ? "lp-current" : ""} aria-current={active === n.href ? "location" : undefined} onClick={() => setOpen(false)}>
                  {n.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="lp-nav-actions">
            {!signedIn && (
              <Cta to={influencerStart} variant="outline">
                Join as a Creator
              </Cta>
            )}
            <Cta to={advertiserStart}>Launch a Campaign</Cta>
          </div>
        </nav>
        <button type="button" className="lp-menu-btn" aria-expanded={open} aria-controls="lp-nav" onClick={() => setOpen(!open)}>
          <Icon name={open ? "close" : "menu"} size={22} />
          <span className="lp-sr">{open ? "Close menu" : "Open menu"}</span>
        </button>
      </div>
      <motion.span className="lp-scrollbar" style={{ scaleX: progress }} aria-hidden />
    </motion.header>
  );
}

const SLIDES: { img?: string; icon?: string; lines?: string[]; alt: string; eyebrow: string; title: string; text: string; link: string }[] = [
  { icon: "shield", lines: ["Shielded payment request", "Fresh address of the creator's wallet", "Memo-matched payment"], alt: "Shielded Zcash payment flow", eyebrow: "Zcash testnet · open source", title: "Private payments for agent-made deals", text: "Agents agree the terms; the advertiser pays the creator's own wallet directly in shielded Zcash.", link: "#how" },
  { icon: "agents", lines: ["Offer · round 1", "Counter · round 1", "Accept · inside your limits"], alt: "Agents exchanging offers over A2A", eyebrow: "Agent negotiation", title: "Your agent talks to every creator’s agent", text: "Offers go out in parallel. Your limits decide every reply.", link: "#negotiate" },
  { img: "hero-proof.webp", alt: "Content agent report on brand mentions and logo visibility, sample data", eyebrow: "Content agent", title: "Every video is checked before the performance fee", text: "Mentions, tags and length are checked against your brief.", link: "#verify" },
  { icon: "key", lines: ["Fixed fee · when you accept", "Performance fee · day 7, from verified views", "Wallet to wallet · Zerveil holds nothing"], alt: "Direct shielded payments to creators", eyebrow: "Direct payments · testnet", title: "Wallet to wallet, confirmed on chain", text: "Fixed fee when you accept, performance fee from verified views at day 7 — each paid directly to the creator's wallet.", link: "#pay-math" },
];

const SLIDE_MS = 6000;

function HeroCarousel() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const touch = useRef<number | null>(null);
  const still = useReducedMotion();
  const n = SLIDES.length;
  const go = useCallback((d: number) => setI((v) => (v + d + n) % n), [n]);

  useEffect(() => {
    if (paused || still) return;
    const t = window.setTimeout(() => go(1), SLIDE_MS);
    return () => window.clearTimeout(t);
  }, [i, paused, go, still]);

  return (
    <div
      className={`lp-carousel ${paused || still ? "lp-paused" : ""}`}
      role="region"
      aria-roledescription="carousel"
      aria-label="Zerveil highlights"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") go(-1);
        if (e.key === "ArrowRight") go(1);
      }}
      onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
      onTouchEnd={(e) => {
        if (touch.current === null) return;
        const dx = e.changedTouches[0].clientX - touch.current;
        if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
        touch.current = null;
      }}
    >
      {SLIDES.map((s, k) => (
        <div key={s.title} className={`lp-slide ${k === i ? "lp-on" : ""}`} role="group" aria-roledescription="slide" aria-label={`${k + 1} of ${n}`} aria-hidden={k !== i}>
          {s.img ? <img src={`${IMG}/${s.img}`} alt={s.alt} loading={k === 0 ? "eager" : "lazy"} /> : <Art icon={s.icon ?? "shield"} lines={s.lines} label={s.alt} />}
          <div className="lp-slide-copy">
            <p className="lp-slide-eyebrow">{s.eyebrow}</p>
            <h2>{s.title}</h2>
            <p className="lp-slide-text">{s.text}</p>
            <a href={s.link} tabIndex={k === i ? 0 : -1}>
              Learn more
            </a>
          </div>
        </div>
      ))}
      <div className="lp-carousel-ui">
        <div className="lp-progress">
          {SLIDES.map((s, k) => (
            <button key={s.title} type="button" className={k === i ? "lp-on" : k < i ? "lp-done" : ""} onClick={() => setI(k)} aria-label={`Show slide ${k + 1}: ${s.eyebrow}`} aria-current={k === i}>
              <span key={k === i ? `on-${i}` : "off"} style={{ animationDuration: `${SLIDE_MS}ms` }} />
            </button>
          ))}
        </div>
        <div className="lp-arrows">
          <button type="button" onClick={() => go(-1)} aria-label="Previous slide">
            <Icon name="arrowLeft" size={20} />
          </button>
          <button type="button" onClick={() => go(1)} aria-label="Next slide">
            <Icon name="arrow" size={20} />
          </button>
        </div>
      </div>
      <p className="lp-sample-tag">Illustrative · testnet</p>
    </div>
  );
}

const STEPS = [
  { icon: "key", title: "Set your limits", detail: "Budget, max fee, max CPM, spending limits and the checks a video must pass.", points: ["Budget as a spending limit", "Fee and CPM ceilings", "Content checks"] },
  { icon: "agents", title: "Agents negotiate", detail: "Your agent sends offers to creator agents over A2A. Deterministic rules decide every reply.", points: ["Parallel offers", "Round limits", "Floors stay private"] },
  { icon: "check", title: "Policy checks", detail: "Agreed terms are re-checked by code, then you approve the deals together.", points: ["Hard limits in code", "One approval per campaign", "The LLM never pays"] },
  { icon: "shield", title: "Pay creators directly", detail: "Accepting requests each creator's fixed fee as a shielded payment to a fresh address of their own wallet. Pay from any wallet, one by one or all at once.", points: ["Amount and memo encrypted", "Confirmations required", "Zerveil holds no funds"] },
  { icon: "eye", title: "Proof, then performance", detail: "The post is verified; at day 7 the performance fee is computed from verified views and requested the same way.", points: ["YouTube checks", "View-only payment scanner", "Receipts for both sides"] },
];

function Steps() {
  const [cur, setCur] = useState(0);
  const [auto, setAuto] = useState(true);
  const [visible, setVisible] = useState(false);
  const still = useReducedMotion();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const reveal = useReveal();

  useEffect(() => {
    if (!auto || !visible || still) return;
    const t = window.setTimeout(() => setCur((c) => (c + 1) % STEPS.length), 4500);
    return () => window.clearTimeout(t);
  }, [cur, auto, visible, still]);

  const pick = (k: number, focus = false) => {
    setAuto(false);
    setCur(k);
    if (focus) tabs.current[k]?.focus();
  };
  const onKey = (e: ReactKeyboardEvent) => {
    const map: Record<string, number> = { ArrowRight: cur + 1, ArrowDown: cur + 1, ArrowLeft: cur - 1, ArrowUp: cur - 1, Home: 0, End: STEPS.length - 1 };
    if (!(e.key in map)) return;
    e.preventDefault();
    pick((map[e.key] + STEPS.length) % STEPS.length, true);
  };
  const s = STEPS[cur];
  const fill = cur / (STEPS.length - 1);

  return (
    <motion.div className="lp-stepper" {...reveal(0.1)} onViewportEnter={() => setVisible(true)} onViewportLeave={() => setVisible(false)}>
      <div className="lp-step-tabs" role="tablist" aria-label="Deal flow steps" onKeyDown={onKey}>
        <span className="lp-step-line" aria-hidden>
          <span style={{ transform: `scaleX(${fill})`, "--s": fill } as CSSProperties} />
        </span>
        {STEPS.map((st, k) => (
          <button
            key={st.title}
            ref={(el) => {
              tabs.current[k] = el;
            }}
            type="button"
            role="tab"
            id={`lp-step-${k}`}
            aria-selected={k === cur}
            aria-controls="lp-step-panel"
            tabIndex={k === cur ? 0 : -1}
            className={`lp-step-tab ${k === cur ? "lp-on" : ""} ${k < cur ? "lp-done" : ""}`}
            onClick={() => pick(k)}
          >
            <span className="lp-step-n">{String(k + 1).padStart(2, "0")}</span>
            <span className="lp-step-t">{st.title}</span>
          </button>
        ))}
      </div>
      <motion.div
        className="lp-step-panel"
        role="tabpanel"
        id="lp-step-panel"
        aria-labelledby={`lp-step-${cur}`}
        key={cur}
        initial={{ opacity: 0, x: 24 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.4, ease: EASE }}
      >
        <span className="lp-step-ic" aria-hidden>
          <Icon name={s.icon} size={30} />
        </span>
        <div>
          <h3>{s.title}</h3>
          <p>{s.detail}</p>
        </div>
        <ul>
          {s.points.map((p) => (
            <li key={p}>
              <Icon name="check" size={18} />
              {p}
            </li>
          ))}
        </ul>
        <div className="lp-step-nav">
          <button type="button" onClick={() => pick((cur - 1 + STEPS.length) % STEPS.length)} aria-label="Previous step">
            <Icon name="arrowLeft" size={20} />
          </button>
          <button type="button" onClick={() => pick((cur + 1) % STEPS.length)} aria-label="Next step">
            <Icon name="arrow" size={20} />
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

/** Same formula as agent_policy/pricing.py (hybrid): fixed + min(cap − fixed, views/1000 × CPM), rounded down to 0.01. */
function dealPay(fixedAmt: number, cpmAmt: number, capAmt: number, views: number) {
  const cents = (v: number) => Math.round(v * 100);
  const cap = cents(capAmt);
  const fixed = Math.min(cents(fixedAmt), cap);
  const variable = Math.floor((views * cents(cpmAmt)) / 1000);
  const performance = Math.max(0, Math.min(cap - fixed, variable));
  return { fixed, performance, unused: cap - fixed - performance, cap, capped: variable > performance };
}

/** Integer cents → "$40.00" (the app-wide USD formatter; no float formatting). */
const usdCents = (c: number) => formatUsd(centsToString(BigInt(c)));

type RangeProps = { id: string; label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format: (v: number) => string };

function Range({ id, label, value, min, max, step, onChange, format }: RangeProps) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="lp-range">
      <label htmlFor={id}>
        <span>{label}</span>
        <output htmlFor={id}>{format(value)}</output>
      </label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ "--p": `${pct}%` } as CSSProperties} />
    </div>
  );
}

function PaymentCalculator() {
  const [fixed, setFixed] = useState(19);
  const [cpm, setCpm] = useState(2.5);
  const [cap, setCap] = useState(40);
  const [views, setViews] = useState(8000);
  const reveal = useReveal();
  const r = dealPay(fixed, cpm, cap, views);
  const w = (c: number) => `${r.cap ? (c / r.cap) * 100 : 0}%`;

  return (
    <section className="lp-pay" id="pay-math" aria-labelledby="pay-math-title">
      <div className="lp-container">
        <motion.div className="lp-pay-head" {...reveal()}>
          <p className="lp-eyebrow">Payment math</p>
          <h2 id="pay-math-title">See what a deal pays</h2>
          <p>Move the sliders to see what a deal pays. Prices are in USD; on testnet each payment is sent in shielded Zcash at a test rate. The fixed fee is paid when you accept the deal, the per-1K-view fee after the measuring window — each straight from your wallet to the creator's. Anything under the cap is simply never requested.</p>
        </motion.div>
        <div className="lp-calc">
          <motion.div className="lp-calc-inputs" {...reveal(0.05)}>
            <Range
              id="calc-fixed"
              label="Fixed fee"
              value={fixed}
              min={0}
              max={200}
              step={1}
              format={(v) => formatUsd(v)}
              onChange={(v) => {
                setFixed(v);
                if (v > cap) setCap(Math.ceil(v / 5) * 5);
              }}
            />
            <Range id="calc-cpm" label="Per 1K views" value={cpm} min={0} max={10} step={0.1} format={(v) => formatUsd(v)} onChange={setCpm} />
            <Range
              id="calc-cap"
              label="Cap"
              value={cap}
              min={10}
              max={500}
              step={5}
              format={(v) => formatUsd(v)}
              onChange={(v) => {
                setCap(v);
                if (fixed > v) setFixed(v);
              }}
            />
            <Range id="calc-views" label="Verified views" value={views} min={0} max={100000} step={500} format={(v) => v.toLocaleString("en-US")} onChange={setViews} />
          </motion.div>
          <motion.div className="lp-calc-out" aria-live="polite" {...reveal(0.15)}>
            <p className="lp-calc-total">
              <span>Creator is paid</span>
              <strong>{usdCents(r.fixed + r.performance)}</strong>
              <small>of a {usdCents(r.cap)} cap, directly to their wallet</small>
            </p>
            <div className="lp-stack" role="img" aria-label={`Fixed ${usdCents(r.fixed)}, performance ${usdCents(r.performance)}, not requested ${usdCents(r.unused)}`}>
              <span className="lp-s-fixed" style={{ width: w(r.fixed) }} />
              <span className="lp-s-perf" style={{ width: w(r.performance) }} />
              <span className="lp-s-unused" style={{ width: w(r.unused) }} />
            </div>
            <dl className="lp-legend">
              <div>
                <dt>
                  <i className="lp-s-fixed" /> Fixed
                </dt>
                <dd>{usdCents(r.fixed)}</dd>
              </div>
              <div>
                <dt>
                  <i className="lp-s-perf" /> Performance
                </dt>
                <dd>{usdCents(r.performance)}</dd>
                {r.capped && <small>cap reached</small>}
              </div>
              <div>
                <dt>
                  <i className="lp-s-unused" /> Not requested
                </dt>
                <dd>{usdCents(r.unused)}</dd>
              </div>
            </dl>
            <p className="lp-calc-note">Example numbers · testnet payments have no monetary value</p>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

const FEATURES: { id: string; eyebrow: string; title: string; text: string; img?: string; icon?: string; lines?: string[]; alt: string; status: Status; note?: string }[] = [
  {
    id: "negotiate",
    eyebrow: "Negotiate",
    title: "Agents agree on the deal, inside your rules",
    text: "Your agent sends offers to creator agents over A2A, in parallel. A deterministic rules engine decides every accept or counter — the AI only suggests and explains.",
    icon: "agents",
    lines: ["Your limits: max fixed, max CPM, cap", "Policy decision: inside every limit", "Decided by rules · the LLM only explains"],
    alt: "A2A negotiation decided by your limits",
    status: "built",
  },
  {
    id: "verify",
    eyebrow: "Verify",
    title: "Know why a video passed — or didn’t",
    text: "The verifier checks the channel, publish window and disclosure through the YouTube Data API, and the content agent looks for brand mentions and your logo. Creators see what to fix and can resubmit.",
    img: "feat-verify.webp",
    alt: "Rejected proof with the reasons and fixes shown to the creator, sample data",
    status: "beta",
    note: "logo detection is advisory",
  },
  {
    id: "private",
    eyebrow: "Pay privately",
    title: "Shielded Zcash, wallet to wallet",
    text: "Advertisers pay each creator directly to a fresh shielded address of the creator's own wallet. Amounts, senders and memos are encrypted on-chain; a view-only scanner confirms receipt, and Zerveil never holds the money.",
    icon: "shield",
    lines: ["Payment request · fresh UA + memo", "Paid to the creator's own wallet", "Confirmed by a view-only scanner"],
    alt: "Direct shielded payments",
    status: "beta",
    note: "testnet; simulated mode by default",
  },
  {
    id: "connect",
    eyebrow: "Connect",
    title: "Built for other agents too",
    text: "Every creator agent publishes an A2A Agent Card, channel data is available as MCP tools, and paid APIs answer HTTP 402 with a shielded invoice your agent can pay from its prepaid API credit within your spending limits.",
    icon: "bot",
    lines: ["/.well-known/agent-card.json", "MCP tools: search, channel, videos", "HTTP 402 · X-Payment-Id"],
    alt: "Agent Card, MCP tools and HTTP 402",
    status: "built",
  },
];

/** Feature screenshot that tilts slightly toward the mouse pointer. */
function TiltFigure({ src, alt, icon, lines }: { src?: string; alt: string; icon?: string; lines?: string[] }) {
  const ref = useRef<HTMLElement>(null);
  const still = useReducedMotion();
  const move = (e: ReactPointerEvent) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse" || still) return;
    const b = el.getBoundingClientRect();
    el.style.setProperty("--rx", `${((e.clientY - b.top) / b.height - 0.5) * -6}deg`);
    el.style.setProperty("--ry", `${((e.clientX - b.left) / b.width - 0.5) * 8}deg`);
  };
  const reset = () => {
    ref.current?.style.setProperty("--rx", "0deg");
    ref.current?.style.setProperty("--ry", "0deg");
  };
  return (
    <figure className="lp-feature-img" ref={ref} onPointerMove={move} onPointerLeave={reset}>
      <div className="lp-tilt">
        {src ? <img src={src} alt={alt} loading="lazy" /> : <Art icon={icon ?? "shield"} lines={lines} label={alt} />}
      </div>
      <figcaption>{src ? "Sample data" : "Illustration"}</figcaption>
    </figure>
  );
}

function Features() {
  const reveal = useReveal();
  return (
    <section className="lp-band lp-features" id="product" aria-label="Product">
      <div className="lp-container">
        {FEATURES.map((f, k) => (
          <motion.article key={f.id} id={f.id} className={`lp-feature ${k % 2 ? "lp-flip" : ""}`} aria-labelledby={`${f.id}-title`} {...reveal()}>
            <div className="lp-feature-copy">
              <p className="lp-feature-eyebrow">
                {f.eyebrow} <StatusTag status={f.status} note={f.note} />
              </p>
              <h3 id={`${f.id}-title`}>{f.title}</h3>
              <p>{f.text}</p>
              {f.note && <p className="lp-note">Note: {f.note}.</p>}
              <a className="lp-more" href={DOCS} target="_blank" rel="noreferrer">
                Learn more <Icon name="arrow" size={16} />
              </a>
            </div>
            <TiltFigure src={f.img ? `${IMG}/${f.img}` : undefined} alt={f.alt} icon={f.icon} lines={f.lines} />
          </motion.article>
        ))}
      </div>
    </section>
  );
}

const FAQ = [
  { q: "Does real money move today?", a: "No. Zerveil runs on Zcash testnet, where test coins have no monetary value, and simulated payment mode is the default: in that mode nothing moves on-chain at all and every payment is labelled simulated." },
  { q: "Does Zerveil hold my money?", a: "No. Advertisers pay each creator directly, from their own wallet to a fresh address of the creator's own wallet. Zerveil only requests payments and confirms they arrived; it never holds, forwards or returns funds." },
  { q: "What can Zerveil see?", a: "Payments are shielded, so amounts, senders and memos are encrypted on-chain. Creators connect a viewing key of a dedicated receiving account: it lets Zerveil's scanner see that account's transactions to confirm payments, and it cannot spend. Creators' addresses are private to them and the operator." },
  { q: "Can the AI spend my budget?", a: "No. The LLM only proposes. Deterministic code checks every offer against your limits, you accept each campaign's deals, and you sign every creator payment yourself in your own wallet. Your agent can only spend prepaid API credit, within your spending limits." },
  { q: "Can advertisers see a creator’s floor?", a: "No. Rate cards never leave the creator’s side." },
];

function Faq() {
  const reveal = useReveal();
  return (
    <section className="lp-faq" id="faq" aria-labelledby="faq-title">
      <div className="lp-container lp-faq-grid">
        <motion.div {...reveal()}>
          <p className="lp-eyebrow">FAQ</p>
          <h2 id="faq-title">Straight answers</h2>
          <p>What Zerveil does today, and what is still testnet-only.</p>
          <a className="lp-more" href={DOCS} target="_blank" rel="noreferrer">
            Read the docs <Icon name="arrow" size={16} />
          </a>
        </motion.div>
        <div className="lp-faq-list">
          {FAQ.map((f, k) => (
            <motion.details key={f.q} open={k === 0} {...reveal(k * 0.06)}>
              <summary>
                {f.q}
                <span className="lp-plus" aria-hidden />
              </summary>
              <p>{f.a}</p>
            </motion.details>
          ))}
        </div>
      </div>
    </section>
  );
}

const RESOURCES = [
  {
    icon: "key",
    kind: "Guide",
    title: "How direct payments work",
    text: "When you accept a deal, the creator's fixed fee is requested as a shielded payment to their own wallet. The performance fee is computed from verified views seven days later and requested the same way. A view-only scanner confirms each payment and issues a receipt.",
  },
  {
    icon: "agents",
    kind: "Architecture",
    title: "Agent Cards and A2A",
    text: "Every creator agent publishes an Agent Card. Your agent finds them, sends offers in parallel, and a rules engine decides each reply — the AI only suggests.",
  },
  {
    icon: "eye",
    kind: "Product",
    title: "What the content agent checks",
    text: "Before the performance fee, the video is checked against your brief: channel, publish window, disclosure, required tags and brand mentions, with reasons a creator can fix.",
  },
];

function Resources() {
  const reveal = useReveal();
  return (
    <section className="lp-resources" id="resources" aria-labelledby="res-title">
      <div className="lp-container">
        <motion.div className="lp-res-head" {...reveal()}>
          <div>
            <h2 id="res-title">Under the hood</h2>
            <p>Guides to the agents, the rules and the payment flow, straight from the project documentation.</p>
            <Cta to={DOCS} size="lg">
              Read the docs
            </Cta>
          </div>
        </motion.div>
        <ul className="lp-res-grid">
          {RESOURCES.map((r, k) => (
            <motion.li key={r.title} {...reveal(k * 0.08)}>
              <a href={DOCS} target="_blank" rel="noreferrer" className="lp-res-card">
                <span className="lp-res-img">
                  <Art icon={r.icon} label="" />
                </span>
                <span className="lp-res-body">
                  <small>{r.kind}</small>
                  <strong>{r.title}</strong>
                  <span className="lp-res-text">{r.text}</span>
                  <em>
                    Read the guide <Icon name="arrow" size={16} />
                  </em>
                </span>
              </a>
            </motion.li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function BackToTop({ direction }: { direction: Direction }) {
  const { scrollY } = useScroll();
  const [far, setFar] = useState(false);
  useMotionValueEvent(scrollY, "change", (current) => setFar(current > window.innerHeight * 1.2));
  // Offer the shortcut when the visitor starts heading back up.
  const show = far && direction === "up";
  return (
    <a href="#top" className={`lp-top ${show ? "lp-on" : ""}`} aria-label="Back to top" tabIndex={show ? 0 : -1}>
      <Icon name="arrowUp" size={22} />
    </a>
  );
}

/** Hero spotlight and slider parallax: pointer position goes into CSS variables, no re-render. */
function trackPointer(e: ReactPointerEvent<HTMLElement>) {
  if (e.pointerType !== "mouse") return;
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  const y = (e.clientY - r.top) / r.height;
  el.style.setProperty("--mx", `${(x * 100).toFixed(1)}%`);
  el.style.setProperty("--my", `${(y * 100).toFixed(1)}%`);
  el.style.setProperty("--px", (x * 2 - 1).toFixed(3));
  el.style.setProperty("--py", (y * 2 - 1).toFixed(3));
  el.classList.add("lp-hover");
}

function resetPointer(e: ReactPointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  el.style.setProperty("--px", "0");
  el.style.setProperty("--py", "0");
  el.classList.remove("lp-hover");
}

export function Landing() {
  const { me } = useSession();
  const signedIn = !!me;
  const advertiserStart = signedIn ? "/app" : "/login?as=advertiser";
  const influencerStart = signedIn ? "/app" : "/login?as=influencer";

  const { scrollY } = useScroll();
  const [scrollDirection, setScrollDirection] = useState<Direction>("down");
  useMotionValueEvent(scrollY, "change", (current) => {
    const diff = current - (scrollY.getPrevious() ?? current);
    if (diff !== 0) setScrollDirection(diff > 0 ? "down" : "up");
  });

  useEffect(() => {
    document.title = "Zerveil — Private Payments. Autonomous Agents.";
  }, []);

  const reveal = (delay = 0) => revealProps(scrollDirection, delay);

  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
      <ScrollDirection.Provider value={scrollDirection}>
        <div className="lp" id="top">
          <a className="lp-skip" href="#main">
            Skip to content
          </a>
          <Header direction={scrollDirection} signedIn={signedIn} advertiserStart={advertiserStart} influencerStart={influencerStart} />

          <main id="main">
            {/* Hero */}
            <section className="lp-hero" aria-labelledby="hero-title" onPointerMove={trackPointer} onPointerLeave={resetPointer}>
              <motion.div className="lp-hero-copy" initial={{ opacity: 0, y: 32 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease: EASE }}>
                <h1 id="hero-title">
                  Private Payments. <span className="lp-hl">Autonomous Agents.</span>
                </h1>
                <p className="lp-hero-lead">
                  AI agents find creators and negotiate sponsorships within your limits. Advertisers pay creators directly in shielded Zcash, wallet to wallet; Zerveil never holds the money.
                </p>
                <div className="lp-cta-row">
                  <Cta to={advertiserStart} size="lg">
                    Launch a Campaign
                  </Cta>
                  <Cta to="#how" variant="outline" size="lg">
                    How It Works
                  </Cta>
                </div>
              </motion.div>
              <HeroCarousel />
            </section>

            {/* Core rule */}
            <section className="lp-rule" aria-labelledby="rule-title">
              <div className="lp-container">
                <motion.div className="lp-rule-card" {...reveal()}>
                  <p className="lp-rule-label">The core rule</p>
                  <div className="lp-rule-grid">
                    <div>
                      <blockquote>
                        <p id="rule-title">
                          The AI <em>proposes</em>. Rules <em>decide</em>. You <em>sign</em> — in your own wallet.
                        </p>
                      </blockquote>
                      <p className="lp-rule-sub">No payment ever depends on AI output alone. Signing is never an AI tool, and every creator payment is sent by the advertiser from their own wallet.</p>
                      <Cta to={DOCS} size="lg">
                        Read the design
                      </Cta>
                    </div>
                    <div className="lp-rule-art">
                      <Art icon="check" lines={["The AI proposes", "Deterministic rules decide", "You accept", "You pay from your own wallet"]} label="The AI proposes, deterministic rules decide, you accept, you pay from your own wallet" />
                    </div>
                  </div>
                </motion.div>
              </div>
            </section>

            {/* How it works */}
            <section className="lp-band lp-band-alt" id="how" aria-labelledby="how-title">
              <div className="lp-container">
                <motion.div className="lp-narrow" {...reveal()}>
                  <p className="lp-eyebrow">How it works</p>
                  <h2 id="how-title">Your terms. Your limits. Private payments.</h2>
                  <p>Agents do the back-and-forth, rules enforce your limits, you approve the deals, and shielded Zcash moves the money. Pick a step to see what happens.</p>
                </motion.div>
                <Steps />
              </div>
            </section>

            <PaymentCalculator />
            <Features />

            {/* For creators */}
            <section className="lp-band lp-band-tight" id="creators" aria-labelledby="creators-title">
              <div className="lp-container">
                <motion.div className="lp-split" {...reveal()}>
                  <Art icon="user" lines={["Private rate card · never shared", "Agent inbox · offers and counters", "Your own wallet · paid directly"]} label="Creator agent with a private rate card, paid directly to the creator's own wallet" />
                  <div className="lp-split-copy">
                    <p className="lp-eyebrow">For creators</p>
                    <p className="lp-split-quote" id="creators-title">
                      Your agent negotiates above your private floor.
                    </p>
                    <p className="lp-split-text">Set your rates once, add your shielded address and connect a viewing key of your receiving account. Your agent handles the offers; brands pay your own wallet directly.</p>
                    <ul className="lp-split-list">
                      <li>Niches from your channel</li>
                      <li>Rate card stays private</li>
                      <li>Fix and resubmit if rejected</li>
                      <li>Paid directly to your own wallet</li>
                    </ul>
                    <Cta to={influencerStart}>Join as a Creator</Cta>
                  </div>
                </motion.div>
              </div>
            </section>

            <Faq />
            <Resources />
          </main>

          <footer className="lp-footer">
            <div className="lp-wide">
              <div className="lp-footer-top">
                <Logo size={38} tone="dark" />
                <a className="lp-social" href={REPO} target="_blank" rel="noreferrer" aria-label="Zerveil on GitHub">
                  <Icon name="github" size={26} />
                </a>
              </div>
              <div className="lp-footer-cols">
                <nav aria-label="Product">
                  <h2>Product</h2>
                  <ul>
                    <li>
                      <a href="#negotiate">Negotiation</a>
                    </li>
                    <li>
                      <a href="#verify">Verification</a>
                    </li>
                    <li>
                      <a href="#pay-math">Payments</a>
                    </li>
                  </ul>
                </nav>
                <nav aria-label="Get started">
                  <h2>Get started</h2>
                  <ul>
                    <li>
                      <a href={advertiserStart}>Advertisers</a>
                    </li>
                    <li>
                      <a href={influencerStart}>Creators</a>
                    </li>
                    <li>
                      <a href="#faq">FAQ</a>
                    </li>
                    <li>
                      <Link to="/login">Log in</Link>
                    </li>
                  </ul>
                </nav>
                <nav aria-label="Project">
                  <h2>Project</h2>
                  <ul>
                    <li>
                      <a href={DOCS} target="_blank" rel="noreferrer">
                        Docs
                      </a>
                    </li>
                    <li>
                      <a href={REPO} target="_blank" rel="noreferrer">
                        GitHub
                      </a>
                    </li>
                    <li>
                      <span className="lp-soon">Privacy · soon</span>
                    </li>
                    <li>
                      <span className="lp-soon">Terms · soon</span>
                    </li>
                  </ul>
                </nav>
                <div className="lp-footer-cta">
                  <h2>Private by default</h2>
                  <p>Sign in with Google as an advertiser or a creator. Your agent is ready in minutes.</p>
                  <div className="lp-cta-row">
                    <Cta to={advertiserStart}>Get Started</Cta>
                    <Cta to={influencerStart} variant="outline">
                      I’m a creator
                    </Cta>
                  </div>
                </div>
              </div>
              <div className="lp-footer-base">
                <span>© {new Date().getFullYear()} Zerveil</span>
                <span>Zcash testnet · open source</span>
              </div>
            </div>
          </footer>
          <BackToTop direction={scrollDirection} />
        </div>
      </ScrollDirection.Provider>
      </MotionConfig>
    </LazyMotion>
  );
}
