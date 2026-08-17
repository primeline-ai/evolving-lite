# Evolving Lite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-green.svg)](CHANGELOG.md)
[![Works with Claude Code](https://img.shields.io/badge/works%20with-Claude%20Code-orange.svg)](https://docs.anthropic.com/en/docs/claude-code)


![evolving-lite](assets/hero.png)

**Claude Code that learns from you.** Install once. Work normally. When you tell Claude it keeps making the same mistake, the system stores that correction, and can feed it back as context before a later tool call.

> "I corrected Claude about checking tsconfig strict mode first. A week later, different project, similar type error - Claude checked strict mode before I said anything. That's when I stopped thinking of it as a plugin."

## See It In Action

[![Evolving Lite Demo](https://img.youtube.com/vi/mR6Ss6Tnzm4/maxresdefault.jpg)](https://www.youtube.com/watch?v=mR6Ss6Tnzm4)

*2 min demo: Claude recalls a past decision and a correction is captured and fed back as context.*

## Self-Star Doctor (`/health`)

Evolving Lite ships its own install-time health assistant. The **Self-Star Doctor**
runs a synthetic pulse through every junction of the self-improving loop and prints a
green/yellow/red board:

```
 ✓ delegation       GREEN  synthetic prompt -> valid delegation decision row
 ✓ fitness          GREEN  event -> bounded cognitive-fitness score, read back
 ✓ autoevolve       GREEN  optimizer present, kill-switches OK, one scored cycle
 ✓ steward          GREEN  zero false findings on a clean repo
 ✓ verifier-spine   GREEN  EPT spine resolves; markerless claim blocked
 ✓ security         GREEN  tier-check classifies + scanner flags a planted secret
 ✓ kairn-link       GREEN  Kairn installed + reachable
```

It runs **once automatically** on your first session and is re-runnable any time with
`/health`. It heals conservatively (creates only missing empty scaffolding, asks before
touching `settings.json`, never overwrites or deletes) and runs its pulse in an isolated
scratch copy, so it never touches your real data.

## What Makes This Different

Most Claude Code tools add features. Evolving Lite adds **feedback loops**.

**It learns from corrections.** From session 3, say "You keep forgetting to check tsconfig first" and the detector recognises the repeat signal and stores it as an experience. From session 10, before Claude runs a Write, Edit, Bash or Task call, the system compares that call's input against your stored experiences and can add up to two matching ones to Claude's context. Matching needs at least two shared keywords, so it is a real filter, not a guarantee. The store lives with the plugin install, so projects sharing that install share it.

**It activates progressively.** Session 1: safety net only. Session 3: learning kicks in. Session 10: deep memory. You're never overwhelmed, and each tier earns trust before the next one activates.

**It heals itself.** Every hook writes a sentinel file proving it ran. Run the `health-monitor` agent to check all sentinels - silent failures don't stay silent.

**It gets leaner, not fatter.** Low-relevance experiences and stale sessions get archived. The system prunes itself so it stays fast at month 6 the same way it was at day 1.

## Self-Evolution is ON

Evolving Lite **self-tunes its own delegation config from your sessions** out of the
box. AutoEvolve watches the outcomes of delegation decisions and adjusts the routing
config when it sees a real, repeated signal. This is the headline feature - and it is
on by default - so here is exactly how it is kept safe and how to turn it off.

**Guardrails that ship with it (so a fresh install never tunes on noise):**
- It holds fire on thin data: no config mutation happens for a task type until it has
  crossed a minimum sample threshold (N=8 real outcomes). A day-1 install mutates nothing.
- An asymmetric trust model (lose-trust-fast) plus a floor prevents a single bad batch
  from swinging the config.
- A **no-regression guard**: a proposed mutation only persists if it scores at least as
  well as the current static baseline. Anything below baseline is automatically reverted
  and logged.
- It is **per-target**: only targets that empirically improve self-tune; the rest stay off.

**Turn it off in one step.** Set this in `_graph/cache/delegation-config.json`:

```json
"mutation_rules": { "v2_tuning_enabled": false }
```

That halts all config self-tuning immediately.

**Inspect / revert what it changed.** Every reverted mutation is recorded in
`_autoevolve/rejected/`. Applied changes live in `_graph/cache/delegation-config.json`
(plain JSON) - diff it against git to see exactly what moved, and revert any line by hand.

> Note: this switch controls **only** AutoEvolve's config self-tuning. The separate
> autonomy layer (the unsupervised `/autonom`-style loop with the EPT stop-gate) ships
> **off** by default and is a deliberate, documented opt-in.

## Honest Scope

Evolving Lite is a **reference implementation** of a complete self-improving agent loop -
it is heavy by design (delegation + fitness + autoevolve + steward + an EPT verifier-spine
+ a security tier system + a graph/memory substrate). **[Kairn](https://github.com/primeline-ai/kairn)
is a required prerequisite** (`pip install kairn-ai`) for the memory layer; the Self-Star
Doctor will tell you if it is missing. If you want lightweight session memory only, the
[Starter System](https://github.com/primeline-ai/claude-code-starter-system) is the smaller
entry point. See [docs/JUNCTIONS.md](docs/JUNCTIONS.md) for the file-level map of all 7 junctions.

One honesty note on the fitness signal: delegation outcomes are scored from what the loop can actually observe - dispatching a subagent counts as *neutral*, not positive (an action is not a success), and only the quality heuristic or a missed delegation moves a score. The signal measures routing discipline, not work quality.

## Quick Install

```bash
# 1. Clone into your skills directory - this is what makes it load
git clone https://github.com/primeline-ai/evolving-lite ~/.claude/skills/evolving-lite

# 2. Run setup (configures hook paths for your machine)
bash ~/.claude/skills/evolving-lite/setup.sh

# 3. Start Claude Code
claude
```

Three steps, no API key, no build step. Confirm it loaded with `claude plugin list`:

```
evolving-lite@skills-dir   Status: ✔ loaded
```

If `~/.claude/skills/` did not exist before step 1, restart Claude Code once so it
starts watching the new directory. If it already existed, the plugin is picked up in
the session you are in.

On first start:

```
Evolving Lite v1.1.0 | Session 1 | Tier 1 (Safety) | 20 experiences
```

### Requirements

- Claude Code 2.1.69+
- Python 3.10+
- Bash 3.2+ (macOS stock bash works)
- **On Windows: concurrent-write safety is not guaranteed.** `locked_json_rmw`
  uses `fcntl`, which is POSIX-only; on Windows it degrades to a lock-free
  fallback, so two hooks writing the same JSON file at the same moment can lose
  one of the writes. This is about concurrent *processes*, not about how many
  people or sessions are involved - a single Claude Code session fires many
  hooks as separate processes, so one session is enough to hit it. Sequential
  single-writer use is fine. A CI run did demonstrate the loss (49 of 50
  appends survived); that test is now skipped on Windows rather than passing by
  luck, so CI no longer checks this - the limitation stands until the module
  implements real Windows locking.
- **On Windows: Git Bash.** Every hook is registered in shell form starting with
  `bash`, and Claude Code only routes shell-form hooks through Git Bash when Git
  Bash is installed - otherwise it falls back to PowerShell, where `bash ...` is
  not a command and no hook runs. Git Bash ships with
  [Git for Windows](https://git-scm.com/download/win). Not verified on a Windows
  machine; the behaviour is taken from the Claude Code hooks documentation.

## What to Expect

**Day 1** - You install it and work normally. Context warnings appear at 70%. Dangerous bash commands get blocked. 20 pre-warmed experiences ship with the install, ready for recall once it reaches session 10. You barely notice it's there.

**Week 1 (Session 3+)** - The learning tier activates. When you tell Claude it keeps making the same mistake, the system captures the correction. Exploration tasks start routing to cheaper models automatically. Each session ends with an auto-generated summary of what happened and what's next.

**Month 1 (Session 10+)** - Deep memory kicks in. While Claude thinks, the system searches your stored experiences and can inject a matching one before you ask. Old data gets archived automatically. Knowledge survives context compaction. It feels less like a tool and more like a colleague who keeps notes.

## How It Works

### 4 Feedback Loops

The system runs on 4 loops that operate in the background. You don't invoke them - they fire on Claude Code events (session start, tool use, prompt submit, session end).

```
LEARN    You correct Claude → correction stored → can surface as context later
CONTEXT  Budget hits 70% → warning → at 93% knowledge is saved → session continues seamlessly
HEAL     Session starts → sentinel check → broken hook? → you see a warning immediately
EVOLVE   Usage tracked → stale data archived → routes refined → system stays lean
```

| Loop | Hooks | What changes |
|------|-------|-------------|
| **Learn** | correction-detector, thinking-recall | Your corrections come back to Claude as context |
| **Context** | context-warning, precompact-extract | Knowledge is saved before context compaction |
| **Heal** | health-sentinel, health-monitor agent | Silent hook failures get caught via sentinel checks |
| **Evolve** | usage-tracker, auto-archival | The system prunes itself and stays fast |

### Tiered Activation

All hooks are registered from day one but only fire when their tier is reached:

| Tier | Sessions | What activates | Purpose |
|------|----------|----------------|---------|
| **1 - Safety** | 1+ | context-warning, security-tier-check, health-sentinel, usage-tracker | Monitoring and protection |
| **2 - Learning** | 3+ | correction-detector, delegation-enforcer, session-summary | Learn from you, delegate smartly |
| **3 - Deep** | 10+ | thinking-recall, auto-archival, precompact-extract | Proactive memory, self-maintenance |

### Pre-warmed Experiences

Ships with 20 battle-tested experiences from real Claude Code workflows: debugging patterns, context management, session continuity, delegation strategies, and common gotchas. The store is populated on install rather than empty, so deep recall has something to match against as soon as it activates.

## Commands

All commands are optional. The system works fully automatically without using any of them.

| Command | What it does |
|---------|-------------|
| `/health` | Run the Self-Star Doctor - green/yellow/red board across all 7 junctions |
| `/debug` | 4-phase structured debugging (observe → hypothesize → test → fix) |
| `/plan-new` | Plan complex work with discovery phase and kill criteria |
| `/remember` | Explicitly save a learning, decision, or pattern to memory |
| `/whats-next` | Current project status and suggested next step |
| `/context-stats` | Context window usage with visual indicator |
| `/sparring` | Adversarial brainstorming - Claude takes the opposing position |
| `/think` | Apply thinking frameworks (80/20, First Principles, Inversion, SWOT) |
| `/evolution` | See what the system learned, archived, and optimized recently |
| `/evolving-update` | Check for updates and install them |
| `/review` | Structured code review with severity categorization |
| `/create-command` | Scaffold a new custom slash command |
| `/create-hook` | Scaffold a new automation hook |
| `/haiku` `/sonnet` `/opus` | Switch model tier |

## What's Inside

```
evolving-lite/
├── .claude-plugin/plugin.json      # Plugin manifest
├── commands/              (16)     # Slash commands
├── agents/                (6)      # Specialized sub-agents
│   ├── integrity-checker           # Cross-reference validation
│   ├── integrity-fixer             # Auto-repair inconsistencies
│   ├── health-monitor              # System diagnostics
│   ├── whats-next                  # Status and next steps
│   ├── planner                     # Plan review and hardening
│   └── autoevolve-optimizer        # Proposes and scores config mutations
├── skills/                (2)      # Auto-activating skills
│   ├── system-boot                 # Session startup context loading
│   └── evolution-guide             # System evolution transparency
├── hooks/
│   ├── hooks.json                  # All hook registrations
│   ├── scripts/           (18)     # Hook + self-* implementations (Python + Bash)
│   ├── scripts/lib/common.py       # Shared foundation (logging, sentinel, rate limiting)
│   └── security-tiers.json         # 10-tier bash command classification
├── knowledge/
│   ├── rules/             (5)      # Behavioral rules
│   └── patterns/          (12)     # Reusable reasoning patterns
├── _memory/                        # Your data (grows through usage)
│   ├── experiences/                # Learned patterns and solutions
│   │   └── _prewarmed/   (20)     # Starter experiences
│   ├── sessions/                   # Session summaries
│   ├── projects/                   # Project state
│   └── analytics/                  # Usage counters + evolution log
└── _graph/cache/                   # Routing and scoring configs
```

## The Ecosystem

Evolving Lite is the foundation. Each layer above is optional, free, and strengthens the one below.

```
                    Quantum Lens
                   (Deep Analysis)
                         │
                  PrimeLine Skills
               (Workflow Improvement)
                         │
            Universal Planning Framework
                  (Better Plans)
                         │
                       Kairn
               (Semantic Memory)
                         │
    ══════════ EVOLVING LITE ══════════
          (Self-Evolving Foundation)
```

| Tool | What it adds | Install |
|------|-------------|---------|
| [**Kairn**](https://github.com/kairn-ai/kairn) | Semantic memory search - "How did I solve the auth problem?" works even when you used different words | `pip install kairn-ai` |
| [**PrimeLine Skills**](https://github.com/primeline-ai/primeline-skills) | 5 workflow skills: debugging (ACH method), delegation scoring, TDD planning, code review, config architecture | `git clone` into `~/.claude/skills/` |
| [**UPF**](https://github.com/primeline-ai/universal-planning-framework) | 3-stage planning with adversarial hardening - 21 anti-patterns, 6 adversarial perspectives, kill criteria | `git clone` or `curl` one-liner |
| [**Quantum Lens**](https://github.com/primeline-ai/quantum-lens) | 7 cognitive lenses with anti-convergence - analysis that structurally can't groupthink | `git clone` into `~/.claude/skills/` |

**Without Kairn:** memory matching is keyword overlap - a stored note surfaces only when a tool call shares at least two of its keywords. **With Kairn:** semantic search, so a note can surface on meaning rather than exact wording, plus natural language search over the store.

Start without extras. Add what you need when you need it.

## Security & Privacy

- **All data stays local.** No network calls from any hook. Zero telemetry.
- **No settings.json modification.** Standard Claude Code plugin system - nothing injected.
- **10-tier bash security.** From blocking `rm -rf /` and reverse shells to logging `npm install -g`.
- **Sentinel verification.** Every hook writes proof it ran. Health check catches silent failures.
- **Credentials are redacted before anything is written.** Both hooks that persist your prompt run it through one shared pattern set first: cloud access keys, whole private-key blocks, JWTs, bearer and basic auth, connection strings with an inline password, vendor tokens (GitHub, Slack, Google, Stripe, Twilio, GitLab, npm, DigitalOcean, SendGrid, Shopify) and `api_key=` / `password=` / `AccountKey=` style assignments. **The known gap:** a bare high-entropy string with no keyword and no vendor prefix - a naked hex or base64 key on its own - is not matched, because catching it needs entropy guessing that would also flag ordinary text. Treat `_memory/` as you would any other local file.
- **Full transparency.** Run `/evolution` anytime. Read `_memory/` - it's all JSON and markdown.

## FAQ

<details>
<summary><strong>Does this slow down Claude Code?</strong></summary>

Every hook has a timeout - 10 seconds for most, 15 for the heavier session-start checks. If a hook takes longer, it's skipped rather than blocking you. Most complete in 20-50ms.
</details>

<details>
<summary><strong>What if Claude Code updates and breaks something?</strong></summary>

The health sentinel checks every hook at session start. If an update breaks a hook, you'll see a warning immediately. Run `/evolving-update` to get the fix.
</details>

<details>
<summary><strong>Can I use this alongside other plugins?</strong></summary>

Yes. Evolving Lite uses the standard plugin system. It doesn't interfere with other plugins or custom hooks - they run in parallel.
</details>

<details>
<summary><strong>What if the system learns something wrong?</strong></summary>

Delete the file from `_memory/experiences/` - that is the reliable way to remove it. Auto-archival only moves out experiences whose relevance score is below 30, and a wrongly-saved correction is stored at the detector's own confidence, so it will not fall out on its own.
</details>

<details>
<summary><strong>Does this work with Cursor or Windsurf?</strong></summary>

Evolving Lite is built for Claude Code (CLI). The hooks use Claude Code's plugin system. For other editors, look at [Kairn](https://github.com/kairn-ai/kairn) as a standalone MCP server - it works with any MCP-compatible client.
</details>

<details>
<summary><strong>How big does the data get?</strong></summary>

After a month: ~50-200 experience files (~0.2MB). Auto-archival cleans up continuously. After a year, typically under 50MB.
</details>

<details>
<summary><strong>Can I go back to vanilla Claude Code?</strong></summary>

Anytime. Delete `~/.claude/skills/evolving-lite`. Your experiences stay as JSON files inside that folder, so copy them out first if you want to keep them - keep them for later or delete the folder for clean removal.
</details>

## Upgrading from Starter System

If you're using [claude-code-starter-system](https://github.com/primeline-ai/claude-code-starter-system), Evolving Lite is the next step. Starter System gives you session memory and handoffs. Evolving Lite adds automated learning, self-healing, progressive activation, delegation, security, and 10 background hooks that make the system grow with you.

Your existing memory files are compatible - clone Evolving Lite into `~/.claude/skills/` and remove the Starter System folder.

## Uninstall

Delete `~/.claude/skills/evolving-lite`. Done. Your memory data lives inside that folder, so move it elsewhere first if you might come back.

## License

MIT - free to use, modify, and distribute.

## Credits

Built by [PrimeLine AI](https://primeline.cc). Extracted from [Evolving](https://primeline.cc/blog/knowledge-architecture), a production AI orchestration system with 130+ sessions and 6 months of daily use.

---

## Part of the PrimeLine Ecosystem

| Tool | What It Does | Deep Dive |
|------|-------------|-----------|
| [**Evolving Lite**](https://github.com/primeline-ai/evolving-lite) | Self-improving Claude Code plugin - memory, delegation, self-correction | [Blog](https://primeline.cc/blog/knowledge-architecture) |
| [**Kairn**](https://github.com/primeline-ai/kairn) | Persistent knowledge graph with context routing for AI | [Blog](https://primeline.cc/blog/knowledge-architecture) |
| [**tmux Orchestration**](https://github.com/primeline-ai/claude-tmux-orchestration) | Parallel Claude Code sessions with heartbeat monitoring | [Blog](https://primeline.cc/blog/tmux-orchestration) |
| [**UPF**](https://github.com/primeline-ai/universal-planning-framework) | 3-stage planning with adversarial hardening | [Blog](https://primeline.cc/blog/planning-framework-dsv-reasoning) |
| [**Quantum Lens**](https://github.com/primeline-ai/quantum-lens) | 7 cognitive lenses for multi-perspective analysis | [Blog](https://primeline.cc/blog/quantum-lens-multi-agent-analysis) |
| [**PrimeLine Skills**](https://github.com/primeline-ai/primeline-skills) | 5 production-grade workflow skills for Claude Code | [Blog](https://primeline.cc/blog/score-based-auto-delegation) |
| [**Starter System**](https://github.com/primeline-ai/claude-code-starter-system) | Lightweight session memory and handoffs | [Blog](https://primeline.cc/blog/session-management) |

**[@PrimeLineAI](https://x.com/PrimeLineAI)** · [primeline.cc](https://primeline.cc) · [Free Guide](https://primeline.cc/guide)