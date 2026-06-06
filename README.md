# STRATUM — An Unnecessarily Complex String Transformation Pipeline

> "Any sufficiently advanced string reversal is indistinguishable from a distributed system."

STRATUM is a fully functional, maximally over-engineered text transformation engine. You give it a string and a transformation program written in its own DSL. It produces the transformed output. That is all it does.

---

## What It Does (Simply)

```
Input: "hello world"
Program: "upper | reverse | rot13"
Output: "QYEBJ BYYRU" ← wait, let me recalculate... "QYEBJ BYYRU"
```

You write transformation programs. STRATUM executes them. The result is text.

---

## What It Does (In Practice)

Your program is parsed by a **hand-written lexer and recursive-descent parser** into an **Abstract Syntax Tree**. The AST is walked by an **optimizer** that applies peephole and constant-folding passes. The optimized AST is **compiled** into bytecode for a **register-based virtual machine**. The VM executes the bytecode by dispatching to **plugins** loaded from a **plugin registry** that was populated via a **dependency injection container** at startup. Every state transition during execution is written as an **immutable event** to an **event store**, which an **event bus** broadcasts to any subscribed **projections**. One projection maintains a **queryable history** of all transformations ever run. Another maintains **aggregate statistics**. The final result is returned through a **result monad** and can be queried via a **local HTTP API** or the **CLI**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI / HTTP API                        │
│                  (two interchangeable frontends)             │
└────────────────────────┬────────────────────────────────────┘
                         │  Request object (not a string)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Pipeline Orchestrator                      │
│        (the only thing allowed to call other layers)         │
└──┬──────────┬────────────┬──────────────┬───────────────────┘
   │          │            │              │
   ▼          ▼            ▼              ▼
 Lexer     Parser       Optimizer      Compiler
   │          │            │              │
   └──────────┴────────────┴──────────────┘
                         │  Bytecode
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Virtual Machine                            │
│   (register-based, dispatches opcodes to plugin handlers)    │
└────────────────────────┬────────────────────────────────────┘
                         │  Events
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      Event Bus                               │
│      (pub/sub, all inter-component communication here)       │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
           ▼                              ▼
  ┌─────────────────┐          ┌──────────────────────┐
  │  Event Store    │          │     Projections       │
  │  (append-only)  │          │  - History Projection │
  └─────────────────┘          │  - Stats Projection   │
                               └──────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                   Plugin Registry                            │
│   (all transforms live here; none are hardcoded in the VM)  │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                DI Container                                  │
│    (wires everything together at startup)                    │
└─────────────────────────────────────────────────────────────┘
```

---

## The DSL

STRATUM programs are written in **Stratum Transformation Language (STL)**. STL supports:

- **Pipelines** — `upper | reverse | rot13`
- **Named arguments** — `pad(width=20, char=".")`
- **Branching** — `if length > 10 then truncate(10) else upper`
- **Variables** — `let x = length; repeat(x)`
- **Macros** — `macro shout = upper | trim | append("!!!")`
- **Inline lambdas** — `map(char => rot13(char))`
- **Comments** — `-- this does nothing useful`

---

## Hard Rules (Project Constitution)

These rules are inviolable and apply to every line of code in this project:

1. **No direct subsystem calls.** Components do not call each other directly. All cross-component communication happens through the event bus or via interfaces provided by the DI container. No exceptions.

2. **Design pattern coverage.** The codebase must implement at least one pattern from each GoF category:
   - *Creational:* Abstract Factory, Builder, Factory Method, Prototype, Singleton
   - *Structural:* Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
   - *Behavioral:* Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

3. **Configuration-driven behavior.** If a behavior can be expressed as a configuration value, it must be. No hardcoded transform names, opcode values, plugin paths, or limits in logic code.

4. **The DSL is real.** STL is parsed by a real lexer and real recursive-descent parser. No `eval`, no regex-replace "parsers", no `split("|")` shortcuts.

5. **Plugin system.** Every built-in transform is implemented as a plugin. The VM has no knowledge of what `upper` or `rot13` mean — it asks the plugin registry.

6. **Dependency injection everywhere.** No component constructs its own dependencies. Everything is wired by the DI container. Constructors declare what they need; the container provides it.

7. **Event sourcing.** The VM emits events for every state transition. Current state is always a projection of the event log, never stored directly.

8. **It must work.** All of this complexity must produce the correct output. Correctness is non-negotiable.

---

## Technology Stack

| Layer | Technology | Why (ostensibly) |
|---|---|---|
| Language | Python 3.12 | Rapid plugin development and metaclass abuse |
| Event bus | Custom (no external broker) | To make it more complex, obviously |
| Persistence | SQLite via raw SQL | No ORM — the query builder is hand-rolled |
| HTTP API | `http.server` (stdlib only) | To avoid the dependency being the interesting part |
| DI container | Custom | Because writing one is more complex than using one |
| Config | TOML | Parsed by a custom validator with a schema DSL |
| Testing | `pytest` | Some things should be sane |

**External dependencies are minimized by design.** The goal is internal complexity, not dependency complexity.

---

## Project Structure (Target)

```
stratum/
├── core/
│   ├── container.py        # DI container
│   ├── events.py           # Event bus, event store, base event types
│   ├── result.py           # Result monad (Ok/Err)
│   └── config.py           # Config loader + schema validator
├── lang/
│   ├── lexer.py            # Hand-written lexer
│   ├── parser.py           # Recursive-descent parser → AST
│   ├── ast_nodes.py        # AST node definitions
│   ├── optimizer.py        # AST optimization passes
│   └── compiler.py         # AST → bytecode compiler
├── vm/
│   ├── machine.py          # Register-based virtual machine
│   ├── opcodes.py          # Opcode definitions
│   └── frame.py            # Execution frame
├── plugins/
│   ├── registry.py         # Plugin registry
│   ├── base.py             # Plugin interface
│   └── builtins/           # All built-in transforms as plugins
│       ├── case.py         # upper, lower, title, swapcase
│       ├── cipher.py       # rot13, caesar, base64
│       ├── structure.py    # reverse, sort, unique, chunk
│       ├── padding.py      # pad, truncate, center
│       └── ...
├── projections/
│   ├── history.py          # Queryable transformation history
│   └── stats.py            # Aggregate stats
├── api/
│   ├── http_server.py      # HTTP API
│   └── cli.py              # CLI frontend
├── config/
│   └── stratum.toml        # Default configuration
└── tests/
    └── ...
```

---

## Getting Started (eventual)

```bash
# Run a transformation
python -m stratum "hello world" "upper | reverse"

# Start the HTTP API
python -m stratum serve

# Query history
python -m stratum history --last 10

# List available transforms
python -m stratum plugins list
```

---

## Why Does This Exist

Because software can be both completely unnecessary and completely correct at the same time, and there is value in exploring what happens when you take a trivial problem and refuse to treat it as one.

Also it is funny.
