# Evolving Lite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](CHANGELOG.md)
[![Works with Claude Code](https://img.shields.io/badge/works%20with-Claude%20Code-orange.svg)](https://docs.anthropic.com/en/docs/claude-code)

**Claude Code that learns from you.** Install once. Work normally. The system remembers what worked, learns from your corrections, and gets better every session — without you lifting a finger.

> "I corrected Claude about checking tsconfig strict mode first. A week later, different project, similar type error — Claude checked strict mode before I said anything. That's when I stopped thinking of it as a plugin."

## See It In Action

[![Evolving Lite Demo](https://img.youtube.com/vi/mR6Ss6Tnzm4/maxresdefault.jpg)](https://www.youtube.com/watch?v=mR6Ss6Tnzm4)

*2 min demo: Claude recalls past decisions, learns from a correction, and applies it automatically — no prompt needed.*

## Audit + Prevention

Evolving Lite prevents drift in real-time. [Claude Health](https://github.com/tw93/claude-health) audits your full config on demand. Together they close the loop.

| | Evolving Lite | Claude Health |
|---|---|---|
| **When** | Every session (automatic) | On demand (`/health`) |
| **How** | Hooks, pulse checks, correction pipeline | 6-layer config audit with parallel diagnostics |
| **Catches** | Drift as it happens | Existing gaps and misconfigurations |

## What Makes This Different

Most Claude Code tools add features. Evolving Lite adds **feedback loops**.

**It learns from corrections.** Say "No, check tsconfig first" once. The system detects the correction, stores it, and recalls it automatically when a similar situation comes up — across sessions, across projects.

**It activates progressively.** Session 1: safety net only. Session 3: learning kicks in. Session 10: deep memory. You're never overwhelmed, and each tier earns trust before the next one activates.

**It heals itself.** Every hook writes a sentinel file proving it ran. Run the `health-monitor` agent to check all sentinels — silent failures don't stay silent.

**It gets leaner, not fatter.** Old experiences decay. Stale sessions get archived. The system prunes itself so it stays fast at month 6 the same way it was at day 1.

## Quick Install

```bash
# 1. Clone
git clone https://github.com/primeline-ai/evolving-lite ~/.claude-plugins/evolving-lite

# 2. Run setup (configures hook paths for your machine)
cd ~/.claude-plugins/evolving-lite && bash setup.sh

# 3. Register (add to ~/.claude/settings.json under "pluginDirectories")
```

```json
{
  "pluginDirectories": [
    "~/.claude-plugins/evolving-lite"
  ]
}
```

```bash
# 3. Start Claude Code
claude
```

On first start:

```
Evolving Lite v1.0 | Session 1 | Tier 1 (Safety) | 20 experiences loaded
The system learns from your corrections automatically.
```

### Requirements

- Claude Code 2.1.69+
- Python 3.10+
- Bash 3.2+ (macOS stock bash works)

## What to Expect

**Day 1** — You install it and work normally. Context warnings appear at 70%. Dangerous bash commands get blocked. 20 pre-warmed experiences provide Claude Code best practices from day one. You barely notice it's there.

**Week 1 (Session 3+)** — The learning tier activates. When you correct Claude, the system captures it. Exploration tasks start routing to cheaper models automatically. Each session ends with an auto-generated summary of what happened and what's next.

**Month 1 (Session 10+)** — Deep memory kicks in. While Claude thinks, the system searches your stored experiences and injects relevant solutions before you ask. Old data gets archived automatically. Knowledge survives context compaction. It feels less like a tool and more like a colleague who remembers everything.

## How It Works

### 4 Feedback Loops

The system runs on 4 loops that operate in the background. You don't invoke them — they fire on Claude Code events (session start, tool use, prompt submit, session end).

```
LEARN    You correct Claude → correction stored → recalled automatically next time
CONTEXT  Budget hits 70% → warning → at 93% knowledge is saved → session continues seamlessly
HEAL     Session starts → sentinel check → broken hook? → you see a warning immediately
EVOLVE   Usage tracked → stale data archived → routes refined → system stays lean
```

| Loop | Hooks | What changes |
|------|-------|-------------|
| **Learn** | correction-detector, thinking-recall | Claude stops repeating your corrections |
| **Context** | context-warning, precompact-extract | You never lose knowledge to context compaction |
| **Heal** | health-sentinel, health-monitor agent | Silent hook failures get caught via sentinel checks |
| **Evolve** | usage-tracker, auto-archival | The system prunes itself and stays fast |

### Tiered Activation

All hooks are registered from day one but only fire when their tier is reached:

| Tier | Sessions | What activates | Purpose |
|------|----------|----------------|---------|
| **1 — Safety** | 1+ | context-warning, security-tier-check, health-sentinel, usage-tracker | Monitoring and protection |
| **2 — Learning** | 3+ | correction-detector, delegation-enforcer, session-summary | Learn from you, delegate smartly |
| **3 — Deep** | 10+ | thinking-recall, auto-archival, precompact-extract | Proactive memory, self-maintenance |

### Pre-warmed Experiences

Ships with 20 battle-tested experiences from real Claude Code workflows: debugging patterns, context management, session continuity, delegation strategies, and common gotchas. The system feels informed from session 1 — not empty.

## Commands

All commands are optional. The system works fully automatically without using any of them.

| Command | What it does |
|---------|-------------|
| `/debug` | 4-phase structured debugging (observe → hypothesize → test → fix) |
| `/plan-new` | Plan complex work with discovery phase and kill criteria |
| `/remember` | Explicitly save a learning, decision, or pattern to memory |
| `/whats-next` | Current project status and suggested next step |
| `/context-stats` | Context window usage with visual indicator |
| `/sparring` | Adversarial brainstorming — Claude takes the opposing position |
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
├── commands/              (15)     # Slash commands
├── agents/                (5)      # Specialized sub-agents
│   ├── integrity-checker           # Cross-reference validation
│   ├── integrity-fixer             # Auto-repair inconsistencies
│   ├── health-monitor              # System diagnostics
│   ├── whats-next                  # Status and next steps
│   └── planner                     # Plan review and hardening
├── skills/                (2)      # Auto-activating skills
│   ├── system-boot                 # Session startup context loading
│   └── evolution-guide             # System evolution transparency
├── hooks/
│   ├── hooks.json                  # All hook registrations
│   ├── scripts/           (10)     # Hook implementations (Python + Bash)
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
| [**Kairn**](https://github.com/kairn-ai/kairn) | Semantic memory search — "How did I solve the auth problem?" works even when you used different words | `pip install kairn-ai` |
| [**PrimeLine Skills**](https://github.com/primeline-ai/primeline-skills) | 5 workflow skills: debugging (ACH method), delegation scoring, TDD planning, code review, config architecture | `git clone` + pluginDirectories |
| [**UPF**](https://github.com/primeline-ai/universal-planning-framework) | 3-stage planning with adversarial hardening — 21 anti-patterns, 6 adversarial perspectives, kill criteria | `git clone` or `curl` one-liner |
| [**Quantum Lens**](https://github.com/primeline-ai/quantum-lens) | 7 cognitive lenses with anti-convergence — analysis that structurally can't groupthink | `git clone` + pluginDirectories |

**Without Kairn:** keyword-based memory matching (~60% recall). **With Kairn:** semantic understanding (~90% recall), cross-project knowledge, natural language search.

Start without extras. Add what you need when you need it.

## Security & Privacy

- **All data stays local.** No network calls from any hook. Zero telemetry.
- **No settings.json modification.** Standard Claude Code plugin system — nothing injected.
- **10-tier bash security.** From blocking `rm -rf /` and reverse shells to logging `npm install -g`.
- **Sentinel verification.** Every hook writes proof it ran. Health check catches silent failures.
- **No secrets stored.** Content scanner skips API keys, passwords, and credentials.
- **Full transparency.** Run `/evolution` anytime. Read `_memory/` — it's all JSON and markdown.

## FAQ

<details>
<summary><strong>Does this slow down Claude Code?</strong></summary>

Every hook has a 10-second timeout. If a hook takes longer, it's skipped — never blocks. Most hooks complete in 20-50ms. You won't notice a difference.
</details>

<details>
<summary><strong>What if Claude Code updates and breaks something?</strong></summary>

The health sentinel checks every hook at session start. If an update breaks a hook, you'll see a warning immediately. Run `/evolving-update` to get the fix.
</details>

<details>
<summary><strong>Can I use this alongside other plugins?</strong></summary>

Yes. Evolving Lite uses the standard plugin system. It doesn't interfere with other plugins or custom hooks — they run in parallel.
</details>

<details>
<summary><strong>What if the system learns something wrong?</strong></summary>

Experiences have a confidence score that decays when recalled but ignored. Bad learnings die naturally. You can also delete any file in `_memory/experiences/` directly.
</details>

<details>
<summary><strong>Does this work with Cursor or Windsurf?</strong></summary>

Evolving Lite is built for Claude Code (CLI). The hooks use Claude Code's plugin system. For other editors, look at [Kairn](https://github.com/kairn-ai/kairn) as a standalone MCP server — it works with any MCP-compatible client.
</details>

<details>
<summary><strong>How big does the data get?</strong></summary>

After a month: ~50-200 experience files (~0.2MB). Auto-archival cleans up continuously. After a year, typically under 50MB.
</details>

<details>
<summary><strong>Can I go back to vanilla Claude Code?</strong></summary>

Anytime. Remove the line from `pluginDirectories` in settings.json. Your experiences stay as JSON files — keep them for later or delete the folder for clean removal.
</details>

## Upgrading from Starter System

If you're using [claude-code-starter-system](https://github.com/primeline-ai/claude-code-starter-system), Evolving Lite is the next step. Starter System gives you session memory and handoffs. Evolving Lite adds automated learning, self-healing, progressive activation, delegation, security, and 10 background hooks that make the system grow with you.

Your existing memory files are compatible — just point `pluginDirectories` to Evolving Lite instead.

## Uninstall

Remove the plugin path from `pluginDirectories` in `~/.claude/settings.json`. Done. Your memory data stays in the plugin directory — delete it for clean removal, or keep it if you might come back.

## License

MIT — free to use, modify, and distribute.

## Credits

Built by [PrimeLine AI](https://primeline.cc). Extracted from [Evolving](https://primeline.cc/blog/knowledge-architecture), a production AI orchestration system with 130+ sessions and 6 months of daily use.

---

## Part of the PrimeLine Ecosystem

| Tool | What It Does | Deep Dive |
|------|-------------|-----------|
| [**Evolving Lite**](https://github.com/primeline-ai/evolving-lite) | Self-improving Claude Code plugin — memory, delegation, self-correction | [Blog](https://primeline.cc/blog/knowledge-architecture) |
| [**Kairn**](https://github.com/primeline-ai/kairn) | Persistent knowledge graph with context routing for AI | [Blog](https://primeline.cc/blog/knowledge-architecture) |
| [**tmux Orchestration**](https://github.com/primeline-ai/claude-tmux-orchestration) | Parallel Claude Code sessions with heartbeat monitoring | [Blog](https://primeline.cc/blog/tmux-orchestration) |
| [**UPF**](https://github.com/primeline-ai/universal-planning-framework) | 3-stage planning with adversarial hardening | [Blog](https://primeline.cc/blog/planning-framework-dsv-reasoning) |
| [**Quantum Lens**](https://github.com/primeline-ai/quantum-lens) | 7 cognitive lenses for multi-perspective analysis | [Blog](https://primeline.cc/blog/quantum-lens-multi-agent-analysis) |
| [**PrimeLine Skills**](https://github.com/primeline-ai/primeline-skills) | 5 production-grade workflow skills for Claude Code | [Blog](https://primeline.cc/blog/score-based-auto-delegation) |
| [**Starter System**](https://github.com/primeline-ai/claude-code-starter-system) | Lightweight session memory and handoffs | [Blog](https://primeline.cc/blog/session-management) |

**[@PrimeLineAI](https://x.com/PrimeLineAI)** · [primeline.cc](https://primeline.cc) · [Free Guide](https://primeline.cc/guide)